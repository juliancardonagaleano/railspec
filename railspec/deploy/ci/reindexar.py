"""Reindexado del grafo canónico desde CI: ``graph.index`` por lotes (contrato 1.1).

Lo corre el workflow ``railspec-reindexar.yml`` en cada push a la rama por
defecto, con el repositorio clonado en ``--raiz`` y el commit empujado
desprotegido. Pasos:

1. Decide delta o índice completo. Delta si el commit anterior del push existe
   en el clon y es ancestro del nuevo; si no (rama nueva, force-push, primer
   indexado), índice completo.
2. Calcula el ``DeltaIndice`` con el mismo indexador que el proxy local
   (``codebase-memory-mcp`` vía ``railspec.local.indexador_cbm``) y las
   exclusiones de secretos del proxy (por defecto más ``.railspecignore``).
   El servidor nunca indexa ni calcula embeddings de código.
3. Lo parte en lotes y llama ``POST {servidor}/v1/tools/graph.index`` con un
   token OIDC de GitHub Actions como actor de servicio. Si el servidor dice
   que el canónico no está en el commit base, repite con índice completo.

No lleva texto de código: ``Simbolo`` solo tiene ruta, rango y sha256.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from railspec.contracts.comun import AlcanceRepositorio
from railspec.contracts.snapshot import DeltaIndice
from railspec.contracts.tools import GraphIndexEntrada, GraphIndexSalida

#: Código con el que el servidor rechaza un delta cuya base no es el
#: canónico vigente; entonces se reintenta con índice completo.
CODIGOS_DESFASE = {"base-commit-distinto"}
CERO = "0" * 40


class Indexador(Protocol):
    def delta(
        self, worktree: Path, repositorio: str, base_commit: str, rutas: list[str], excluir: list[str]
    ) -> DeltaIndice: ...


class ErrorReindexado(RuntimeError):
    pass


class RechazoServidor(ErrorReindexado):
    def __init__(self, estado: int, cuerpo: dict[str, Any]) -> None:
        super().__init__(f"graph.index respondió {estado}: {json.dumps(cuerpo, ensure_ascii=False)[:500]}")
        self.estado = estado
        self.codigo = cuerpo.get("codigo")


# --- git ------------------------------------------------------------------------------


def _git(raiz: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(raiz), *args], capture_output=True, check=False)
    if proc.returncode != 0:
        raise ErrorReindexado(f"git {' '.join(args[:2])}: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout.decode("utf-8")


def _existe(raiz: Path, commit: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(raiz), "cat-file", "-e", f"{commit}^{{commit}}"], capture_output=True
        ).returncode
        == 0
    )


def _es_ancestro(raiz: Path, anterior: str, commit: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(raiz), "merge-base", "--is-ancestor", anterior, commit], capture_output=True
        ).returncode
        == 0
    )


@dataclass(frozen=True)
class Plan:
    rutas: list[str]
    base: str
    commit_anterior: str | None

    @property
    def completo(self) -> bool:
        return self.commit_anterior is None


def plan_completo(raiz: Path, commit: str) -> Plan:
    arbol_vacio = _git(raiz, "hash-object", "-t", "tree", "/dev/null").strip()
    rutas = [r for r in _git(raiz, "ls-tree", "-r", "-z", "--name-only", commit).split("\0") if r]
    return Plan(rutas=rutas, base=arbol_vacio, commit_anterior=None)


def planificar(raiz: Path, commit: str, anterior: str | None) -> Plan:
    if not anterior or anterior == CERO or anterior == commit:
        return plan_completo(raiz, commit)
    if not _existe(raiz, anterior) or not _es_ancestro(raiz, anterior, commit):
        return plan_completo(raiz, commit)
    salida = _git(raiz, "diff", "--name-only", "--no-renames", "-z", anterior, commit)
    return Plan(rutas=sorted(r for r in salida.split("\0") if r), base=anterior, commit_anterior=anterior)


# --- lotes ----------------------------------------------------------------------------


def partir(delta: DeltaIndice, tamano: int) -> list[DeltaIndice]:
    """Parte el delta en lotes de a lo sumo ``tamano`` elementos por lista.

    Cada embedding viaja en el lote de su símbolo, como exige ``DeltaIndice``.
    Siempre devuelve al menos un lote: un delta vacío también avanza el commit.
    """

    if tamano < 1:
        raise ValueError("tamano debe ser >= 1")
    listas = {
        "simbolos_upsert": delta.simbolos_upsert,
        "simbolos_borrados": delta.simbolos_borrados,
        "aristas_agregadas": delta.aristas_agregadas,
        "aristas_borradas": delta.aristas_borradas,
    }
    n = max(1, *(math.ceil(len(v) / tamano) for v in listas.values()))
    embeddings: dict[str, list] = {}
    for e in delta.embeddings:
        embeddings.setdefault(e.simbolo, []).append(e)
    lotes = []
    for i in range(n):
        trozo = {k: v[i * tamano : (i + 1) * tamano] for k, v in listas.items()}
        propios = [e for s in trozo["simbolos_upsert"] for e in embeddings.get(s.id, [])]
        lotes.append(DeltaIndice(motor=delta.motor, embeddings=propios, **trozo))
    return lotes


# --- HTTP -----------------------------------------------------------------------------


class TokenOidc:
    """Token OIDC de GitHub Actions para ``audiencia``; se renueva cada pocos minutos."""

    VIDA_S = 240

    def __init__(self, audiencia: str, entorno: dict[str, str] | None = None) -> None:
        env = os.environ if entorno is None else entorno
        self._url = env.get("ACTIONS_ID_TOKEN_REQUEST_URL")
        self._clave = env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
        if not self._url or not self._clave:
            raise ErrorReindexado("sin OIDC: el job necesita 'permissions: id-token: write'")
        self._audiencia = audiencia
        self._valor: str | None = None
        self._desde = 0.0

    def __call__(self) -> str:
        if self._valor is None or time.monotonic() - self._desde > self.VIDA_S:
            url = f"{self._url}&audience={urllib.parse.quote(self._audiencia)}"
            peticion = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._clave}"})
            with urllib.request.urlopen(peticion, timeout=30) as r:
                self._valor = json.load(r)["value"]
            self._desde = time.monotonic()
        return self._valor


Transporte = Callable[[str, dict[str, Any], str], tuple[int, dict[str, Any]]]


def transporte_http(url: str, cuerpo: dict[str, Any], token: str) -> tuple[int, dict[str, Any]]:
    datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
    peticion = urllib.request.Request(
        url,
        data=datos,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(peticion, timeout=120) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except json.JSONDecodeError:
            return exc.code, {}


def enviar(
    entrada: GraphIndexEntrada,
    url: str,
    token: Callable[[], str],
    transporte: Transporte = transporte_http,
    intentos: int = 4,
    espera_s: float = 2.0,
) -> GraphIndexSalida:
    cuerpo = entrada.model_dump(mode="json", exclude_none=True)
    for intento in range(1, intentos + 1):
        try:
            estado, respuesta = transporte(url, cuerpo, token())
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            estado, respuesta = 0, {"detalle": str(exc)}
        if estado == 200:
            return GraphIndexSalida.model_validate(respuesta)
        if estado not in (0, 429) and estado < 500:
            raise RechazoServidor(estado, respuesta)
        if intento == intentos:
            raise RechazoServidor(estado, respuesta)
        time.sleep(espera_s * 2 ** (intento - 1))
    raise AssertionError("inalcanzable")


# --- flujo ----------------------------------------------------------------------------


def _subir(
    plan: Plan,
    delta: DeltaIndice,
    alcance: AlcanceRepositorio,
    rama: str,
    commit: str,
    url: str,
    token: Callable[[], str],
    tamano: int,
    transporte: Transporte,
    espera_s: float,
) -> GraphIndexSalida:
    lotes = partir(delta, tamano)
    salida = None
    for i, parte in enumerate(lotes, start=1):
        entrada = GraphIndexEntrada(
            alcance=alcance,
            rama=rama,
            commit=commit,
            commit_anterior=plan.commit_anterior,
            lote=i,
            lotes=len(lotes),
            delta=parte,
        )
        salida = enviar(entrada, url, token, transporte, espera_s=espera_s)
        print(f"lote {i}/{len(lotes)}: recibidos={salida.lotes_recibidos} aplicado={salida.aplicado}")
    assert salida is not None
    if not salida.aplicado:
        raise ErrorReindexado("el servidor recibió todos los lotes pero no aplicó el commit")
    return salida


def reindexar(
    raiz: Path,
    alcance: AlcanceRepositorio,
    rama: str,
    commit: str,
    anterior: str | None,
    servidor: str,
    token: Callable[[], str],
    indexador: Indexador,
    excluir: list[str],
    tamano: int = 2000,
    transporte: Transporte = transporte_http,
    espera_s: float = 2.0,
) -> GraphIndexSalida:
    url = servidor.rstrip("/") + "/v1/tools/graph.index"
    plan = planificar(raiz, commit, anterior)
    print(f"{'índice completo' if plan.completo else 'delta'}: {len(plan.rutas)} rutas")
    delta = indexador.delta(raiz, alcance.repositorio, plan.base, plan.rutas, excluir)
    try:
        return _subir(plan, delta, alcance, rama, commit, url, token, tamano, transporte, espera_s)
    except RechazoServidor as exc:
        if plan.completo or exc.codigo not in CODIGOS_DESFASE:
            raise
        print(f"canónico desfasado ({exc.codigo}): se reintenta con índice completo")
    plan = plan_completo(raiz, commit)
    delta = indexador.delta(raiz, alcance.repositorio, plan.base, plan.rutas, excluir)
    return _subir(plan, delta, alcance, rama, commit, url, token, tamano, transporte, espera_s)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--servidor", required=True, help="URL base de railspec-server (sin /mcp).")
    p.add_argument("--org", required=True)
    p.add_argument("--workspace", required=True)
    p.add_argument("--repositorio", required=True, help="Slug del repositorio vinculado.")
    p.add_argument("--rama", required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--anterior", default=None, help="Commit anterior del push (github.event.before).")
    p.add_argument("--audiencia", default="railspec", help="Audiencia del token OIDC.")
    p.add_argument("--raiz", type=Path, default=Path.cwd())
    p.add_argument("--tamano-lote", type=int, default=2000)
    a = p.parse_args(argv)

    from railspec.local import indexador_cbm, secretos

    indexador = indexador_cbm.crear()
    if indexador is None:
        print("reindexar: codebase-memory-mcp no está instalado", file=sys.stderr)
        return 2
    raiz = a.raiz.resolve()
    alcance = AlcanceRepositorio(org=a.org, workspace=a.workspace, repositorio=a.repositorio)
    try:
        salida = reindexar(
            raiz,
            alcance,
            a.rama,
            a.commit,
            a.anterior,
            a.servidor,
            TokenOidc(a.audiencia),
            indexador,
            secretos.exclusiones(raiz),
            a.tamano_lote,
        )
    except ErrorReindexado as exc:
        print(f"reindexar: {exc}", file=sys.stderr)
        return 1
    print(f"canónico en {salida.commit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
