"""Qué archivos escribe una orden de shell por las tres rutas que la guardia vigila.

La guardia (``guardia.py``) impide que las tools de edición escriban fuera del alcance de la orden
vigente. La shell es otra puerta, y no hay forma fiable de saber qué escribe un comando cualquiera.
Se portan los tres patrones que ya bloqueaba el kit viejo (``.spec/scripts/guard_bash_spec_writes.py``,
S-6) y solo esos:

1. ``sed -i`` (o ``--in-place``) sobre un archivo.
2. Una redirección que trunca (``>``, nunca ``>>``) hacia un archivo que **ya existe**.
3. Código inline (``-c``, ``-e`` o heredoc) pasado a un intérprete de una lista **cerrada** que nombra
   un archivo. Un intérprete fuera de la lista (``awk``, ``ruby``) no se bloquea: es un resultado
   documentado, no un hueco.

Este módulo solo lee la orden y dice qué rutas toca cada patrón; si esas rutas se pueden escribir lo
decide la guardia con las mismas reglas que para ``Edit`` y ``Write``. Lanzar un script versionado
(``python3 script.py``) no casa con ningún patrón.

No ejecuta nada ni abre archivos más allá de preguntar si una ruta existe, y se queda en la biblioteca
estándar: la guardia corre antes de cada comando y debe arrancar rápido.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

#: Intérpretes cuyo código inline vigila la regla 3 (lista cerrada). Se comparan sin la versión:
#: ``python3.12`` cuenta como ``python3``.
INTERPRETES = ("python3", "python", "bash", "sh", "zsh", "node", "perl")

#: Letras de las opciones que pasan código inline a cada intérprete (``-c``, ``-lc``, ``-e``, ``-pe``…).
_OPCIONES_INLINE = {"python": "c", "bash": "c", "sh": "c", "zsh": "c", "node": "ep", "perl": "eE"}
_OPCIONES_LARGAS_INLINE = ("--eval", "--print")

_PUNTUACION = ";&|()<>\n"
_SEPARADORES = ";&|()\n"
#: Una racha de signos del lexer (``;>``, ``&&\n``) se parte en sus operadores.
_OPERADORES = re.compile(r"&&|\|\||;;|\|&|&>>|&>|>>|>\||>&|<<<|<<-|<<|<&|<>|[;&|()<>\n]")
_ASIGNACION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PALABRAS_RESERVADAS = frozenset(
    {"{", "}", "!", "if", "then", "else", "elif", "while", "until", "do", "time"}
)
_ENVOLTORIOS = frozenset({"env", "sudo", "command", "exec", "nohup", "nice", "builtin"})
#: Redirecciones que truncan; ``>&`` solo si el destino no es un descriptor (``2>&1``).
_TRUNCANTES = frozenset({">", ">|", "&>"})
_APERTURA_HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(?:\"([^\"]+)\"|'([^']+)'|\\?([A-Za-z_][A-Za-z0-9_]*))")
_TEXTO_DE_RUTA = re.compile(r"[\w.@+~/-]+")
_EXTENSION = re.compile(r"\.[A-Za-z]\w*$")


@dataclass(frozen=True)
class Hallazgo:
    """Una ruta que toca un patrón: ``texto`` como está en la orden y la carpeta que la resuelve."""

    regla: int
    texto: str
    carpeta: Path


def hallazgos(orden: str, carpeta: Path) -> list[Hallazgo]:
    """Rutas que la orden escribe por los tres patrones, con ``carpeta`` como directorio de partida.

    Sigue los ``cd`` literales de la propia orden (``cd sub && sed -i …``), que cambian contra qué se
    resuelven las rutas relativas.
    """

    cuerpo, cuerpos_heredoc = _separar_heredocs(orden.replace("\\\n", " ").replace("`", ";"))
    encontrados: list[Hallazgo] = []
    actual = carpeta
    for segmento in _segmentos(_lexemas(cuerpo)):
        cabeza, resto = _cabeza(segmento)
        if not cabeza:
            continue
        nombre = _nombre(cabeza)
        argumentos, destinos_truncantes = _argumentos(resto)
        encontrados += [Hallazgo(2, d, actual) for d in destinos_truncantes if _existe(d, actual)]
        if nombre in ("cd", "pushd"):
            actual = _cambiar_carpeta(actual, argumentos)
        elif nombre == "sed":
            encontrados += [Hallazgo(1, f, actual) for f in _archivos_de_sed(argumentos)]
        elif nombre in _OPCIONES_INLINE:
            rutas = _rutas_de_codigo_inline(nombre, resto, cuerpos_heredoc, actual)
            encontrados += [Hallazgo(3, r, actual) for r in rutas]
    return _sin_repetidos(encontrados)


# --- lectura de la orden --------------------------------------------------------------------


def _separar_heredocs(orden: str) -> tuple[str, list[str]]:
    """La orden sin los cuerpos de heredoc (no son comandos) y esos cuerpos, que son código inline."""

    lineas: list[str] = []
    cuerpos: list[str] = []
    pendientes: list[tuple[str, list[str]]] = []  # (etiqueta de cierre, líneas del cuerpo)
    for linea in orden.split("\n"):
        if pendientes:
            etiqueta, acumulado = pendientes[0]
            if linea.strip() == etiqueta:
                cuerpos.append("\n".join(acumulado))
                pendientes.pop(0)
            else:
                acumulado.append(linea)
            continue
        lineas.append(linea)
        for apertura in _APERTURA_HEREDOC.finditer(linea):
            pendientes.append((apertura.group(1) or apertura.group(2) or apertura.group(3), []))
    # Un heredoc que no cierra (la orden termina antes) sigue siendo el código que recibe el intérprete.
    cuerpos += ["\n".join(acumulado) for _, acumulado in pendientes]
    return "\n".join(lineas), cuerpos


def _lexemas(texto: str) -> list[str]:
    """``shlex`` con los operadores de shell como lexemas aparte; sin comillas balanceadas, por espacios.

    Degradar en vez de fallar importa: una orden que no se puede leer no debe dejar pasar justo lo que
    los patrones buscan (``echo "it's" > archivo``).
    """

    lexer = shlex.shlex(texto, posix=True, punctuation_chars=_PUNTUACION)
    lexer.whitespace = " \t\r"  # el salto de línea separa órdenes: es un operador más
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        crudos = list(lexer)
    except ValueError:
        crudos = texto.split()
    lexemas: list[str] = []
    for lexema in crudos:
        if lexema and all(c in _PUNTUACION for c in lexema):
            lexemas += _OPERADORES.findall(lexema)
        else:
            lexemas.append(lexema)
    return lexemas


def _es_separador(lexema: str) -> bool:
    return bool(lexema) and all(c in _SEPARADORES for c in lexema)


def _es_redireccion(lexema: str) -> bool:
    return bool(lexema) and ("<" in lexema or ">" in lexema) and all(c in "<>&|" for c in lexema)


def _segmentos(lexemas: list[str]) -> list[list[str]]:
    """Cada comando de una línea compuesta por separado: ``cd x && sed -i …`` son dos."""

    segmentos: list[list[str]] = [[]]
    for lexema in lexemas:
        if _es_separador(lexema):
            segmentos.append([])
        else:
            segmentos[-1].append(lexema)
    return [s for s in segmentos if s]


def _cabeza(segmento: list[str]) -> tuple[str, list[str]]:
    """El comando del segmento y sus argumentos, sin asignaciones, palabras reservadas ni ``sudo``/``env``."""

    i = 0
    while i < len(segmento):
        lexema = segmento[i]
        if _ASIGNACION.match(lexema) or lexema in _PALABRAS_RESERVADAS:
            i += 1
        elif lexema in _ENVOLTORIOS:
            i += 1
            while i < len(segmento) and segmento[i].startswith("-"):
                i += 1
        else:
            return lexema, segmento[i + 1 :]
    return "", []


def _argumentos(resto: list[str]) -> tuple[list[str], list[str]]:
    """Los argumentos del comando sin sus redirecciones, y los destinos de las que truncan."""

    argumentos: list[str] = []
    truncantes: list[str] = []
    i = 0
    while i < len(resto):
        lexema = resto[i]
        if _es_redireccion(lexema):
            destino = resto[i + 1] if i + 1 < len(resto) else ""
            if lexema in _TRUNCANTES or (lexema == ">&" and not re.fullmatch(r"\d+|-", destino)):
                if destino:
                    truncantes.append(destino)
            # `<<<` y los heredocs no cambian los argumentos; el destino (o la etiqueta) tampoco cuenta.
            i += 2
            continue
        siguiente = resto[i + 1] if i + 1 < len(resto) else ""
        if lexema.isdigit() and _es_redireccion(siguiente):
            i += 1  # `2>` : el descriptor, no un argumento
            continue
        argumentos.append(lexema)
        i += 1
    return argumentos, truncantes


def _nombre(comando: str) -> str:
    """Nombre del ejecutable sin carpeta ni versión: ``/usr/bin/python3.12`` → ``python``."""

    base = comando.rsplit("/", 1)[-1]
    sin_version = re.sub(r"[\d.]+$", "", base)
    return sin_version if sin_version in _OPCIONES_INLINE or sin_version == "sed" else base


def _cambiar_carpeta(actual: Path, argumentos: list[str]) -> Path:
    destinos = [a for a in argumentos if not (a.startswith("-") and a != "-")]
    if not destinos or "$" in destinos[0] or destinos[0] == "-":
        return actual  # no se sabe adónde va: se sigue con la carpeta conocida
    return Path(os.path.normpath(actual / os.path.expanduser(destinos[0])))


def _sin_repetidos(encontrados: list[Hallazgo]) -> list[Hallazgo]:
    vistos: set[Hallazgo] = set()
    return [h for h in encontrados if not (h in vistos or vistos.add(h))]


def _existe(texto: str, carpeta: Path) -> bool:
    return bool(texto) and (carpeta / os.path.expanduser(texto)).exists()


# --- regla 1: sed -i ------------------------------------------------------------------------


def _archivos_de_sed(argumentos: list[str]) -> list[str]:
    """Los archivos de un ``sed`` en el sitio; ninguno si no edita en el sitio.

    El primer argumento que no es opción es el guion, salvo que ``-e``/``-f`` ya lo hayan dado.
    """

    en_el_sitio = False
    tiene_guion = False
    posicionales: list[str] = []
    i = 0
    while i < len(argumentos):
        a = argumentos[i]
        i += 1
        if a == "--":
            posicionales += argumentos[i:]
            break
        if a.startswith("--"):
            nombre, _, valor = a.partition("=")
            if nombre == "--in-place":
                en_el_sitio = True
            elif nombre in ("--expression", "--file"):
                tiene_guion = True
                if not valor:
                    i += 1
            continue
        if a.startswith("-") and len(a) > 1:
            for j, letra in enumerate(a[1:], start=1):
                if letra == "i":
                    en_el_sitio = True
                    break  # el resto es el sufijo de la copia
                if letra in "ef":
                    tiene_guion = True
                    if j == len(a) - 1:
                        i += 1  # el guion o el archivo viene en el argumento siguiente
                    break
            continue
        if a == "" and en_el_sitio:
            continue  # `sed -i '' …` (BSD): el sufijo vacío
        posicionales.append(a)
    if not en_el_sitio:
        return []
    return posicionales if tiene_guion else posicionales[1:]


# --- regla 3: código inline en un intérprete --------------------------------------------------


def _es_opcion_inline(interprete: str, lexema: str) -> bool:
    if lexema in _OPCIONES_LARGAS_INLINE:
        return interprete == "node"
    letras = _OPCIONES_INLINE[interprete]
    return bool(re.fullmatch(r"-[A-Za-z]+", lexema)) and lexema[-1] in letras


def _rutas_de_codigo_inline(
    interprete: str, resto: list[str], cuerpos_heredoc: list[str], carpeta: Path
) -> list[str]:
    """Rutas que nombra el código que recibe el intérprete por ``-c``/``-e``, un heredoc o ``<<<``."""

    textos: list[str] = []
    en_codigo = False
    hay_heredoc = False
    i = 0
    while i < len(resto):
        lexema = resto[i]
        if lexema in ("<<", "<<-"):
            hay_heredoc = True
            i += 2  # la etiqueta no es código
            continue
        if lexema == "<<<":
            textos += resto[i + 1 : i + 2]
            i += 2
            continue
        if _es_redireccion(lexema):
            i += 2
            continue
        if _es_opcion_inline(interprete, lexema):
            en_codigo = True
        elif en_codigo:
            textos.append(lexema)  # el código y los archivos que siguen (`perl -pi -e '…' archivo`)
        i += 1
    if hay_heredoc:
        textos += cuerpos_heredoc
    candidatos: list[str] = []
    for texto in textos:
        candidatos += [c for c in _TEXTO_DE_RUTA.findall(texto) if _parece_ruta(c, carpeta)]
    return candidatos


def _parece_ruta(candidato: str, carpeta: Path) -> bool:
    """Si un trozo de código parece nombrar un archivo y no una división, un módulo o una ruta de URL.

    Con carpeta (``a/b.py``) pasa si es absoluto, relativo explícito o acaba en extensión; sin carpeta
    (``calc.py``) solo si el archivo existe. Así ``1/2``, ``and/or`` o ``os.path`` no cuentan.
    """

    if not re.search(r"[A-Za-z]", candidato):
        return False
    if "/" in candidato:
        return candidato.startswith(("/", "./", "../", "~/")) or bool(_EXTENSION.search(candidato))
    return (carpeta / candidato).is_file()
