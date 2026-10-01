"""Consola: creación atómica de perfiles, presupuestos y proveedores de contexto (B8)."""

from __future__ import annotations

import json
import threading

import pytest
from apoyo_motor import JULIAN, ORG, WS
from railspec.contracts.almacen import ConflictoVersion
from railspec.contracts.comun import Perfil
from railspec.contracts.repositorio import (
    Auditoria,
    PerfilConfig,
    PresupuestoConfig,
    ProveedorContexto,
)
from railspec.server.consola.almacen import AlmacenConsola
from test_consola import AHORA, CSRF, Montaje, correr


def _auditoria() -> Auditoria:
    return Auditoria(creado_por=JULIAN, creado_en=AHORA, actualizado_por=JULIAN, actualizado_en=AHORA)


def _perfil(workspace: str | None = None, nombre: Perfil = Perfil.estandar) -> PerfilConfig:
    return PerfilConfig.model_validate(
        {
            "org": ORG,
            "workspace": workspace,
            "nombre": nombre.value,
            "version": 1,
            "auditoria": _auditoria().model_dump(mode="json"),
            "roles": {"redactor": {"modelo": {"foundry": "gpt-5"}}},
            "gate": {"bajo": {"criticos": 1, "iteraciones": 1, "adversarial": False}},
            "exploradores": {"bajo": 1},
        }
    )


def _presupuesto(workspace: str | None = WS) -> PresupuestoConfig:
    return PresupuestoConfig.model_validate(
        {
            "org": ORG,
            "workspace": workspace,
            "version": 1,
            "auditoria": _auditoria().model_dump(mode="json"),
            "por_unidad": {"costo_usd_max": 5},
            "mensual_usd": 100,
        }
    )


def _proveedor(workspace: str | None = WS) -> ProveedorContexto:
    return ProveedorContexto.model_validate(
        {
            "org": ORG,
            "workspace": workspace,
            "rol": "gobernanza",
            "nombre": "pce",
            "version": 1,
            "auditoria": _auditoria().model_dump(mode="json"),
            "url": "https://pce.example",
            "politica_fallo": "estricta",
            "credencial_ref": "secret://pce/api-key",
        }
    )


CREAR = {
    "perfiles": lambda a, e: a.guardar_perfil(e, None),
    "presupuestos": lambda a, e: a.guardar_presupuesto(e, None),
    "proveedores_contexto": lambda a, e: a.guardar_proveedor_contexto(e, None),
}
ENTIDAD = {"perfiles": _perfil, "presupuestos": _presupuesto, "proveedores_contexto": _proveedor}


class _Cita:
    """Fuerza el peor entrelazado: todos los hilos pasan la comprobación de existencia antes de insertar."""

    def __init__(self, coleccion, hilos: int) -> None:
        self.original = coleccion.find_one
        self.barrera = threading.Barrier(hilos, timeout=5)
        self.coleccion = coleccion

    def instalar(self) -> None:
        def find_one(*args, **kwargs):
            r = self.original(*args, **kwargs)
            try:
                self.barrera.wait()
            except threading.BrokenBarrierError:
                pass
            return r

        self.coleccion.find_one = find_one


