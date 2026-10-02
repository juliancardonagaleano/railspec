"""Reindexado del grafo canónico desde CI: ``graph.index`` por lotes (contrato 1.1; cobertura 1.5).

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
3. Declara en ``commits_cubiertos`` (1.5) los commits de la rama que el índice
   incorpora (``git rev-list --first-parent``, a lo sumo ``MAX_COMMITS_CUBIERTOS``):
   el servidor no tiene git y con eso retira solo las superposiciones de las
   unidades integradas en alguno de ellos. ``--sin-cobertura`` habla 1.4 (sin la
   lista): el índice completo retira todas las retenidas, que es como se limpian
   las que quedaron varadas. Un servidor 1.4 rechaza la lista con 422 y entonces
   se sube sin ella.
4. Lo parte en lotes y llama ``POST {servidor}/v1/tools/graph.index`` con un
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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from railspec.contracts.comun import AlcanceRepositorio
from railspec.contracts.snapshot import DeltaIndice
from railspec.contracts.tools import MAX_COMMITS_CUBIERTOS, GraphIndexEntrada, GraphIndexSalida

#: Código con el que el servidor rechaza un delta cuya base no es el
#: canónico vigente; entonces se reintenta con índice completo.
CODIGOS_DESFASE = {"base-commit-distinto"}
CERO = "0" * 40
#: Versión con la que se habla sin ``commits_cubiertos``: servidores 1.4 y ``--sin-cobertura``.
VERSION_SIN_COBERTURA = "1.4"


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
        self.errores = cuerpo.get("errores") or []

    @property
    def rechaza_cobertura(self) -> bool:
        """422 de un servidor 1.4: no conoce ``commits_cubiertos`` ni la versión 1.5."""

        return self.estado == 422 and any(
            str(e.get("ruta", "")).split(".")[0] in ("commits_cubiertos", "version_contrato")
            for e in self.errores
            if isinstance(e, dict)
        )


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
    #: Commits que el índice incorpora (1.5); ``None`` = no se declaran (1.4).
    cubiertos: list[str] | None = None

    @property
    def completo(self) -> bool:
        return self.commit_anterior is None

    def sin_cobertura(self) -> Plan:
        return replace(self, cubiertos=None)


def commits_cubiertos(raiz: Path, commit: str, anterior: str | None) -> list[str]:
    """Los commits de la rama por defecto que el índice de ``commit`` incorpora.

    Primeros padres, del más reciente al más antiguo y a lo sumo ``MAX_COMMITS_CUBIERTOS``:
    ``commit_integrado`` es siempre la punta de la rama en algún momento, y las puntas son
    primeros padres. Si hay más, se declaran los más recientes: lo que queda fuera se retira
    más tarde, nunca antes. Si git falla se declara solo ``commit``."""

    rango = commit if anterior is None else f"{anterior}..{commit}"
    try:
        salida = _git(raiz, "rev-list", "--first-parent", f"--max-count={MAX_COMMITS_CUBIERTOS}", rango)
    except ErrorReindexado as exc:
        print(f"reindexar: no se pudo enumerar commits_cubiertos ({exc}); se declara solo el commit")
        return [commit]
    return salida.split()


def plan_completo(raiz: Path, commit: str) -> Plan:
    arbol_vacio = _git(raiz, "hash-object", "-t", "tree", "/dev/null").strip()
    rutas = [r for r in _git(raiz, "ls-tree", "-r", "-z", "--name-only", commit).split("\0") if r]
    return Plan(
        rutas=rutas,
        base=arbol_vacio,
        commit_anterior=None,
        cubiertos=commits_cubiertos(raiz, commit, None),
    )


def planificar(raiz: Path, commit: str, anterior: str | None) -> Plan:
    if not anterior or anterior == CERO or anterior == commit:
        return plan_completo(raiz, commit)
    if not _existe(raiz, anterior) or not _es_ancestro(raiz, anterior, commit):
        return plan_completo(raiz, commit)
    salida = _git(raiz, "diff", "--name-only", "--no-renames", "-z", anterior, commit)
    return Plan(
        rutas=sorted(r for r in salida.split("\0") if r),
        base=anterior,
        commit_anterior=anterior,
        cubiertos=commits_cubiertos(raiz, commit, anterior),
    )


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
            commits_cubiertos=plan.cubiertos,
            **({} if plan.cubiertos is not None else {"version_contrato": VERSION_SIN_COBERTURA}),
        )
        salida = enviar(entrada, url, token, transporte, espera_s=espera_s)
        print(f"lote {i}/{len(lotes)}: recibidos={salida.lotes_recibidos} aplicado={salida.aplicado}")
    assert salida is not None
    if not salida.aplicado:
        raise ErrorReindexado("el servidor recibió todos los lotes pero no aplicó el commit")
    return salida


def _subir_o_degradar(
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
    """``_subir``; un servidor 1.4 que rechaza ``commits_cubiertos`` recibe el índice sin la lista."""

    try:
        return _subir(plan, delta, alcance, rama, commit, url, token, tamano, transporte, espera_s)
    except RechazoServidor as exc:
        if plan.cubiertos is None or not exc.rechaza_cobertura:
            raise
        print("el servidor no admite commits_cubiertos (contrato 1.4): se sube sin cobertura")
        return _subir(
            plan.sin_cobertura(), delta, alcance, rama, commit, url, token, tamano, transporte, espera_s
        )


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
    cobertura: bool = True,
) -> GraphIndexSalida:
    url = servidor.rstrip("/") + "/v1/tools/graph.index"
    plan = planificar(raiz, commit, anterior)
    if not cobertura:
        plan = plan.sin_cobertura()
    print(f"{'índice completo' if plan.completo else 'delta'}: {len(plan.rutas)} rutas")
    delta = indexador.delta(raiz, alcance.repositorio, plan.base, plan.rutas, excluir)
    try:
        return _subir_o_degradar(plan, delta, alcance, rama, commit, url, token, tamano, transporte, espera_s)
    except RechazoServidor as exc:
        if plan.completo or exc.codigo not in CODIGOS_DESFASE:
            raise
        print(f"canónico desfasado ({exc.codigo}): se reintenta con índice completo")
    plan = plan_completo(raiz, commit)
    if not cobertura:
        plan = plan.sin_cobertura()
    delta = indexador.delta(raiz, alcance.repositorio, plan.base, plan.rutas, excluir)
    return _subir_o_degradar(plan, delta, alcance, rama, commit, url, token, tamano, transporte, espera_s)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--servidor", required=True, help="URL base de railspec-server (sin /mcp).")
    p.add_argument("--org", required=True)
    p.add_argument("--workspace", required=True)
    p.add_argument("--repositorio", required=True, help="Slug del repositorio vinculado.")
    p.add_argument("--rama", required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--anterior", default=None, help="Commit anterior del push (github.event.before).")
    p.add_argument(
        "--audiencia", required=True, help="Audiencia del token OIDC (la del servidor; sin defecto)."
    )
    p.add_argument("--raiz", type=Path, default=Path.cwd())
    p.add_argument("--tamano-lote", type=int, default=2000)
    p.add_argument(
        "--sin-cobertura",
        action="store_true",
        help=(
            "No declarar commits_cubiertos (contrato 1.4): un índice completo retira todas las "
            "superposiciones retenidas, también las varadas."
        ),
    )
    a = p.parse_args(argv)
    if not a.audiencia.strip():
        p.error("--audiencia vacía: define la variable RAILSPEC_OIDC_AUDIENCIA del repositorio")

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
            cobertura=not a.sin_cobertura,
        )
    except ErrorReindexado as exc:
        print(f"reindexar: {exc}", file=sys.stderr)
        return 1
    print(f"canónico en {salida.commit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
