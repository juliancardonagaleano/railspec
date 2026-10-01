"""Normalización y huellas para el gate de salida del chat (reglas 2 y 3).

Dos representaciones de un texto, ambas sin modelo de por medio:

- **Tokens**: NFKC, camelCase partido, minúsculas, y solo las secuencias de
  letras y dígitos (``calcular_total(pedido)`` y "calcular total pedido" dan
  los mismos tokens). La regla ``huella-contexto`` compara n-gramas de N
  tokens de la respuesta contra los de cada fragmento que el agente leyó.
- **Aplastado**: además, confusibles Unicode a latín, caracteres invisibles
  fuera, y todo lo que no sea letra o dígito eliminado. Sobre eso se
  calculan huellas de k-gramas de caracteres con winnowing. La regla
  ``normalizacion`` compara las variantes desofuscadas de la respuesta
  (aplastada, invertida, ROT13, base64, hex, URL y escapes decodificados)
  contra esas huellas, de modo que separar letras, intercalar espacios o
  codificar no esquiva la comparación.

Las huellas son hashes de 64 bits en hexadecimal: no permiten reconstruir el
texto y solo viven mientras dura la conversación.
"""

from __future__ import annotations

import base64
import binascii
import codecs
import hashlib
import re
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from urllib.parse import unquote

# --- Tokens ---------------------------------------------------------------------------

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_PALABRA = re.compile(r"[^\W_]+")
_IDENTIFICADOR = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def tokens(texto: str) -> list[str]:
    """Secuencia de palabras normalizadas: la unidad de comparación de la regla 2."""

    texto = unicodedata.normalize("NFKC", texto)
    texto = _CAMEL.sub(" ", texto)
    return _PALABRA.findall(texto.casefold())


def _hash(texto: str) -> str:
    return hashlib.blake2b(texto.encode(), digest_size=8).hexdigest()


def ngramas(lista: list[str], n: int) -> Iterator[str]:
    for i in range(len(lista) - n + 1):
        yield _hash("\x1f".join(lista[i : i + n]))


# --- Aplastado y desofuscación ---------------------------------------------------------

#: Letras cirílicas y griegas que se confunden con latinas (minúsculas tras casefold).
_CONFUSIBLES = str.maketrans(
    {
        "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p",
        "с": "c", "т": "t", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "ɡ": "g",
        "α": "a", "β": "b", "ε": "e", "η": "n", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p",
        "τ": "t", "υ": "u", "χ": "x", "ω": "w", "ϲ": "c", "ℓ": "l", "ı": "i",
    }
)  # fmt: skip
_INVISIBLES = re.compile(
    "[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180e\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]"
)
_NO_ALNUM = re.compile(r"[\W_]+")


def aplastar(texto: str) -> str:
    texto = unicodedata.normalize("NFKC", _INVISIBLES.sub("", texto)).casefold()
    texto = texto.translate(_CONFUSIBLES)
    # Quita diacríticos combinantes (p̲r̲i̲n̲t̲, letras con marcas añadidas).
    texto = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return _NO_ALNUM.sub("", texto)


_B64 = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")
_HEX = re.compile(r"(?:(?:\\x|0x)?[0-9a-fA-F]{2}[\s,:]?){8,}")
_PORCIENTO = re.compile(r"%[0-9a-fA-F]{2}")
_ESCAPE_U = re.compile(r"(?:\\u[0-9a-fA-F]{4}|\\x[0-9a-fA-F]{2}|&#x?[0-9a-fA-F]+;){4,}")


def _imprimible(texto: str) -> bool:
    if not texto:
        return False
    buenos = sum(c.isprintable() or c in "\n\t\r" for c in texto)
    return buenos / len(texto) >= 0.9


