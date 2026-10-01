"""Consola: lo que la API devuelve al navegador (M2).

Regla de fondo (docs/consola.md): la consola nunca muestra código. Estas pruebas la comprueban en la
API, no solo en la SPA: ninguna respuesta de ``/consola/api`` lleva instrucciones, plantilla, contexto,
comando de validación, evidencia ni propuesta. ``/v1`` y MCP (el arnés sí necesita la orden completa)
no cambian.
"""

from __future__ import annotations

import json

import pytest
from apoyo_motor import JULIAN, ORG, WS, avanzar, entrada_start
from railspec.contracts.comun import GateFase
from railspec.contracts.hallazgos import Cita, Hallazgo
from railspec.contracts.repositorio import Rol
from railspec.server.api import AutorizadorRoles, Registro
from railspec.server.api.superficies import aplicacion
from railspec.server.consola import montar_consola
from railspec.server.consola.vistas import TOOLS_CONSOLA, salida_tool
from railspec.server.portabilidad import manejadores as manejadores_portabilidad
from test_consola import ANA_ID, CSRF, Montaje, _unidad_cerrada, asignar, correr

CODIGO = "def firmar():\n    return 'CODIGO-EN-EVIDENCIA-9f3a'"
PROPUESTA = "reemplazar por CODIGO-EN-PROPUESTA-4b7c"
PROHIBIDOS = ("CODIGO-EN-EVIDENCIA-9f3a", "CODIGO-EN-PROPUESTA-4b7c")


def _con_hallazgo(m: Montaje, alcance) -> None:
    """Deja en el gate de spec un hallazgo baja sin resolver cuya evidencia y propuesta llevan código."""

    estado = m.almacen.obtener_estado(alcance)
    gate = estado.gates[GateFase.spec]
    h = Hallazgo(
        id="H-1",
        gate=GateFase.spec,
        lente="seguridad",
        severidad="baja",
        criterio="CA-01",
        titulo="Falta validar la firma",
        cita=Cita(seccion="Alcance"),
        evidencia=CODIGO,
        propuesta=PROPUESTA,
    )
    gates = {**estado.gates, GateFase.spec: gate.model_copy(update={"hallazgos": [h]})}
    m.almacen.guardar_estado(estado.model_copy(update={"gates": gates}), estado.version)


def _sin_codigo(texto: str) -> None:
    for marca in PROHIBIDOS:
        assert marca not in texto, f"la respuesta de la consola lleva código: {marca}"


# --- M2: estado hacia el navegador -----------------------------------------------------------------


def test_detalle_de_unidad_sin_evidencia_ni_propuesta():
    async def caso():
        m = Montaje()
        alcance = await _unidad_cerrada(m)
        _con_hallazgo(m, alcance)
        asignar(m.almacen, Rol.lector, ANA_ID)
        async with m.cliente("tk-ana") as c:
            r = await c.get(f"/consola/api/orgs/{ORG}/workspaces/{WS}/unidades/{alcance.unidad}")
            assert r.status_code == 200
            _sin_codigo(r.text)
            h = r.json()["estado"]["gates"]["spec"]["hallazgos"][0]
            # Lo que la SPA pinta sigue ahí; evidencia y propuesta no.
            assert h["id"] == "H-1" and h["titulo"] == "Falta validar la firma"
            assert h["lente"] == "seguridad" and h["cita"]["seccion"] == "Alcance"
            assert h["severidad"] == "baja" and h["criterio"] == "CA-01" and h["refutado"] is False
            assert "evidencia" not in h and "propuesta" not in h
            estado = r.json()["estado"]
            assert "comando_validacion" not in estado
            assert estado["fase"] == "done" and estado["gates"]["spec"]["veredicto"] == "aprobado"

    correr(caso())


def test_unit_status_por_la_consola_no_devuelve_la_orden_completa():
    async def caso():
        m = Montaje()
        salida = await m.motor.start(entrada_start(), JULIAN)
        alcance = salida.estado.unidad
        av = await avanzar(m.motor, alcance)
        assert av.tipo == "orden"
        asignar(m.almacen, Rol.lector, ANA_ID)
        cuerpo = {"unidad": alcance.model_dump(mode="json", exclude_none=True)}
        async with m.cliente("tk-ana") as c:
            r = await c.post("/consola/api/tools/unit.status", json=cuerpo, headers=CSRF)
            assert r.status_code == 200, r.text
            vigente = r.json()["orden_vigente"]
            assert vigente["id"] == str(av.orden.id) and vigente["tipo"] == "redactar"
            assert vigente["criterios"] is not None and "alcance" in vigente
            for campo in ("instrucciones", "plantilla", "contexto", "comando_validacion"):
                assert campo not in vigente, campo
            assert av.orden.instrucciones[:60] not in r.text
            assert "comando_validacion" not in r.json()["estado"]
            # El alias MCP (con guion bajo) pasa por el mismo filtro.
            r = await c.post("/consola/api/tools/unit_status", json=cuerpo, headers=CSRF)
            assert r.status_code == 200 and "instrucciones" not in r.json()["orden_vigente"]
        # El arnés sí necesita la orden completa: /v1 no cambia.
        async with m.cliente() as c:
            r = await c.post("/v1/tools/unit.status", json=cuerpo, headers={"Authorization": "Bearer tk-ana"})
            assert r.status_code == 200, r.text
            completa = r.json()["orden_vigente"]
            assert completa["instrucciones"] == av.orden.instrucciones and "contexto" in completa

    correr(caso())


