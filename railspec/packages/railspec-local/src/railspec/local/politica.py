"""Construcción del snapshot aplicando la política de código propietario.

El snapshot es el único vehículo por el que algo del código sale del clon.
Lo que viaja depende del nivel del vínculo del repositorio:

========== ================================================================
Nivel      Qué viaja además de rutas, hashes de archivo y delta del índice
========== ================================================================
restringido nada (ni diff ni fragmentos)
interno    fragmentos acotados a los símbolos tocados por la unidad
abierto    diff unificado base..árbol (sin archivos excluidos), si cabe
========== ================================================================

Los embeddings, si el indexador los trae, solo se calculan en local; el indexador incluido
(``codebase-memory-mcp``) no los calcula y el delta viaja sin ellos. Sin indexador local el snapshot va en
``solo-hashes`` en cualquier nivel.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import UUID

from railspec.contracts.comun import AlcanceUnidad, NivelCodigo
from railspec.contracts.snapshot import (
    DIFF_MAX_BYTES,
    CambioArchivo,
    DeltaIndice,
    EscaneoSecretos,
    EstadoArchivo,
    Fragmento,
    ModoDelta,
    Snapshot,
)

from . import git, secretos
from .errores import SecretosDetectados
from .indice import Indexador

log = logging.getLogger(__name__)

_ESTADOS = {
    "A": EstadoArchivo.agregado,
    "M": EstadoArchivo.modificado,
    "T": EstadoArchivo.modificado,
    "D": EstadoArchivo.borrado,
    "R": EstadoArchivo.renombrado,
}


@dataclass
class ResultadoSnapshot:
    snapshot: Snapshot
    excluidos: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def construir_snapshot(
    *,
    worktree: Path,
    unidad: AlcanceUnidad,
    repositorio: str,
    base_commit: str,
    nivel: NivelCodigo,
    indexador: Indexador | None,
    snapshot_id: UUID,
    ahora: datetime,
) -> ResultadoSnapshot:
    arbol = git.hash_arbol(worktree)
    patrones = secretos.exclusiones(worktree)
    avisos: list[str] = []

    excluidos: list[str] = []
    archivos: list[CambioArchivo] = []
    hallazgos: list[secretos.Hallazgo] = []
    for cambio in git.cambios(worktree, base_commit, arbol):
        rutas = [cambio.ruta] + ([cambio.ruta_anterior] if cambio.ruta_anterior else [])
        if any(secretos.excluido(r, patrones) for r in rutas):
            excluidos.append(cambio.ruta)
            continue
        estado = _ESTADOS.get(cambio.estado, EstadoArchivo.modificado)
        antes = despues = None
        if estado in (EstadoArchivo.modificado, EstadoArchivo.borrado):
            antes = git.sha256_blob(worktree, base_commit, cambio.ruta)
        if estado == EstadoArchivo.renombrado and cambio.ruta_anterior:
            antes = git.sha256_blob(worktree, base_commit, cambio.ruta_anterior)
        if estado != EstadoArchivo.borrado:
            datos = git.contenido(worktree, arbol, cambio.ruta)
            hallazgos += secretos.escanear(cambio.ruta, datos)
            despues = hashlib.sha256(datos).hexdigest()
        archivos.append(
            CambioArchivo(
                ruta=cambio.ruta,
                estado=estado,
                ruta_anterior=cambio.ruta_anterior,
                sha256_antes=antes,
                sha256_despues=despues,
            )
        )
    if hallazgos:
        raise SecretosDetectados([str(h) for h in hallazgos])

    delta = _delta(indexador, worktree, repositorio, base_commit, archivos, patrones, avisos)
    modo = ModoDelta.completo if delta is not None else ModoDelta.solo_hashes
    diff: str | None = None
    fragmentos: list[Fragmento] | None = None
    if modo == ModoDelta.completo and nivel == NivelCodigo.interno:
        fragmentos = _fragmentos(worktree, arbol, delta, {a.ruta for a in archivos}) or None
    elif modo == ModoDelta.completo and nivel == NivelCodigo.abierto:
        crudo = git.diff(worktree, base_commit, arbol, excluidos)
        if len(crudo) > DIFF_MAX_BYTES:
            avisos.append(f"diff de {len(crudo)} bytes supera el tope; viaja sin diff")
        elif crudo:
            diff = crudo.decode("utf-8", "replace")

    snapshot = Snapshot(
        id=snapshot_id,
        unidad=unidad,
        repositorio=repositorio,
        base_commit=base_commit,
        hash_arbol=arbol,
        creado_en=ahora,
        nivel_codigo=nivel,
        modo_delta=modo,
        archivos=archivos,
        diff=diff,
        fragmentos=fragmentos,
        delta_indice=delta,
        escaneo_secretos=EscaneoSecretos(
            herramienta=secretos.HERRAMIENTA, version=secretos.VERSION, hallazgos=0
        ),
    )
    return ResultadoSnapshot(snapshot=snapshot, excluidos=excluidos, avisos=avisos)


def _delta(
    indexador: Indexador | None,
    worktree: Path,
    repositorio: str,
    base_commit: str,
    archivos: list[CambioArchivo],
    patrones: list[str],
    avisos: list[str],
) -> DeltaIndice | None:
    if indexador is None:
        return None
    try:
        return indexador.delta(worktree, repositorio, base_commit, [a.ruta for a in archivos], patrones)
    except Exception as exc:  # el indexador es externo: su fallo degrada, no bloquea
        log.warning("indexador local falló: %s", exc)
        avisos.append(f"indexador local falló ({exc}); el snapshot viaja en solo-hashes")
        return None


def _fragmentos(worktree: Path, arbol: str, delta: DeltaIndice, tocadas: set[str]) -> list[Fragmento]:
    """Texto de los símbolos tocados, solo de archivos tocados (nivel interno)."""

    lineas_por_ruta: dict[str, list[str]] = {}
    fragmentos: list[Fragmento] = []
    for simbolo in delta.simbolos_upsert:
        if simbolo.ruta not in tocadas:
            continue
        if simbolo.ruta not in lineas_por_ruta:
            datos = git.contenido(worktree, arbol, simbolo.ruta)
            lineas_por_ruta[simbolo.ruta] = datos.decode("utf-8", "replace").splitlines(keepends=True)
        texto = "".join(lineas_por_ruta[simbolo.ruta][simbolo.linea_inicio - 1 : simbolo.linea_fin])
        if not texto:
            continue
        fragmentos.append(
            Fragmento(
                ruta=simbolo.ruta,
                simbolo=simbolo.id,
                sha256=hashlib.sha256(texto.encode("utf-8")).hexdigest(),
                texto=texto,
            )
        )
    return fragmentos
