"""Aislamiento por organización y workspace de los almacenes sobre Mongo.

Un ``db`` espía registra el filtro de cada consulta que hacen ``AlmacenMongo``, ``AlmacenConsola``,
``CheckpointsMongo`` y ``RevocadosMongo``, y la prueba falla si alguna no lleva org y workspace (o la
regla de su colección, ver ``REGLA_COLECCION``). Vale también para lecturas y reemplazos por ``_id``: un id
que existe en otro workspace no puede ni leerse ni pisarse. Cada método público tiene su receta; un método
nuevo sin receta rompe ``test_cada_metodo_publico_tiene_receta`` hasta que se clasifique.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from agent_framework import WorkflowCheckpoint
from agent_framework.exceptions import WorkflowCheckpointException
from apoyo_motor import (
    JULIAN,
    JULIAN_CONSOLA,
    ORG,
    REPO,
    WS,
    WS_ALCANCE,
    aprobar,
    avanzar,
    construir,
    entrada_start,
    reporte,
    snapshot,
    vinculo,
)
from pymongo.errors import DuplicateKeyError
from railspec.contracts.almacen import ConflictoVersion
from railspec.contracts.comun import (
    AlcanceRepositorio,
    AlcanceUnidad,
    NivelCodigo,
    Perfil,
    Presupuesto,
    Proveedor,
)
from railspec.contracts.eventos import Direccion
from railspec.contracts.repositorio import (
    AsignacionRol,
    Auditoria,
    Capacidades,
    EventoAuditoria,
    ModeloCatalogo,
    Organizacion,
    PresupuestoConfig,
    ProveedorContexto,
    RegistroAuditoria,
    Rol,
    RolContexto,
    SujetoUsuario,
    SuscripcionModelo,
    TelemetriaNodo,
    Workspace,
)
from railspec.contracts.tools import TelemetryQueryEntrada, UnitListEntrada
from railspec.server.avisos.modelo import ConfigAvisos
from railspec.server.consola.almacen import AlmacenConsola
from railspec.server.consola.revocados import RevocadosMongo
from railspec.server.estado import CheckpointsMongo, nombre_workflow
from railspec.server.estado.interfaces import EntradaPendiente, TipoEntrada
from railspec.server.estado.mongo import AlmacenMongo
from railspec.server.motor.perfiles import perfil_por_defecto
from railspec.server.proveedores.catalogo import EstadoLectura

AHORA = datetime(2026, 10, 2, 12, tzinfo=UTC)
OTRO_WS = "otro-workspace"

#: Qué exige el espía a cada consulta según la colección que toca:
#:   ``ws``     org y workspace (o ``workspace`` nulo explícito: el nivel organización de la configuración);
#:   ``org``    la organización: datos de toda la organización que lo son a propósito;
#:   ``global`` nada: la sesión revocada es de una persona, no de un workspace.
#: Lo que no está aquí es ``ws``, así que una colección nueva nace con la regla más estricta.
REGLA_COLECCION = {
    "cache_nodos": "org",
    "avisos_config": "org",
    "avisos_envios": "org",
    "avisos_eventos": "org",
    "avisos_historial": "org",
    "avisos_secretos": "org",
    "catalogo": "org",
    "catalogo_estado": "org",
    "organizaciones": "org",
    "roles": "org",
    "sesiones_revocadas": "global",
    "suscripciones": "org",
    "suscripciones_claves": "org",
    "workspaces": "ws",
}


# --- el espía -------------------------------------------------------------------------------------

#: Métodos de colección que llevan un filtro como primer argumento.
CON_FILTRO = {
    "find",
    "find_one",
    "find_one_and_update",
    "find_one_and_replace",
    "find_one_and_delete",
    "replace_one",
    "update_one",
    "update_many",
    "delete_one",
    "delete_many",
    "count_documents",
    "distinct",
}
#: Métodos que no consultan por filtro (el documento que se inserta lleva su propio alcance).
SIN_FILTRO = {"insert_one", "insert_many", "create_index"}


class ColeccionEspia:
    def __init__(self, real: Any, nombre: str, registro: list[tuple[str, str, Any]]) -> None:
        self._real, self._nombre, self._registro = real, nombre, registro

    def __getattr__(self, metodo: str) -> Any:
        atributo = getattr(self._real, metodo)
        if metodo == "aggregate":

            def agregar(etapas: list[dict[str, Any]], *a: Any, **kw: Any) -> Any:
                for etapa in etapas:
                    if "$match" in etapa:
                        self._registro.append((self._nombre, metodo, etapa["$match"]))
                if not any("$match" in etapa for etapa in etapas):
                    self._registro.append((self._nombre, metodo, {}))
                return atributo(etapas, *a, **kw)

            return agregar
        if metodo in CON_FILTRO:

            def consultar(filtro: Any = None, *a: Any, **kw: Any) -> Any:
                self._registro.append((self._nombre, metodo, {} if filtro is None else filtro))
                return atributo(filtro, *a, **kw)

            return consultar
        if metodo in SIN_FILTRO:
            return atributo
        raise AssertionError(
            f"{self._nombre}.{metodo}: el espía no sabe qué filtro lleva; añádelo a la prueba"
        )


class EspiaDb:
    def __init__(self, real: Any, registro: list[tuple[str, str, Any]]) -> None:
        self._real, self.registro = real, registro

    def __getitem__(self, nombre: str) -> ColeccionEspia:
        return ColeccionEspia(self._real[nombre], nombre, self.registro)

    def __getattr__(self, nombre: str) -> ColeccionEspia:
        if nombre.startswith("_"):
            raise AttributeError(nombre)
        return self[nombre]


def _valores(filtro: Any, ruta: str = "") -> list[tuple[str, Any]]:
    """Pares (ruta, valor) de las hojas de un filtro, para encontrar ``workspace`` nulo."""

    if isinstance(filtro, dict):
        return [par for k, v in filtro.items() for par in _valores(v, f"{ruta}.{k}" if ruta else k)]
    if isinstance(filtro, list):
        return [par for v in filtro for par in _valores(v, ruta)]
    return [(ruta, filtro)]


def violacion(regla: str, filtro: Any, org: str, ws: str) -> bool:
    """¿Incumple el filtro lo que exige la regla?"""

    if regla == "global":
        return False
    texto = json.dumps(filtro, default=str, sort_keys=True)
    if org not in texto:
        return True
    if regla == "org":
        return False
    nulo = any(ruta.rsplit(".", 1)[-1] == "workspace" and valor is None for ruta, valor in _valores(filtro))
    return ws not in texto and not nulo


def revisar(
    registro: list[tuple[str, str, Any]], org: str = ORG, ws: str = WS, regla: str | None = None
) -> list[str]:
    """Las consultas del registro que no cumplen su regla (la de su colección o ``regla``)."""

    return [
        f"{coleccion}.{metodo}({json.dumps(filtro, default=str, sort_keys=True)})"
        for coleccion, metodo, filtro in registro
        if violacion(regla or REGLA_COLECCION.get(coleccion, "ws"), filtro, org, ws)
    ]


def test_el_espia_marca_las_consultas_sin_alcance():
    registro: list[tuple[str, str, Any]] = []
    db = EspiaDb(_db(), registro)
    db.checkpoints.find_one({"_id": "x"})
    db.ordenes.replace_one({"_id": "x"}, {"_id": "x"}, upsert=True)
    db.unidades.delete_many({"unidad.org": ORG})  # la organización sola no basta
    db.unidades.find({"unidad.org": ORG, "unidad.workspace": WS})
    db.perfiles.find_one({"org": ORG, "workspace": None})  # nivel organización explícito
    db.cache_nodos.find_one({"_id": f"{ORG}/h", "org": ORG})  # colección de organización
    db.sesiones_revocadas.find_one({"_id": "sid"})  # global
    assert [m.split("(")[0] for m in revisar(registro)] == [
        "checkpoints.find_one",
        "ordenes.replace_one",
        "unidades.delete_many",
    ]
    with pytest.raises(AssertionError, match="no sabe qué filtro"):
        db.unidades.bulk_write([])


def _db():
    import mongomock

    return mongomock.MongoClient(tz_aware=True)["railspec"]


# --- datos de prueba ------------------------------------------------------------------------------


def _auditoria() -> Auditoria:
    return Auditoria(creado_por=JULIAN, creado_en=AHORA, actualizado_por=JULIAN, actualizado_en=AHORA)


def _rol(workspace: str | None = WS, id_: uuid.UUID | None = None) -> AsignacionRol:
    return AsignacionRol(
        id=id_ or uuid.uuid4(),
        org=ORG,
        workspace=workspace,
        rol=Rol.org_admin if workspace is None else Rol.desarrollador,
        sujeto=SujetoUsuario(github_id=83125327),
        version=1,
        auditoria=_auditoria(),
    )


def _configuracion() -> dict[str, Any]:
    return {
        "perfil": perfil_por_defecto(WS_ALCANCE, Perfil.estandar),
        "presupuesto": PresupuestoConfig(
            version=1, auditoria=_auditoria(), org=ORG, workspace=WS, por_unidad=Presupuesto()
        ),
        "workspace": Workspace(version=1, auditoria=_auditoria(), alcance=WS_ALCANCE, nombre="Certificados"),
        "organizacion": Organizacion(
            id=ORG,
            nombre="ACME",
            github_org="acme",
            region_datos="eastus2",
            version=1,
            auditoria=_auditoria(),
        ),
        "proveedor": ProveedorContexto(
            version=1,
            auditoria=_auditoria(),
            org=ORG,
            workspace=WS,
            rol=RolContexto.gobernanza,
            nombre="pce",
            url="https://pce.acme.com/mcp",
            credencial_ref=f"secret://{ORG}--pce/clave",
            politica_fallo="estricta",
        ),
        "vinculo": vinculo(NivelCodigo.restringido),
        "suscripcion": SuscripcionModelo(
            version=1,
            auditoria=_auditoria(),
            org=ORG,
            id="foundry-eu",
            nombre="Foundry UE",
            proveedor=Proveedor.foundry,
            endpoint="https://acme.services.ai.azure.com",
            clave_configurada=True,
        ),
        "modelo": ModeloCatalogo(
            org=ORG,
            proveedor=Proveedor.foundry,
            modelo="gpt-5",
            hosting="azure",
            region="eastus2",
            capacidades=Capacidades(efforts=["low"], structured_outputs=True, contexto_max_tokens=200000),
            leido_en=AHORA,
        ),
        "estado_lectura": EstadoLectura(
            org=ORG,
            proveedor=Proveedor.foundry,
            intento_en=AHORA,
            origen="consola",
            por=None,
            resultado="ok",
            reutilizada=False,
            modelos=1,
            leido_en=AHORA,
            error=None,
        ),
        "avisos": ConfigAvisos(
            org=ORG, version=1, activo=True, destinatarios=["equipo@acme.com"], auditoria=_auditoria()
        ),
        "auditoria": RegistroAuditoria(
            id=uuid.uuid4(),
            alcance=WS_ALCANCE,
            evento=EventoAuditoria.cambio_configuracion,
            actor=JULIAN,
            en=AHORA,
            detalle={"entidad": "prueba"},
        ),
        "telemetria": TelemetriaNodo(
            id=uuid.uuid4(),
            org=ORG,
            workspace=WS,
            unidad="0001-x",
            nodo="n",
            fase="spec",
            duracion_ms=1,
            en=AHORA,
        ),
    }


def _poblar():
    """Una unidad en marcha (estado, eventos, orden, reporte, checkpoints) y configuración sembrada."""

    async def caso():
        motor, _ = construir(nivel=NivelCodigo.restringido)
        salida = await motor.start(entrada_start(), JULIAN)
        alcance = salida.estado.unidad
        avance = await avanzar(motor, alcance)
        informe = reporte(avance.orden)
        await motor.report(informe, JULIAN_CONSOLA)
        return motor, alcance, avance.orden, informe

    motor, alcance, orden, informe = asyncio.run(caso())
    config = _configuracion()
    almacen = motor.n.almacen
    almacen.guardar_configuracion(
        [config[c] for c in ("perfil", "presupuesto", "workspace", "proveedor", "vinculo")] + [_rol()]
    )
    almacen.guardar_catalogo(ORG, Proveedor.foundry, [config["modelo"]])
    consola = AlmacenConsola(almacen.db)
    consola.guardar_suscripcion(config["suscripcion"], None)
    consola.guardar_clave_suscripcion(ORG, "foundry-eu", "v1.00000000.cifrado")
    consola.guardar_avisos_config(config["avisos"], None)
    almacen.guardar_nodo_en_cache(ORG, "hash", {"valor": 1}, AHORA + timedelta(days=1))
    almacen.registrar_telemetria(config["telemetria"])
    almacen.registrar_auditoria(config["auditoria"])
    return SimpleNamespace(
        motor=motor,
        db=almacen.db,
        alcance=alcance,
        orden=orden,
        reporte=informe,
        snapshot=snapshot(orden),
        config=config,
        repo=AlcanceRepositorio(org=ORG, workspace=WS, repositorio=REPO),
    )


# --- el recorrido del motor -----------------------------------------------------------------------


def test_el_recorrido_del_motor_nunca_consulta_sin_alcance():
    """Todas las consultas que el motor hace en una unidad completa, con su cableado real."""

    async def caso():
        registro: list[tuple[str, str, Any]] = []
        motor, _ = construir(nivel=NivelCodigo.restringido)
        espia = EspiaDb(motor.n.almacen.db, registro)
        motor.n.almacen.db = espia
        motor.checkpoints._col = espia.checkpoints
        motor.checkpoints._contadores = espia.contadores
        salida = await motor.start(entrada_start(), JULIAN)
        alcance = salida.estado.unidad
        for _ in range(20):
            av = await avanzar(motor, alcance)
            if av.tipo == "cerrada":
                break
            if av.tipo == "orden":
                await motor.report(reporte(av.orden), JULIAN_CONSOLA)
            elif av.tipo == "checkpoint":
                await aprobar(motor, alcance, av.checkpoint.id, actor=JULIAN_CONSOLA)
        assert av.tipo == "cerrada"
        return registro

    registro = asyncio.run(caso())
    colecciones = {c for c, _, _ in registro}
    # Pasó por todo lo que el motor persiste por unidad.
    assert {
        "unidades",
        "eventos",
        "ordenes",
        "reportes",
        "snapshots",
        "checkpoints",
        "contadores",
    } <= colecciones
    assert len(registro) > 100
    assert revisar(registro) == []


# --- cada método público --------------------------------------------------------------------------


def _recetas() -> dict[str, tuple[Any, str]]:
    """``Clase.método`` -> (receta, regla que debe cumplir). La regla por defecto es ``ws``.

    Las recetas con otra regla son datos de toda la organización o de la plataforma, a propósito; la
    explicación de cada una está en el docstring de su módulo.
    """

    def a(c):  # AlmacenMongo
        return c.almacen

    def k(c):  # AlmacenConsola
        return c.consola

    def cp(c):  # CheckpointsMongo; sus métodos son async
        return c.checkpoints

    wf = lambda c: nombre_workflow(c.alcance)  # noqa: E731

    def nuevo(registro):  # los registros se insertan: id nuevo para no chocar con el sembrado
        return registro.model_copy(update={"id": uuid.uuid4()})

    def checkpoint(c, nombre=None):
        return WorkflowCheckpoint(workflow_name=nombre or wf(c), graph_signature_hash="h", state={})

    def con_checkpoint(c, accion):
        async def caso():
            guardado = checkpoint(c)
            await cp(c).save(guardado)
            return await accion(guardado.checkpoint_id)

        return asyncio.run(caso())

    return {
        # --- AlmacenMongo: estado, eventos, órdenes, reportes, snapshots -------------------------------
        "AlmacenMongo.crear_indices": (lambda c: a(c).crear_indices(), "ws"),
        "AlmacenMongo.obtener_estado": (lambda c: a(c).obtener_estado(c.alcance), "ws"),
        "AlmacenMongo.guardar_estado": (
            lambda c: a(c).guardar_estado(*(lambda e: (e, e.version))(a(c).obtener_estado(c.alcance))),
            "ws",
        ),
        "AlmacenMongo.registrar_evento": (
            lambda c: a(c).registrar_evento(a(c).eventos_desde(c.alcance, Direccion.remoto_a_local, 0)[0]),
            "ws",
        ),
        "AlmacenMongo.existe_evento": (lambda c: a(c).existe_evento(c.alcance, uuid.uuid4()), "ws"),
        "AlmacenMongo.eventos_desde": (
            lambda c: a(c).eventos_desde(c.alcance, Direccion.remoto_a_local, 0, 5),
            "ws",
        ),
        "AlmacenMongo.ultima_secuencia": (
            lambda c: a(c).ultima_secuencia(c.alcance, Direccion.remoto_a_local),
            "ws",
        ),
        "AlmacenMongo.registrar_auditoria": (
            lambda c: a(c).registrar_auditoria(nuevo(c.config["auditoria"])),
            "ws",
        ),
        "AlmacenMongo.registrar_telemetria": (
            lambda c: a(c).registrar_telemetria(nuevo(c.config["telemetria"])),
            "ws",
        ),
        "AlmacenMongo.consultar_telemetria": (
            lambda c: a(c).consultar_telemetria(
                TelemetryQueryEntrada(
                    org=ORG, workspace=WS, desde=AHORA - timedelta(days=1), hasta=AHORA + timedelta(days=1)
                )
            ),
            "ws",
        ),
        "AlmacenMongo.siguiente_numero_unidad": (lambda c: a(c).siguiente_numero_unidad(WS_ALCANCE), "ws"),
        "AlmacenMongo.reclamar_importacion": (
            lambda c: a(c).reclamar_importacion(WS_ALCANCE, REPO, "plan", "p-1", "0099-importada"),
            "ws",
        ),
        "AlmacenMongo.listar_estados": (
            lambda c: a(c).listar_estados(UnitListEntrada(alcance=WS_ALCANCE)),
            "ws",
        ),
        "AlmacenMongo.guardar_orden": (lambda c: a(c).guardar_orden(c.orden), "ws"),
        "AlmacenMongo.obtener_orden": (lambda c: a(c).obtener_orden(c.alcance, str(c.orden.id)), "ws"),
        "AlmacenMongo.reporte_aceptado": (
            lambda c: a(c).reporte_aceptado(c.alcance, c.orden.id, c.orden.secuencia),
            "ws",
        ),
        "AlmacenMongo.guardar_reporte": (lambda c: a(c).guardar_reporte(c.reporte), "ws"),
        "AlmacenMongo.guardar_snapshot": (lambda c: a(c).guardar_snapshot(c.snapshot), "ws"),
        "AlmacenMongo.obtener_snapshot": (
            lambda c: a(c).obtener_snapshot(c.alcance, str(c.snapshot.id)),
            "ws",
        ),
        "AlmacenMongo.borrar_snapshots_repositorio": (
            lambda c: a(c).borrar_snapshots_repositorio(c.repo),
            "ws",
        ),
        "AlmacenMongo.registrar_entrada": (
            lambda c: a(c).registrar_entrada(
                EntradaPendiente(c.alcance, "r-1", TipoEntrada.reporte, {}, AHORA)
            ),
            "ws",
        ),
        "AlmacenMongo.entradas_pendientes": (lambda c: a(c).entradas_pendientes(c.alcance), "ws"),
        "AlmacenMongo.consumir_entrada": (lambda c: a(c).consumir_entrada(c.alcance, "r-1"), "ws"),
        "AlmacenMongo.tomar_turno": (lambda c: a(c).tomar_turno(c.alcance, "yo", AHORA, 30), "ws"),
        "AlmacenMongo.soltar_turno": (lambda c: a(c).soltar_turno(c.alcance, "yo"), "ws"),
        # --- AlmacenMongo: configuración ---------------------------------------------------------------
        "AlmacenMongo.perfil": (lambda c: a(c).perfil(WS_ALCANCE, Perfil.estandar), "ws"),
        "AlmacenMongo.presupuesto": (lambda c: a(c).presupuesto(WS_ALCANCE), "ws"),
        "AlmacenMongo.vinculos": (lambda c: a(c).vinculos(ORG, WS), "ws"),
        "AlmacenMongo.vinculo": (lambda c: a(c).vinculo(c.repo), "ws"),
        "AlmacenMongo.asignaciones": (lambda c: a(c).asignaciones(ORG, 83125327, frozenset({7})), "org"),
        "AlmacenMongo.workspace": (lambda c: a(c).workspace(WS_ALCANCE), "ws"),
        "AlmacenMongo.proveedores_contexto": (lambda c: a(c).proveedores_contexto(WS_ALCANCE), "ws"),
        "AlmacenMongo.nodo_en_cache": (lambda c: a(c).nodo_en_cache(ORG, "hash", AHORA), "org"),
        "AlmacenMongo.guardar_nodo_en_cache": (
            lambda c: a(c).guardar_nodo_en_cache(ORG, "hash", {"valor": 2}, AHORA + timedelta(days=1)),
            "org",
        ),
        "AlmacenMongo.catalogo": (lambda c: a(c).catalogo(ORG), "org"),
        "AlmacenMongo.guardar_catalogo": (
            lambda c: a(c).guardar_catalogo(ORG, Proveedor.foundry, [c.config["modelo"]]),
            "org",
        ),
        "AlmacenMongo.guardar_configuracion": (
            lambda c: a(c).guardar_configuracion(
                [c.config[x] for x in ("perfil", "presupuesto", "workspace", "proveedor", "vinculo")]
                + [_rol(), _rol(None)]
            ),
            "ws",
        ),
        # --- AlmacenConsola -------------------------------------------------------------------------------
        "AlmacenConsola.organizaciones": (lambda c: k(c).organizaciones({ORG}), "org"),
        "AlmacenConsola.organizacion": (lambda c: k(c).organizacion(ORG), "org"),
        "AlmacenConsola.guardar_organizacion": (
            lambda c: k(c).guardar_organizacion(c.config["organizacion"], None),
            "org",
        ),
        "AlmacenConsola.workspaces": (lambda c: k(c).workspaces(ORG), "org"),
        "AlmacenConsola.workspace": (lambda c: k(c).workspace(ORG, WS), "ws"),
        "AlmacenConsola.guardar_workspace": (
            lambda c: k(c).guardar_workspace(c.config["workspace"], 1),
            "ws",
        ),
        "AlmacenConsola.roles": (lambda c: k(c).roles(ORG, WS, True), "org"),
        "AlmacenConsola.rol": (lambda c: k(c).rol(ORG, str(uuid.uuid4())), "org"),
        "AlmacenConsola.guardar_rol": (lambda c: k(c).guardar_rol(_rol()), "ws"),
        "AlmacenConsola.borrar_rol": (lambda c: k(c).borrar_rol(ORG, str(uuid.uuid4())), "org"),
        "AlmacenConsola.asignaciones": (lambda c: k(c).asignaciones(ORG, 83125327, frozenset({7})), "org"),
        "AlmacenConsola.asignaciones_de_sujeto": (
            lambda c: k(c).asignaciones_de_sujeto(83125327, frozenset({7})),
            "global",
        ),
        "AlmacenConsola.vinculos": (lambda c: k(c).vinculos(ORG, WS), "ws"),
        "AlmacenConsola.vinculo": (lambda c: k(c).vinculo(c.repo), "ws"),
        "AlmacenConsola.guardar_vinculo": (lambda c: k(c).guardar_vinculo(c.config["vinculo"], 1), "ws"),
        "AlmacenConsola.borrar_vinculo": (lambda c: k(c).borrar_vinculo(c.repo), "ws"),
        "AlmacenConsola.perfiles": (lambda c: k(c).perfiles(ORG, WS), "ws"),
        "AlmacenConsola.perfil": (lambda c: k(c).perfil(ORG, WS, "estandar"), "ws"),
        "AlmacenConsola.guardar_perfil": (lambda c: k(c).guardar_perfil(c.config["perfil"], 1), "ws"),
        "AlmacenConsola.presupuestos": (lambda c: k(c).presupuestos(ORG, WS), "ws"),
        "AlmacenConsola.presupuesto": (lambda c: k(c).presupuesto(ORG, WS), "ws"),
        "AlmacenConsola.guardar_presupuesto": (
            lambda c: k(c).guardar_presupuesto(c.config["presupuesto"], 1),
            "ws",
        ),
        "AlmacenConsola.proveedores_contexto": (lambda c: k(c).proveedores_contexto(ORG, WS), "ws"),
        "AlmacenConsola.proveedor_contexto": (
            lambda c: k(c).proveedor_contexto(ORG, WS, "gobernanza", "pce"),
            "ws",
        ),
        "AlmacenConsola.guardar_proveedor_contexto": (
            lambda c: k(c).guardar_proveedor_contexto(c.config["proveedor"], 1),
            "ws",
        ),
        "AlmacenConsola.borrar_proveedor_contexto": (
            lambda c: k(c).borrar_proveedor_contexto(ORG, WS, "gobernanza", "pce"),
            "ws",
        ),
        "AlmacenConsola.catalogo": (lambda c: k(c).catalogo(ORG), "org"),
        "AlmacenConsola.guardar_estado_catalogo": (
            lambda c: k(c).guardar_estado_catalogo(c.config["estado_lectura"]),
            "org",
        ),
        "AlmacenConsola.estados_catalogo": (lambda c: k(c).estados_catalogo(ORG), "org"),
        "AlmacenConsola.suscripciones": (lambda c: k(c).suscripciones(ORG), "org"),
        "AlmacenConsola.suscripcion": (lambda c: k(c).suscripcion(ORG, "foundry-eu"), "org"),
        "AlmacenConsola.guardar_suscripcion": (
            lambda c: k(c).guardar_suscripcion(c.config["suscripcion"], 1),
            "org",
        ),
        "AlmacenConsola.borrar_suscripcion": (lambda c: k(c).borrar_suscripcion(ORG, "foundry-eu"), "org"),
        # Perfiles de toda la organización (de cualquier workspace) que usan la suscripción.
        "AlmacenConsola.perfiles_con_suscripcion": (
            lambda c: k(c).perfiles_con_suscripcion(ORG, "foundry-eu"),
            "org",
        ),
        "AlmacenConsola.clave_suscripcion": (lambda c: k(c).clave_suscripcion(ORG, "foundry-eu"), "org"),
        "AlmacenConsola.guardar_clave_suscripcion": (
            lambda c: k(c).guardar_clave_suscripcion(ORG, "foundry-eu", "v1.00000000.otro"),
            "org",
        ),
        "AlmacenConsola.borrar_clave_suscripcion": (
            lambda c: k(c).borrar_clave_suscripcion(ORG, "foundry-eu"),
            "org",
        ),
        "AlmacenConsola.avisos_config": (lambda c: k(c).avisos_config(ORG), "org"),
        "AlmacenConsola.guardar_avisos_config": (
            lambda c: k(c).guardar_avisos_config(c.config["avisos"], 1),
            "org",
        ),
        # Recorre todas las organizaciones a propósito: lo barre el fondo.
        "AlmacenConsola.avisos_con_informe": (lambda c: k(c).avisos_con_informe(), "global"),
        "AlmacenConsola.secreto_aviso": (lambda c: k(c).secreto_aviso(ORG, "teams"), "org"),
        "AlmacenConsola.guardar_secreto_aviso": (
            lambda c: k(c).guardar_secreto_aviso(ORG, "teams", "v1.00000000.otro"),
            "org",
        ),
        "AlmacenConsola.borrar_secreto_aviso": (lambda c: k(c).borrar_secreto_aviso(ORG, "teams"), "org"),
        "AlmacenConsola.registrar_evento_aviso": (
            lambda c: k(c).registrar_evento_aviso(ORG, "clave-nueva", {"tipo": "gate-escalado"}, AHORA),
            "org",
        ),
        "AlmacenConsola.eventos_aviso": (
            lambda c: k(c).eventos_aviso(ORG, AHORA - timedelta(days=7), AHORA),
            "org",
        ),
        "AlmacenConsola.reservar_envio": (
            lambda c: k(c).reservar_envio(ORG, "clave", "correo", AHORA),
            "org",
        ),
        "AlmacenConsola.liberar_envio": (lambda c: k(c).liberar_envio(ORG, "clave", "correo"), "org"),
        "AlmacenConsola.envios_desde": (lambda c: k(c).envios_desde(ORG, AHORA - timedelta(hours=1)), "org"),
        "AlmacenConsola.registrar_historial_aviso": (
            lambda c: k(c).registrar_historial_aviso(ORG, {"tipo": "prueba"}, AHORA),
            "org",
        ),
        "AlmacenConsola.historial_avisos": (lambda c: k(c).historial_avisos(ORG), "org"),
        "AlmacenConsola.registrar_auditoria": (
            lambda c: k(c).registrar_auditoria(nuevo(c.config["auditoria"])),
            "ws",
        ),
        "AlmacenConsola.auditoria": (lambda c: k(c).auditoria(ORG, WS, evento="cambio-configuracion"), "ws"),
        "AlmacenConsola.unidades": (lambda c: k(c).unidades(ORG, WS), "ws"),
        "AlmacenConsola.ordenes": (lambda c: k(c).ordenes(c.alcance), "ws"),
        "AlmacenConsola.reportes": (lambda c: k(c).reportes(c.alcance), "ws"),
        "AlmacenConsola.snapshot": (lambda c: k(c).snapshot(c.alcance, str(c.snapshot.id)), "ws"),
        # --- CheckpointsMongo ---------------------------------------------------------------------------
        "CheckpointsMongo.save": (lambda c: asyncio.run(cp(c).save(checkpoint(c))), "ws"),
        "CheckpointsMongo.load": (lambda c: con_checkpoint(c, cp(c).load), "ws"),
        "CheckpointsMongo.list_checkpoints": (
            lambda c: asyncio.run(cp(c).list_checkpoints(workflow_name=wf(c))),
            "ws",
        ),
        "CheckpointsMongo.delete": (lambda c: con_checkpoint(c, cp(c).delete), "ws"),
        "CheckpointsMongo.get_latest": (lambda c: asyncio.run(cp(c).get_latest(workflow_name=wf(c))), "ws"),
        "CheckpointsMongo.list_checkpoint_ids": (
            lambda c: asyncio.run(cp(c).list_checkpoint_ids(workflow_name=wf(c))),
            "ws",
        ),
        # --- RevocadosMongo --------------------------------------------------------------------------------
        "RevocadosMongo.revocar": (lambda c: c.revocados.revocar("sid-1", AHORA), "global"),
        "RevocadosMongo.revocada": (lambda c: c.revocados.revocada("sid-1"), "global"),
    }


CLASES = {
    "AlmacenMongo": AlmacenMongo,
    "AlmacenConsola": AlmacenConsola,
    "CheckpointsMongo": CheckpointsMongo,
    "RevocadosMongo": RevocadosMongo,
}


def _publicos() -> set[str]:
    return {
        f"{nombre}.{metodo}"
        for nombre, clase in CLASES.items()
        for metodo, _ in inspect.getmembers(clase, inspect.isfunction)
        if not metodo.startswith("_")
    }


def test_cada_metodo_publico_tiene_receta():
    recetas = set(_recetas())
    assert _publicos() - recetas == set(), "métodos públicos sin receta: añade la receta y su regla"
    assert recetas - _publicos() == set(), "recetas de métodos que ya no existen"


@pytest.mark.parametrize("metodo", sorted(_recetas()))
def test_metodo_publico_consulta_con_alcance(metodo):
    contexto = _poblar()
    registro: list[tuple[str, str, Any]] = []
    espia = EspiaDb(contexto.db, registro)
    contexto.almacen = AlmacenMongo(espia, crear_indices=False)
    contexto.consola = AlmacenConsola(espia)
    contexto.checkpoints = CheckpointsMongo(espia)
    contexto.revocados = RevocadosMongo(espia)
    registro.clear()
    receta, regla = _recetas()[metodo]
    receta(contexto)
    assert revisar(registro, regla=regla) == []
    if regla != "global" and metodo not in SIN_CONSULTA:
        assert registro, "la receta no llegó a consultar: no prueba nada"


#: Solo insertan un documento nuevo (su alcance va en el documento) o crean índices.
SIN_CONSULTA = {
    "AlmacenMongo.crear_indices",
    "AlmacenMongo.registrar_auditoria",
    "AlmacenMongo.registrar_entrada",
    "AlmacenMongo.registrar_telemetria",
    "AlmacenConsola.registrar_auditoria",
    "AlmacenConsola.registrar_evento_aviso",
    "AlmacenConsola.reservar_envio",
    "AlmacenConsola.registrar_historial_aviso",
}


# --- aislamiento entre workspaces de una misma organización ---------------------------------------


def _ajena(alcance: AlcanceUnidad) -> AlcanceUnidad:
    return alcance.model_copy(update={"workspace": OTRO_WS})


def test_orden_reporte_y_snapshot_con_id_de_otro_workspace_no_pisan_ni_se_leen():
    c = _poblar()
    almacen = c.motor.n.almacen
    ajena = _ajena(c.alcance)
    almacen.guardar_snapshot(c.snapshot)
    antes = {
        "orden": almacen.obtener_orden(c.alcance, str(c.orden.id)),
        "snapshot": almacen.obtener_snapshot(c.alcance, str(c.snapshot.id)),
        "reportes": c.db.reportes.find_one({"_id": str(c.orden.id)}),
    }
    assert all(antes.values())

    with pytest.raises(DuplicateKeyError):
        almacen.guardar_orden(c.orden.model_copy(update={"unidad": ajena}))
    with pytest.raises(DuplicateKeyError):
        almacen.guardar_reporte(c.reporte.model_copy(update={"unidad": ajena}))
    with pytest.raises(DuplicateKeyError):
        almacen.guardar_snapshot(c.snapshot.model_copy(update={"unidad": ajena}))

    # El otro workspace no ve nada con esos ids, y lo de este queda intacto.
    assert almacen.obtener_orden(ajena, str(c.orden.id)) is None
    assert almacen.obtener_snapshot(ajena, str(c.snapshot.id)) is None
    assert almacen.reporte_aceptado(ajena, c.orden.id, c.orden.secuencia) is False
    assert almacen.obtener_orden(c.alcance, str(c.orden.id)) == antes["orden"]
    assert almacen.obtener_snapshot(c.alcance, str(c.snapshot.id)) == antes["snapshot"]
    assert c.db.reportes.find_one({"_id": str(c.orden.id)}) == antes["reportes"]
    assert almacen.reporte_aceptado(c.alcance, c.orden.id, c.orden.secuencia) is True


def test_un_evento_con_el_id_de_otro_workspace_no_es_un_reenvio():
    c = _poblar()
    almacen = c.motor.n.almacen
    evento = almacen.eventos_desde(c.alcance, Direccion.remoto_a_local, 0)[0]
    assert almacen.registrar_evento(evento) is False  # el reenvío de la misma unidad sigue siendo idempotente
    ajeno = evento.model_copy(update={"unidad": _ajena(c.alcance)})
    with pytest.raises(DuplicateKeyError):
        almacen.registrar_evento(ajeno)
    assert almacen.existe_evento(ajeno.unidad, evento.id) is False
    assert almacen.existe_evento(c.alcance, evento.id) is True


def test_una_asignacion_con_el_id_de_otra_no_la_pisa_y_borrar_filtra_por_su_workspace():
    c = _poblar()
    almacen, consola = c.motor.n.almacen, AlmacenConsola(c.db)
    propia = _rol(WS)
    almacen.guardar_configuracion([propia])
    with pytest.raises(DuplicateKeyError):
        almacen.guardar_configuracion([_rol(OTRO_WS, propia.id)])
    with pytest.raises(ConflictoVersion):
        consola.guardar_rol(_rol(OTRO_WS, propia.id))
    assert consola.rol(ORG, str(propia.id)).workspace == WS

    registro: list[tuple[str, str, Any]] = []
    consola_espia = AlmacenConsola(EspiaDb(c.db, registro))
    registro.clear()
    assert consola_espia.borrar_rol(ORG, str(propia.id)) is True
    borrado = next(f for _, m, f in registro if m == "delete_one")
    assert borrado == {"org": ORG, "workspace": WS, "_id": str(propia.id)}
    assert consola.rol(ORG, str(propia.id)) is None
    assert consola.borrar_rol(ORG, str(propia.id)) is False


def test_checkpoints_de_otra_unidad_no_se_leen_ni_se_borran_ni_se_pisan():
    c = _poblar()
    cps = CheckpointsMongo(c.db)
    propia, ajena = nombre_workflow(c.alcance), nombre_workflow(_ajena(c.alcance))

    async def caso():
        mio = WorkflowCheckpoint(workflow_name=propia, graph_signature_hash="h", state={"de": "mi"})
        await cps.save(mio)

        # Mismo id desde otra unidad: no pisa el checkpoint ajeno.
        usurpador = WorkflowCheckpoint(
            checkpoint_id=mio.checkpoint_id,
            workflow_name=ajena,
            graph_signature_hash="h",
            state={"de": "otro"},
        )
        with pytest.raises(DuplicateKeyError):
            await cps.save(usurpador)

        # load y delete llevan la unidad del último workflow que tocó este contexto.
        assert await cps.list_checkpoint_ids(workflow_name=ajena) == []
        with pytest.raises(WorkflowCheckpointException, match="No checkpoint found"):
            await cps.load(mio.checkpoint_id)
        assert await cps.delete(mio.checkpoint_id) is False
        assert (await cps.get_latest(workflow_name=propia)).state == {"de": "mi"}
        assert (await cps.load(mio.checkpoint_id)).state == {"de": "mi"}
        assert await cps.delete(mio.checkpoint_id) is True

    asyncio.run(caso())


def test_load_y_delete_sin_unidad_en_el_contexto_fallan_cerrado():
    c = _poblar()
    cps = CheckpointsMongo(c.db)
    guardado = c.db.checkpoints.find_one({})
    assert guardado is not None  # el motor ya dejó checkpoints de la unidad

    async def caso():
        # Un contexto nuevo no hereda la unidad de otro: no hay a qué acotar el id.
        with pytest.raises(WorkflowCheckpointException, match="sin unidad en el contexto"):
            await cps.load(guardado["_id"])
        with pytest.raises(WorkflowCheckpointException, match="sin unidad en el contexto"):
            await cps.delete(guardado["_id"])

    asyncio.run(caso())
    assert c.db.checkpoints.find_one({"_id": guardado["_id"]}) is not None


def test_telemetria_se_acota_al_workspace_y_sin_workspace_es_de_la_organizacion():
    c = _poblar()
    registro: list[tuple[str, str, Any]] = []
    almacen = AlmacenMongo(EspiaDb(c.db, registro), crear_indices=False)
    rango = {"desde": AHORA - timedelta(days=1), "hasta": AHORA + timedelta(days=1)}

    propia = almacen.consultar_telemetria(TelemetryQueryEntrada(org=ORG, workspace=WS, **rango))
    assert propia.filas[0].llamadas >= 1
    assert revisar(registro) == []
    ajena = almacen.consultar_telemetria(TelemetryQueryEntrada(org=ORG, workspace=OTRO_WS, **rango))
    assert sum(f.llamadas for f in ajena.filas) == 0  # mongomock da una fila en cero; Mongo, ninguna

    # El contrato admite la consulta de toda la organización (estadísticas): solo ahí basta la org.
    registro.clear()
    toda = almacen.consultar_telemetria(TelemetryQueryEntrada(org=ORG, **rango))
    assert toda.filas[0].llamadas == propia.filas[0].llamadas
    assert revisar(registro) != [] and revisar(registro, regla="org") == []