def test_tools_que_devuelven_estado_salen_filtradas():
    async def caso():
        m = Montaje()
        alcance = await _unidad_cerrada(m)
        _con_hallazgo(m, alcance)
        asignar(m.almacen, Rol.desarrollador, ANA_ID)
        cuerpo = {
            "unidad": alcance.model_dump(mode="json", exclude_none=True),
            "especificacion_viva": "specs/pdf.md",
        }
        async with m.cliente("tk-ana") as c:
            r = await c.post("/consola/api/tools/unit.integrate", json=cuerpo, headers=CSRF)
            assert r.status_code == 200, r.text
            _sin_codigo(r.text)
            estado = r.json()["estado"]
            assert estado["integracion"]["especificacion_viva"] == "specs/pdf.md"
            assert "evidencia" not in estado["gates"]["spec"]["hallazgos"][0]
            assert set(r.json()) == {"version_contrato", "estado"}
        # Por /v1 (el arnés) el estado sigue completo.
        async with m.cliente() as c:
            r = await c.post(
                "/v1/tools/unit.status",
                json={"unidad": alcance.model_dump(mode="json", exclude_none=True)},
                headers={"Authorization": "Bearer tk-ana"},
            )
            assert all(marca in r.text for marca in PROHIBIDOS)

    correr(caso())


def test_salida_de_cada_tool_de_consola_pasa_por_el_filtro():
    async def caso():
        m = Montaje()
        alcance = await _unidad_cerrada(m)
        _con_hallazgo(m, alcance)
        estado = m.almacen.obtener_estado(alcance).model_dump(mode="json")
        assert PROHIBIDOS[0] in json.dumps(estado)  # el estado del motor sí lo guarda
        for nombre in ("unit.approve", "unit.integrate", "unit.set_mode"):
            _sin_codigo(json.dumps(salida_tool(nombre, {"estado": estado})))
        inicio = salida_tool(
            "unit.start", {"estado": estado, "version_contrato_negociada": "1.4", "extra": "no"}
        )
        _sin_codigo(json.dumps(inicio))
        assert set(inicio) == {"estado", "version_contrato_negociada"}  # nada fuera de lista
        # Una tool sin filtro declarado no sale: falla cerrado.
        with pytest.raises(KeyError):
            salida_tool("unit.export", {"paquete": {}})

    correr(caso())


def _con_portabilidad(m: Montaje) -> None:
    """Como en producción (app.py): el registro también lleva unit.import y unit.export."""

    registro = Registro.del_motor(m.motor, AutorizadorRoles(m.almacen), manejadores_portabilidad(m.motor))
    m.ctx.registro = registro
    m.app = aplicacion(registro, m.ctx.identidad)
    montar_consola(m.app, m.ctx)


def test_unit_export_e_import_no_estan_en_la_consola():
    async def caso():
        m = Montaje()
        _con_portabilidad(m)
        alcance = await _unidad_cerrada(m)
        asignar(m.almacen, Rol.lector, ANA_ID)
        cuerpo = {"unidad": alcance.model_dump(mode="json", exclude_none=True)}
        async with m.cliente("tk-ana") as c:
            nombres = {t["name"] for t in (await c.get("/consola/api/tools")).json()["tools"]}
            assert nombres <= set(TOOLS_CONSOLA)
            assert {"unit.list", "unit.status", "unit.approve", "unit.integrate"} <= nombres
            for fuera in ("unit.export", "unit.import", "insumo.get", "graph.index", "unit.report"):
                assert fuera not in nombres
            for nombre in ("unit.export", "unit_export", "unit.import", "insumo.get"):
                r = await c.post(f"/consola/api/tools/{nombre}", json=cuerpo, headers=CSRF)
                assert r.status_code == 403, (nombre, r.text)
                assert r.json()["codigo"] == "fuera-de-alcance"
                assert "paquete" not in r.text
            r = await c.post("/consola/api/tools/no.existe", json=cuerpo, headers=CSRF)
            assert r.status_code == 404 and r.json()["codigo"] == "no-encontrado"
        # Por /v1 el arnés conserva unit.export con rol lector (recomendación: exigir más rol).
        async with m.cliente() as c:
            r = await c.post("/v1/tools/unit.export", json=cuerpo, headers={"Authorization": "Bearer tk-ana"})
            assert r.status_code == 200 and "paquete" in r.json()

    correr(caso())


def test_las_tools_de_consola_son_las_documentadas():
    assert set(TOOLS_CONSOLA) == {
        "unit.list",
        "unit.status",
        "unit.start",
        "unit.approve",
        "unit.integrate",
        "unit.set_mode",
        "telemetry.query",
        "graph.query",
    }