@pytest.mark.parametrize("coleccion", ["perfiles", "presupuestos", "proveedores_contexto"])
def test_creacion_concurrente_no_duplica(coleccion):
    m = Montaje()
    almacen = AlmacenConsola(m.almacen.db)
    col = m.almacen.db[coleccion]
    hilos = 6
    _Cita(col, hilos).instalar()
    resultados: list[object] = []
    bloqueo = threading.Lock()

    def crear():
        try:
            CREAR[coleccion](almacen, ENTIDAD[coleccion]())
            salida: object = "creado"
        except ConflictoVersion as exc:
            salida = exc
        except Exception as exc:  # cualquier otra cosa es un fallo de la prueba
            salida = exc
        with bloqueo:
            resultados.append(salida)

    ts = [threading.Thread(target=crear) for _ in range(hilos)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert col.count_documents({}) == 1, "dos POST concurrentes crearon duplicados"
    assert resultados.count("creado") == 1
    perdedores = [r for r in resultados if r != "creado"]
    assert len(perdedores) == hilos - 1
    assert all(isinstance(r, ConflictoVersion) and r.esperada == 0 for r in perdedores), perdedores


def test_claves_deterministas_alineadas_con_las_del_motor():
    m = Montaje()
    almacen = AlmacenConsola(m.almacen.db)
    db = m.almacen.db
    almacen.guardar_perfil(_perfil(), None)
    almacen.guardar_perfil(_perfil(WS, Perfil.ligero), None)
    almacen.guardar_presupuesto(_presupuesto(None), None)
    almacen.guardar_presupuesto(_presupuesto(WS), None)
    almacen.guardar_proveedor_contexto(_proveedor(), None)
    almacen.guardar_proveedor_contexto(_proveedor(None), None)
    assert sorted(d["_id"] for d in db.perfiles.find()) == [f"{ORG}/*/estandar", f"{ORG}/{WS}/ligero"]
    assert sorted(d["_id"] for d in db.presupuestos.find()) == [f"{ORG}/*", f"{ORG}/{WS}"]
    assert sorted(d["_id"] for d in db.proveedores_contexto.find()) == [
        f"{ORG}/*/gobernanza/pce",
        f"{ORG}/{WS}/gobernanza/pce",
    ]
    # El motor, al guardar la misma entidad, reescribe el mismo documento (no crea otro).
    m.almacen.guardar_configuracion([_perfil(), _presupuesto(WS), _proveedor()])
    assert db.perfiles.count_documents({}) == 2
    assert db.presupuestos.count_documents({}) == 2
    assert db.proveedores_contexto.count_documents({}) == 2
    # Y lo que crea el motor primero lo reconoce la consola (409 al crear de nuevo).
    m2 = Montaje()
    m2.almacen.guardar_configuracion([_perfil(), _presupuesto(WS), _proveedor()])
    a2 = AlmacenConsola(m2.almacen.db)
    for coleccion in CREAR:
        with pytest.raises(ConflictoVersion):
            CREAR[coleccion](a2, ENTIDAD[coleccion]())
        assert m2.almacen.db[coleccion].count_documents({}) == 1
    assert [d["_id"] for d in m2.almacen.db.perfiles.find()] == [f"{ORG}/*/estandar"]


def test_documentos_antiguos_con_object_id_conviven():
    """Los creados antes de este cambio llevan ``_id`` ObjectId: se leen, se editan y bloquean la creación."""

    m = Montaje()
    almacen = AlmacenConsola(m.almacen.db)
    db = m.almacen.db
    antiguos = {
        "perfiles": _perfil(),
        "presupuestos": _presupuesto(),
        "proveedores_contexto": _proveedor(),
    }
    ids = {}
    for coleccion, entidad in antiguos.items():
        ids[coleccion] = db[coleccion].insert_one(json.loads(entidad.model_dump_json())).inserted_id
        assert not isinstance(ids[coleccion], str)
    for coleccion in antiguos:
        with pytest.raises(ConflictoVersion) as exc:
            CREAR[coleccion](almacen, ENTIDAD[coleccion]())
        assert exc.value.esperada == 0 and exc.value.actual == 1
        assert db[coleccion].count_documents({}) == 1
    # Editar con la versión conserva el ``_id`` antiguo y sube la versión.
    perfil = _perfil().model_copy(update={"version": 2})
    almacen.guardar_perfil(perfil, 1)
    presupuesto = _presupuesto().model_copy(update={"version": 2})
    almacen.guardar_presupuesto(presupuesto, 1)
    proveedor = _proveedor().model_copy(update={"version": 2})
    almacen.guardar_proveedor_contexto(proveedor, 1)
    for coleccion in antiguos:
        docs = list(db[coleccion].find())
        assert len(docs) == 1 and docs[0]["_id"] == ids[coleccion] and docs[0]["version"] == 2
    assert almacen.perfil(ORG, None, "estandar").version == 2
    # Una versión vieja sigue dando conflicto (bloqueo optimista intacto).
    with pytest.raises(ConflictoVersion) as exc:
        almacen.guardar_perfil(perfil, 1)
    assert exc.value.esperada == 1 and exc.value.actual == 2
    # El motor reescribe el documento antiguo sin duplicarlo.
    m.almacen.guardar_configuracion([perfil, presupuesto, proveedor])
    for coleccion in antiguos:
        assert db[coleccion].count_documents({}) == 1


def test_creacion_http_duplicada_responde_409():
    async def caso():
        m = Montaje()
        async with m.cliente("tk-julian") as c:
            rutas = [
                (
                    f"/consola/api/orgs/{ORG}/presupuestos",
                    {"por_unidad": {"costo_usd_max": 5}, "mensual_usd": 1},
                ),
                (
                    f"/consola/api/orgs/{ORG}/proveedores-contexto/gobernanza/pce",
                    {
                        "url": "https://pce.example",
                        "politica_fallo": "estricta",
                        "credencial_ref": "secret://pce/api-key",
                    },
                ),
            ]
            for ruta, cuerpo in rutas:
                assert (await c.put(ruta, json=cuerpo, headers=CSRF)).status_code == 200
                r = await c.put(ruta, json=cuerpo, headers=CSRF)
                assert r.status_code == 409, r.text
        assert m.almacen.db.presupuestos.count_documents({}) == 1
        assert m.almacen.db.proveedores_contexto.count_documents({}) == 1

    correr(caso())


def test_indices_unicos_por_clave_natural():
    m = Montaje()
    db = m.almacen.db
    for coleccion, campos in (
        ("perfiles", ["org", "workspace", "nombre"]),
        ("presupuestos", ["org", "workspace"]),
        ("proveedores_contexto", ["org", "workspace", "rol", "nombre"]),
    ):
        unicos = [i["key"] for i in db[coleccion].index_information().values() if i.get("unique")]
        assert [(c, 1) for c in campos] in [list(k) if not isinstance(k, list) else k for k in unicos], (
            coleccion,
            unicos,
        )