def _decodificados(texto: str) -> Iterator[str]:
    for m in _B64.finditer(texto):
        bloque = m.group(0)
        for decodificador in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                crudo = decodificador(bloque + "=" * (-len(bloque) % 4))
                plano = crudo.decode("utf-8")
            except (binascii.Error, ValueError, UnicodeDecodeError):
                continue
            if _imprimible(plano):
                yield plano
                break
    for m in _HEX.finditer(texto):
        digitos = re.sub(r"\\x|0x|[\s,:]", "", m.group(0))
        try:
            plano = bytes.fromhex(digitos[: len(digitos) // 2 * 2]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if _imprimible(plano):
            yield plano
    if len(_PORCIENTO.findall(texto)) >= 3:
        yield unquote(texto)
    for m in _ESCAPE_U.finditer(texto):
        bloque = m.group(0)
        try:
            if "&#" in bloque:
                import html

                yield html.unescape(bloque)
            else:
                yield codecs.decode(bloque, "unicode_escape")
        except (UnicodeDecodeError, ValueError):
            continue


def variantes(texto: str, profundidad: int = 2) -> list[str]:
    """Variantes desofuscadas del texto (sin el propio texto), en crudo y sin aplastar.

    Incluye el texto invertido, ROT13 y lo que se decodifique de base64, hex,
    URL encoding y escapes, recursivamente hasta ``profundidad`` niveles.
    """

    vistas: set[str] = {texto}
    salida: list[str] = []
    pendientes = [(texto, 0)]
    while pendientes:
        actual, nivel = pendientes.pop()
        nuevas = [actual[::-1], codecs.encode(actual, "rot13")]
        if nivel < profundidad:
            nuevas.extend(_decodificados(actual))
        for v in nuevas:
            if v and v not in vistas:
                vistas.add(v)
                salida.append(v)
                if nivel + 1 <= profundidad and len(salida) < 64:
                    pendientes.append((v, nivel + 1))
    return salida


def winnowing(texto_aplastado: str, k: int, ventana: int) -> set[str]:
    """Huellas de k-gramas de caracteres seleccionadas por winnowing.

    Garantía clásica: toda subcadena común de al menos ``k + ventana - 1``
    caracteres comparte al menos una huella.
    """

    if len(texto_aplastado) < k:
        return {_hash(texto_aplastado)} if texto_aplastado else set()
    hashes = [_hash(texto_aplastado[i : i + k]) for i in range(len(texto_aplastado) - k + 1)]
    if len(hashes) <= ventana:
        return {min(hashes)}
    elegidas: set[str] = set()
    for i in range(len(hashes) - ventana + 1):
        elegidas.add(min(hashes[i : i + ventana]))
    return elegidas


def kgramas(texto_aplastado: str, k: int) -> set[str]:
    """Todos los k-gramas (lado de la respuesta: la consulta no se winnowea)."""

    return {_hash(texto_aplastado[i : i + k]) for i in range(max(0, len(texto_aplastado) - k + 1))}


# --- Parámetros por nivel -------------------------------------------------------------------


@dataclass(frozen=True)
class Parametros:
    """Umbrales derivados de ``huella_tokens_n`` de la política del vínculo."""

    n_tokens: int

    @property
    def caracteres_min(self) -> int:
        """Subcadena aplastada compartida que bloquea en la regla 3 (≈ 4 caracteres por token)."""

        return 4 * self.n_tokens

    @property
    def ventana(self) -> int:
        return 8

    @property
    def k(self) -> int:
        return self.caracteres_min - self.ventana + 1


# --- Huellas de lo que el agente leyó ------------------------------------------------------


@dataclass
class HuellasContexto:
    """Huellas de los fragmentos de código leídos en una conversación (nunca el texto)."""

    tokens: set[str] = field(default_factory=set)
    caracteres: set[str] = field(default_factory=set)
    identificadores: set[str] = field(default_factory=set)

    def agregar(self, texto: str, p: Parametros) -> None:
        self.tokens |= set(ngramas(tokens(texto), p.n_tokens))
        self.caracteres |= winnowing(aplastar(texto), p.k, p.ventana)
        self.identificadores |= {hash_identificador(i) for i in _IDENTIFICADOR.findall(texto)}

    def unir(self, otras: Iterable[HuellasContexto]) -> HuellasContexto:
        for o in otras:
            self.tokens |= o.tokens
            self.caracteres |= o.caracteres
            self.identificadores |= o.identificadores
        return self

    @property
    def vacio(self) -> bool:
        return not (self.tokens or self.caracteres)


def hash_identificador(nombre: str) -> str:
    return _hash("id:" + nombre.casefold())


def identificadores(texto: str) -> list[str]:
    return _IDENTIFICADOR.findall(texto)
