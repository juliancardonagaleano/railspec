"""Consola: desvincular un repositorio borra grafo, snapshots y vínculo, en ese orden y reintentable."""

from __future__ import annotations

import asyncio
import types

import pytest
from apoyo_motor import ORG, REPO, WS, snapshot, vinculo
from railspec.contracts.comun import AlcanceRepositorio, AlcanceUnidad, NivelCodigo
from railspec.contracts.repositorio import Rol
from railspec.graph import AccesoGrafo, AlmacenGrafo, MotorMemoria
from railspec.graph.motor import Meta
from railspec.server.consola import rutas_admin
from railspec.server.estado import almacen_en_memoria
from test_consola import ANA_ID, CSRF, Montaje, asignar

OTRO_REPO = "facturas-api"
UNIDAD_A, UNIDAD_B = "0001-emitir-pdf", "0002-firmar-pdf"
AMBOS = [REPO, OTRO_REPO]  # los vínculos salen ordenados por repositorio
RUTA = f"/consola/api/orgs/{ORG}/workspaces/{WS}"
FALLO = "redis://:clave-secreta@falkordb:6379 caído"


def correr(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _sin_esperas(monkeypatch):
    monkeypatch.setattr(rutas_admin, "ESPERA_DESVINCULO_S", 0)


class GrafoEspia(AlmacenGrafo):
    """``AlmacenGrafo`` en memoria cuyo borrado falla las primeras ``fallos`` veces (``None``: siempre)."""

    def __init__(self, acceso, fallos: int | None = 0) -> None:
        super().__init__(acceso)
        self.fallos = fallos
        self.llamadas = 0

    def borrar_repositorio(self, alcance) -> None:
        self.llamadas += 1
        if self.fallos is None or self.llamadas <= self.fallos:
            raise ConnectionError(FALLO)
        super().borrar_repositorio(alcance)


def _repo(slug: str = REPO, workspace: str = WS) -> AlcanceRepositorio:
    return AlcanceRepositorio(org=ORG, workspace=workspace, repositorio=slug)


def _guardar_snapshot(almacen, unidad: str, repo: str = REPO, workspace: str = WS) -> None:
    orden = types.SimpleNamespace(
        unidad=AlcanceUnidad(org=ORG, workspace=workspace, unidad=unidad),
        repositorio=repo,
        base_commit="a" * 40,
    )
    almacen.guardar_snapshot(snapshot(orden))


def _poblar_grafo(acceso: AccesoGrafo, repo: AlcanceRepositorio) -> None:
    """Un canónico y la superposición de una unidad: lo que borra ``borrar_repositorio``."""

    acceso.espacio(repo).fijar_meta(Meta(commit="b" * 40))
    acceso.espacio(repo, UNIDAD_A).fijar_meta(Meta(unidad=UNIDAD_A))


def _hay_grafo(acceso: AccesoGrafo, repo: AlcanceRepositorio) -> bool:
    return acceso.espacio(repo).existe() or acceso.superposiciones(repo) != []


def _montaje(grafo=None, **extra):
    """Montaje con un workspace-admin, el vínculo del repo y otro vínculo del mismo workspace."""

    m = Montaje(nivel=None, **({"grafo": grafo} if grafo is not None else {}) | extra)
    asignar(m.almacen, Rol.workspace_admin, ANA_ID)
    for slug in (REPO, OTRO_REPO):
        v = vinculo(NivelCodigo.interno)
        m.almacen.guardar_configuracion(
            [v.model_copy(update={"alcance": _repo(slug), "url": f"https://github.com/acme/{slug}"})]
        )
    return m


def _eventos(m: Montaje) -> list[dict]:
    """Registros ``desvinculo-repositorio`` del más antiguo al más reciente."""

    registros, _ = m.ctx.datos.auditoria(ORG, WS, evento="desvinculo-repositorio")
    return [r.model_dump(mode="json") for r in reversed(registros)]


def _vinculados(m: Montaje) -> list[str]:
    return [v.alcance.repositorio for v in m.almacen.vinculos(ORG, WS)]


def _snapshots(m: Montaje) -> list[tuple[str, str, str]]:
    docs = m.almacen.db.snapshots.find({})
    return sorted((d["unidad"]["workspace"], d["repositorio"], d["unidad"]["unidad"]) for d in docs)


def test_borrar_snapshots_repositorio_es_idempotente_y_no_cruza_repo_ni_workspace():
    almacen = almacen_en_memoria()
    _guardar_snapshot(almacen, UNIDAD_A)
    _guardar_snapshot(almacen, UNIDAD_B)
    _guardar_snapshot(almacen, UNIDAD_A, repo=OTRO_REPO)
    _guardar_snapshot(almacen, UNIDAD_A, workspace="otro-ws")  # mismo slug de repo, otro workspace

    assert almacen.borrar_snapshots_repositorio(_repo()) == 2
    assert almacen.borrar_snapshots_repositorio(_repo()) == 0
    restantes = sorted((d["unidad"]["workspace"], d["repositorio"]) for d in almacen.db.snapshots.find({}))
    assert restantes == [(WS, OTRO_REPO), ("otro-ws", REPO)]
    assert almacen.borrar_snapshots_repositorio(_repo(workspace="no-existe")) == 0


def test_desvincular_borra_grafo_snapshots_y_vinculo_en_orden_y_audita_inicio_y_fin():
    async def caso():
        acceso = AccesoGrafo(MotorMemoria())
        grafo = GrafoEspia(acceso)
        m = _montaje(grafo)
        _poblar_grafo(acceso, _repo())
        _poblar_grafo(acceso, _repo(OTRO_REPO))
        for unidad in (UNIDAD_A, UNIDAD_B):
            _guardar_snapshot(m.almacen, unidad)
        _guardar_snapshot(m.almacen, UNIDAD_A, repo=OTRO_REPO)

        # Qué había hecho ya cada paso cuando le tocó: auditoría de intención primero, vínculo el último.
        traza: list[tuple[str, int, int, list[str]]] = []

        def espiar(nombre, original):
            def paso(*args):
                traza.append((nombre, len(_eventos(m)), len(_snapshots(m)), _vinculados(m)))
                return original(*args)

            return paso

        grafo.borrar_repositorio = espiar("grafo", grafo.borrar_repositorio)
        m.almacen.borrar_snapshots_repositorio = espiar("snapshots", m.almacen.borrar_snapshots_repositorio)
        m.ctx.datos.borrar_vinculo = espiar("vinculo", m.ctx.datos.borrar_vinculo)

        async with m.cliente("tk-ana") as c:
            r = await c.delete(f"{RUTA}/repositorios/{REPO}", params={"motivo": "migrado"}, headers=CSRF)
        assert r.status_code == 204, r.text

        assert traza == [
            ("grafo", 1, 3, AMBOS),
            ("snapshots", 1, 3, AMBOS),
            ("vinculo", 1, 1, AMBOS),
        ]
        assert not _hay_grafo(acceso, _repo()) and _hay_grafo(acceso, _repo(OTRO_REPO))
        assert _snapshots(m) == [(WS, OTRO_REPO, UNIDAD_A)]
        assert _vinculados(m) == [OTRO_REPO]

        inicio, fin = _eventos(m)
        assert inicio["repositorio"] == REPO and inicio["actor"]["login"] == "ana"
        assert inicio["detalle"] == {"fase": "inicio", "motivo": "migrado", "nivel": "interno"}
        assert fin["detalle"] == {
            "fase": "fin",
            "estado": "completo",
            "motivo": "migrado",
            "nivel": "interno",
            "grafo_borrado": True,
            "snapshots_borrados": 2,
        }

    correr(caso())


def test_desvincular_sin_grafo_borra_snapshots_y_audita_grafo_no_borrado():
    async def caso():
        m = _montaje()  # sin grafo configurado (desarrollo)
        _guardar_snapshot(m.almacen, UNIDAD_A)
        async with m.cliente("tk-ana") as c:
            r = await c.delete(f"{RUTA}/repositorios/{REPO}", params={"motivo": "migrado"}, headers=CSRF)
        assert r.status_code == 204, r.text
        assert _snapshots(m) == [] and _vinculados(m) == [OTRO_REPO]
        assert _eventos(m)[-1]["detalle"]["grafo_borrado"] is False
        assert _eventos(m)[-1]["detalle"]["estado"] == "completo"

    correr(caso())


def test_fallo_del_grafo_conserva_el_vinculo_audita_incompleto_y_repetir_termina():
    async def caso():
        acceso = AccesoGrafo(MotorMemoria())
        grafo = GrafoEspia(acceso, fallos=None)
        m = _montaje(grafo)
        _poblar_grafo(acceso, _repo())
        _guardar_snapshot(m.almacen, UNIDAD_A)
        _guardar_snapshot(m.almacen, UNIDAD_B)
        url = f"{RUTA}/repositorios/{REPO}"

        async with m.cliente("tk-ana") as c:
            r = await c.delete(url, params={"motivo": "migrado"}, headers=CSRF)
            assert r.status_code == 502
            # Dice qué falta y que se puede repetir; el texto del fallo (con credenciales) no sale.
            assert "grafo" in r.json()["detalle"] and "repite" in r.json()["detalle"]
            assert "clave-secreta" not in r.text

            # Reintento acotado; el vínculo y el grafo siguen; los snapshots, que no dependían del grafo, no.
            assert grafo.llamadas == rutas_admin.INTENTOS_DESVINCULO
            assert _vinculados(m) == AMBOS and _hay_grafo(acceso, _repo())
            assert _snapshots(m) == []
            inicio, fin = _eventos(m)
            assert inicio["detalle"]["fase"] == "inicio"
            assert fin["detalle"] == {
                "fase": "fin",
                "estado": "incompleto",
                "motivo": "migrado",
                "nivel": "interno",
                "grafo_borrado": False,
                "snapshots_borrados": 2,
                "pendientes": "grafo",
                "error_grafo": "ConnectionError",
            }
            assert "clave-secreta" not in str(fin)

            # El repositorio sigue en la consola, así que se repite: ahora el grafo responde.
            grafo.fallos = 0
            r = await c.delete(url, params={"motivo": "migrado"}, headers=CSRF)
            assert r.status_code == 204, r.text

        assert not _hay_grafo(acceso, _repo()) and _vinculados(m) == [OTRO_REPO]
        assert [e["detalle"]["fase"] for e in _eventos(m)] == ["inicio", "fin", "inicio", "fin"]
        ultimo = _eventos(m)[-1]["detalle"]
        assert ultimo["estado"] == "completo" and ultimo["snapshots_borrados"] == 0

    correr(caso())


def test_fallo_transitorio_se_recupera_dentro_de_los_reintentos():
    async def caso():
        acceso = AccesoGrafo(MotorMemoria())
        grafo = GrafoEspia(acceso, fallos=rutas_admin.INTENTOS_DESVINCULO - 1)
        m = _montaje(grafo)
        _poblar_grafo(acceso, _repo())
        async with m.cliente("tk-ana") as c:
            r = await c.delete(f"{RUTA}/repositorios/{REPO}", params={"motivo": "migrado"}, headers=CSRF)
        assert r.status_code == 204, r.text
        assert grafo.llamadas == rutas_admin.INTENTOS_DESVINCULO
        assert not _hay_grafo(acceso, _repo()) and _vinculados(m) == [OTRO_REPO]
        assert [e["detalle"]["estado"] for e in _eventos(m) if "estado" in e["detalle"]] == ["completo"]

    correr(caso())


def test_fallo_de_los_snapshots_borra_el_grafo_conserva_el_vinculo_y_repetir_termina():
    async def caso():
        acceso = AccesoGrafo(MotorMemoria())
        m = _montaje(GrafoEspia(acceso))
        _poblar_grafo(acceso, _repo())
        _guardar_snapshot(m.almacen, UNIDAD_A)
        borrar = m.almacen.borrar_snapshots_repositorio
        intentos: list[int] = []

        def caido(alcance):
            intentos.append(1)
            if len(intentos) <= rutas_admin.INTENTOS_DESVINCULO:
                raise TimeoutError("mongodb://usuario:clave@mongo caído")
            return borrar(alcance)

        m.almacen.borrar_snapshots_repositorio = caido
        url = f"{RUTA}/repositorios/{REPO}"
        async with m.cliente("tk-ana") as c:
            r = await c.delete(url, params={"motivo": "migrado"}, headers=CSRF)
            assert r.status_code == 502 and "snapshots" in r.json()["detalle"]
            assert not _hay_grafo(acceso, _repo())
            assert _vinculados(m) == AMBOS and len(_snapshots(m)) == 1
            fin = _eventos(m)[-1]["detalle"]
            # Sin cuenta de snapshots: no se sabe cuántos hay borrados.
            assert fin["estado"] == "incompleto" and fin["pendientes"] == "snapshots"
            assert fin["error_snapshots"] == "TimeoutError" and "snapshots_borrados" not in fin
            assert fin["grafo_borrado"] is True and "clave" not in str(fin)

            r = await c.delete(url, params={"motivo": "migrado"}, headers=CSRF)
            assert r.status_code == 204, r.text
        assert _snapshots(m) == [] and _vinculados(m) == [OTRO_REPO]

    correr(caso())


def test_fallo_del_vinculo_audita_incompleto_con_datos_ya_borrados():
    async def caso():
        acceso = AccesoGrafo(MotorMemoria())
        m = _montaje(GrafoEspia(acceso))
        _poblar_grafo(acceso, _repo())
        _guardar_snapshot(m.almacen, UNIDAD_A)
        borrar = m.ctx.datos.borrar_vinculo
        intentos: list[int] = []

        def caido(alcance):
            intentos.append(1)
            if len(intentos) <= rutas_admin.INTENTOS_DESVINCULO:
                raise OSError("sin conexión")
            return borrar(alcance)

        m.ctx.datos.borrar_vinculo = caido
        url = f"{RUTA}/repositorios/{REPO}"
        async with m.cliente("tk-ana") as c:
            r = await c.delete(url, params={"motivo": "migrado"}, headers=CSRF)
            assert r.status_code == 502 and "vinculo" in r.json()["detalle"]
            fin = _eventos(m)[-1]["detalle"]
            assert fin["estado"] == "incompleto" and fin["pendientes"] == "vinculo"
            assert fin["grafo_borrado"] is True and fin["snapshots_borrados"] == 1
            assert _vinculados(m) == AMBOS

            r = await c.delete(url, params={"motivo": "migrado"}, headers=CSRF)
            assert r.status_code == 204, r.text
        assert _vinculados(m) == [OTRO_REPO]

    correr(caso())


def test_sin_motivo_o_sin_vinculo_no_toca_nada_ni_audita():
    async def caso():
        acceso = AccesoGrafo(MotorMemoria())
        m = _montaje(GrafoEspia(acceso))
        _poblar_grafo(acceso, _repo())
        _guardar_snapshot(m.almacen, UNIDAD_A)
        async with m.cliente("tk-ana") as c:
            assert (await c.delete(f"{RUTA}/repositorios/{REPO}", headers=CSRF)).status_code == 422
            r = await c.delete(f"{RUTA}/repositorios/no-vinculado", params={"motivo": "x"}, headers=CSRF)
            assert r.status_code == 404
        assert _hay_grafo(acceso, _repo()) and len(_snapshots(m)) == 1
        assert _vinculados(m) == AMBOS and _eventos(m) == []

    correr(caso())
