"""Codificador local de embeddings: opcional, sin red al buscar y sin ningún dato fuera de la máquina.

La búsqueda de texto (``busqueda.py``) funciona sin modelo. Si hay un codificador, el mismo índice
guarda además un vector por símbolo y ``code_search`` mezcla BM25 con similitud. Nada de esto viaja:
ni el texto de los símbolos ni sus vectores salen del clon.

Un codificador es un directorio con un modelo ONNX, su ``tokenizer.json`` y un ``codificador.json``
que dice cómo usarlos; el que no está instalado simplemente no existe y la búsqueda sigue por palabras.
``railspec modelo instalar`` baja el modelo predeterminado con versión y sha256 fijos (no ejecuta
código del repositorio del modelo, solo lee pesos y un tokenizador). Dependencias opcionales:
``pip install "railspec-local[embeddings]"`` (onnxruntime, tokenizers, numpy).

Formato de ``codificador.json``::

    {"nombre": "jina-v2-base-code", "dimensiones": 768, "onnx": "onnx/model_quantized.onnx",
     "tokenizer": "tokenizer.json", "salida": "last_hidden_state", "pooling": "mean",
     "prefijo_consulta": "", "max_tokens": 256}

``pooling`` es ``mean`` (promedio de ``salida`` con la máscara) o ``directo`` (``salida`` ya es el
vector de la oración). Los vectores se normalizan y se guardan en int8.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .errores import ErrorRailspec

ENV_MODELO = "RAILSPEC_MODELO_EMBEDDINGS"
MANIFIESTO = "codificador.json"
PREDETERMINADO = "jina-v2-base-code"
LOTE = 16
#: Techo de símbolos que codifica un ``unit_report``; el resto espera a ``railspec indice --vectores``.
POR_REPORTE = 64


class CodificadorNoDisponible(ErrorRailspec):
    """Faltan las dependencias opcionales o el modelo; el mensaje dice cómo instalarlos."""


class Codificador(Protocol):
    nombre: str
    dimensiones: int

    def codificar(self, textos: list[str]) -> Any:
        """Matriz ``len(textos) × dimensiones`` de float32 normalizados (documentos)."""

    def codificar_consulta(self, texto: str) -> Any:
        """Vector float32 normalizado de una consulta (con el prefijo del modelo, si lo hay)."""


@dataclass(frozen=True)
class Modelo:
    """Un modelo descargable con versión y hashes fijos."""

    nombre: str
    repositorio: str
    revision: str
    archivos: dict[str, str]  # ruta en el repositorio → sha256
    manifiesto: dict[str, Any] = field(default_factory=dict)
    licencia: str = ""


MODELOS: dict[str, Modelo] = {
    PREDETERMINADO: Modelo(
        nombre=PREDETERMINADO,
        repositorio="jinaai/jina-embeddings-v2-base-code",
        revision="516f4baf13dec4ddddda8631e019b5737c8bc250",
        archivos={
            "onnx/model_quantized.onnx": "ed45870251c9f0cf656e78aab0d37a23489066df8a222bb1c8caf8a45f2cb16d",
            "tokenizer.json": "b01c78a902aa4facb2f47f95449f48e2f7bbfea5d2472ee2f6ce92323c6f86e5",
        },
        manifiesto={
            "dimensiones": 768,
            "onnx": "onnx/model_quantized.onnx",
            "tokenizer": "tokenizer.json",
            "salida": "last_hidden_state",
            "pooling": "mean",
            "prefijo_consulta": "",
            "max_tokens": 256,
        },
        licencia="Apache-2.0",
    )
}


def directorio_modelos() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or "")
    if not base.is_absolute():  # la especificación XDG manda ignorar una ruta relativa
        base = Path.home() / ".local" / "share"
    return base / "railspec" / "modelos"


def directorio_de(nombre: str = PREDETERMINADO) -> Path:
    """Dónde se instala ``nombre``; ``RAILSPEC_MODELO_EMBEDDINGS`` apunta a otro directorio ya listo."""

    explicito = os.environ.get(ENV_MODELO)
    return Path(explicito).expanduser() if explicito else directorio_modelos() / nombre


# --- instalación --------------------------------------------------------------------------------


def _descargar(url: str, destino: Path, esperado: str, avance: Callable[[str], None]) -> None:
    temporal = destino.with_name(destino.name + ".parcial")
    destino.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, temporal.open("wb") as f:  # noqa: S310 - https fijo
            total = int(resp.headers.get("Content-Length") or 0)
            hecho = 0
            while bloque := resp.read(1 << 20):
                f.write(bloque)
                h.update(bloque)
                hecho += len(bloque)
                if total and (hecho // (1 << 20)) % 25 == 0:
                    avance(f"  {destino.name}: {hecho >> 20} de {total >> 20} MB")
        if h.hexdigest() != esperado:
            raise CodificadorNoDisponible(
                f"El sha256 de {destino.name} no es el esperado ({h.hexdigest()[:16]}… en vez de "
                f"{esperado[:16]}…); no se instala."
            )
        temporal.replace(destino)
    except OSError as exc:
        raise CodificadorNoDisponible(f"No se pudo descargar {url}: {exc}") from exc
    finally:
        temporal.unlink(missing_ok=True)


def instalar(nombre: str = PREDETERMINADO, avance: Callable[[str], None] = print) -> Path:
    """Baja los archivos del modelo ``nombre`` (con hash verificado) y escribe su manifiesto."""

    if nombre not in MODELOS:
        raise CodificadorNoDisponible(
            f"No conozco el modelo «{nombre}». Disponibles: {', '.join(sorted(MODELOS))}. "
            f"Para otro, prepara un directorio con {MANIFIESTO} y apunta {ENV_MODELO} a él."
        )
    modelo = MODELOS[nombre]
    destino = directorio_modelos() / nombre
    for ruta, sha in modelo.archivos.items():
        archivo = destino / ruta
        if archivo.is_file() and hashlib.sha256(archivo.read_bytes()).hexdigest() == sha:
            continue
        avance(f"Descargando {modelo.repositorio}/{ruta}")
        _descargar(
            f"https://huggingface.co/{modelo.repositorio}/resolve/{modelo.revision}/{ruta}",
            archivo,
            sha,
            avance,
        )
    (destino / MANIFIESTO).write_text(
        json.dumps({"nombre": nombre, **modelo.manifiesto}, indent=2) + "\n", encoding="utf-8"
    )
    return destino


# --- ONNX -------------------------------------------------------------------------------------------


class CodificadorOnnx:
    def __init__(self, directorio: Path) -> None:
        try:
            manifiesto = json.loads((directorio / MANIFIESTO).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CodificadorNoDisponible(
                f"No hay un modelo de embeddings en {directorio} (falta {MANIFIESTO}). "
                "Instálalo con `railspec modelo instalar`."
            ) from exc
        try:
            import numpy  # noqa: F401
            import onnxruntime
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise CodificadorNoDisponible(
                "Faltan las dependencias del codificador local; instálalas con "
                '`pip install "railspec-local[embeddings]"`. Mientras tanto la búsqueda sigue por palabras.'
            ) from exc
        try:
            self.nombre: str = str(manifiesto["nombre"])
            self.dimensiones: int = int(manifiesto["dimensiones"])
            ruta_onnx = directorio / manifiesto["onnx"]
        except (KeyError, TypeError, ValueError) as exc:
            raise CodificadorNoDisponible(
                f"El {MANIFIESTO} de {directorio} está incompleto (nombre, dimensiones y onnx): {exc!r}."
            ) from exc
        self._salida: str = manifiesto.get("salida", "last_hidden_state")
        self._pooling: str = manifiesto.get("pooling", "mean")
        self._prefijo: str = manifiesto.get("prefijo_consulta", "")
        opciones = onnxruntime.SessionOptions()
        opciones.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
        try:
            self._sesion = onnxruntime.InferenceSession(
                str(ruta_onnx), opciones, providers=["CPUExecutionProvider"]
            )
            self._tokenizador = Tokenizer.from_file(
                str(directorio / manifiesto.get("tokenizer", "tokenizer.json"))
            )
        except Exception as exc:  # noqa: BLE001 - archivo ausente o corrupto
            raise CodificadorNoDisponible(f"No se pudo cargar el modelo de {directorio}: {exc}") from exc
        self._tokenizador.enable_truncation(max_length=int(manifiesto.get("max_tokens", 256)))
        self._tokenizador.enable_padding()
        self._entradas = {i.name for i in self._sesion.get_inputs()}

    def _lote(self, textos: list[str]) -> Any:
        import numpy as np

        codificados = self._tokenizador.encode_batch(textos)
        ids = np.array([e.ids for e in codificados], dtype=np.int64)
        mascara = np.array([e.attention_mask for e in codificados], dtype=np.int64)
        entrada: dict[str, Any] = {"input_ids": ids, "attention_mask": mascara}
        if "token_type_ids" in self._entradas:
            entrada["token_type_ids"] = np.zeros_like(ids)
        salida = self._sesion.run([self._salida], entrada)[0]
        if self._pooling == "mean":
            m = mascara[:, :, None].astype(np.float32)
            salida = (salida * m).sum(axis=1) / np.maximum(m.sum(axis=1), 1.0)
        salida = salida.astype(np.float32)
        return salida / np.maximum(np.linalg.norm(salida, axis=1, keepdims=True), 1e-12)

    def codificar(self, textos: list[str]) -> Any:
        import numpy as np

        if not textos:
            return np.zeros((0, self.dimensiones), dtype=np.float32)
        return np.vstack([self._lote(textos[i : i + LOTE]) for i in range(0, len(textos), LOTE)])

    def codificar_consulta(self, texto: str) -> Any:
        return self._lote([self._prefijo + texto])[0]


def cargar(nombre: str = PREDETERMINADO) -> CodificadorOnnx | None:
    """El codificador instalado, o ``None`` si no hay ninguno (la búsqueda sigue por palabras).

    Lanza ``CodificadorNoDisponible`` solo si hay un modelo pero no se puede usar (faltan dependencias o
    está corrupto): eso sí merece un aviso al usuario."""

    directorio = directorio_de(nombre)
    if not (directorio / MANIFIESTO).is_file():
        return None
    return CodificadorOnnx(directorio)


def a_int8(vectores: Any) -> list[bytes]:
    """Cuantiza vectores unitarios a int8 (``round(x·127)``); es lo que guarda el índice."""

    import numpy as np

    return [bytes(fila) for fila in np.clip(np.rint(vectores * 127.0), -127, 127).astype(np.int8)]
