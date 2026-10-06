"""API de suscripciones de la consola: permisos, claves que no salen, descubrimiento, perfiles, auditoría."""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from apoyo_motor import JULIAN, ORG, WS, WS_ALCANCE, construir, critico_sin_hallazgos, entrada_start
from railspec.contracts.comun import NivelCodigo, Perfil, Proveedor
from railspec.contracts.repositorio import EventoAuditoria, Rol
from railspec.contracts.tools import CodigoError
from railspec.server.motor.motor import ErrorNegocio
from railspec.server.motor.perfiles import perfil_por_defecto
from railspec.server.proveedores import Proveedores
from railspec.server.proveedores.falso import ProveedorGuionado
from railspec.server.proveedores.suscripciones import ServicioSuscripciones
from test_consola import ANA_ID, CSRF, LUIS_ID, Montaje, asignar
from test_suscripciones import (
    AUDITORIA,
    CLAVE,
    CUERPO_FOUNDRY,
    ENDPOINT,
    _cifrador,
    _foundry,
)

RUTA = f"/consola/api/orgs/{ORG}/suscripciones"
CUERPO = {
    "proveedor": "foundry",
    "nombre": "Foundry UE",
    "endpoint": ENDPOINT,
    "proyecto": "railspec",
    "region": "swedencentral",
    "zona_datos": "eu",
    "clave": CLAVE,
}


def correr(coro):
    return asyncio.run(coro)


def _montaje(cifrador="si", transporte=None, **kw):
    m = Montaje()
    transporte = transporte or httpx.MockTransport(lambda p: httpx.Response(200, json=CUERPO_FOUNDRY))
    m.ctx.suscripciones = ServicioSuscripciones(
        m.ctx.datos, _cifrador() if cifrador == "si" else cifrador, transporte=transporte, **kw
    )
    return m


def _todo(m) -> str:
    db = m.almacen.db
    return json.dumps({n: list(db[n].find({})) for n in db.list_collection_names()}, default=str)


def test_flujo_completo_registrar_descubrir_elegir_y_asociar_a_un_perfil(caplog):
    caplog.set_level(logging.DEBUG)
    m = _montaje()

    async def caso():
        async with m.cliente("tk-julian") as c:
            r = await c.put(f"{RUTA}/foundry-eu", json=CUERPO, headers=CSRF)
            assert r.status_code == 200, r.text
            s = r.json()
            assert (s["clave_configurada"], s["version"], s["modelos"], s["perfiles"]) == (True, 1, [], [])
            assert CLAVE not in r.text and "clave" not in s
            # Descubrir
            r = await c.post(f"{RUTA}/foundry-eu/descubrir", headers=CSRF)
            assert r.status_code == 200, r.text
            cuerpo = r.json()
            assert (cuerpo["resultado"], cuerpo["modelos"]) == ("ok", 4)
            modelos = {x["clave"]: x for x in cuerpo["suscripcion"]["modelos"]}
            assert modelos["opus-dz"]["region"] == "zona-eu" and modelos["sonnet-gl"]["region"] == "global"
            assert modelos["raro"]["region"] is None  # la región se muestra; ya no clasifica a los modelos
            assert not any("restringible" in m for m in modelos.values())
            assert modelos["opus-dz"]["hosting"] == "azure" and not modelos["opus-dz"]["seleccionado"]
            version = cuerpo["suscripcion"]["version"]
            # Elegir los modelos disponibles
            r = await c.put(
                f"{RUTA}/foundry-eu/modelos",
                json={"seleccionados": ["opus-dz", "haiku-st"], "version": version},
                headers=CSRF,
            )
            assert r.status_code == 200, r.text
            assert [x["clave"] for x in r.json()["modelos"] if x["seleccionado"]] == ["haiku-st", "opus-dz"]
            # El perfil se asocia a la suscripción y solo puede usar sus modelos elegidos.
            perfil = {
                "suscripcion": "foundry-eu",
                "roles": {
                    "redactor": {
                        "modelo": {"foundry": "opus-dz"},
                        "effort": "high",
                        "structured_outputs": True,
                    }
                },
                "gate": {"bajo": {"criticos": 1, "iteraciones": 1, "adversarial": False}},
                "exploradores": {"bajo": 1},
            }
            r = await c.put(f"/consola/api/orgs/{ORG}/perfiles/estandar", json=perfil, headers=CSRF)
            assert r.status_code == 200, r.text
            assert r.json()["perfil"]["suscripcion"] == "foundry-eu" and r.json()["avisos"] == []
            lista = (await c.get(f"/consola/api/orgs/{ORG}/perfiles")).json()
            assert [p["suscripcion"] for p in lista] == ["foundry-eu"]
            # Ahora la suscripción dice quién la usa y no se deja borrar.
            s = (await c.get(f"{RUTA}/foundry-eu")).json()
            assert s["perfiles"] == [{"nombre": "estandar", "workspace": None}]
            r = await c.delete(f"{RUTA}/foundry-eu", headers=CSRF)
            assert (
                r.status_code == 409
                and r.json()["codigo"] == "suscripcion-en-uso"
                and "estandar" in r.json()["detalle"]
            )
            # Un modelo descubierto pero no elegido no vale en el perfil.
            r = await c.put(
                f"/consola/api/orgs/{ORG}/perfiles/estandar",
                json={**perfil, "version": 1, "roles": {"redactor": {"modelo": {"foundry": "sonnet-gl"}}}},
                headers=CSRF,
            )
            assert (
                r.status_code == 422
                and "sonnet-gl no está entre los modelos disponibles" in r.json()["detalle"]
            )
            # Para borrarla hay que cambiar la suscripción del perfil o borrar el perfil.
            return c

    correr(caso())
    assert CLAVE not in _todo(m)
    assert CLAVE not in caplog.text


def test_la_clave_nunca_sale_por_ninguna_respuesta_ni_por_los_errores_de_validacion():
    m = _montaje()

    async def caso():
        async with m.cliente("tk-julian") as c:
            respuestas = [
                await c.put(f"{RUTA}/foundry-eu", json=CUERPO, headers=CSRF),
                await c.get(RUTA),
                await c.get(f"{RUTA}/foundry-eu"),
                await c.post(f"{RUTA}/foundry-eu/descubrir", headers=CSRF),
                # Entradas inválidas: el 422 no repite lo recibido.
                await c.put(f"{RUTA}/otra", json=CUERPO | {"nombre": ""}, headers=CSRF),
                await c.put(f"{RUTA}/otra", json=CUERPO | {"proveedor": "nube"}, headers=CSRF),
                await c.put(f"{RUTA}/otra", json=CUERPO | {"extra": CLAVE}, headers=CSRF),
                await c.put(
                    f"{RUTA}/otra", json=CUERPO | {"endpoint": "https://evil.example.com"}, headers=CSRF
                ),
                await c.put(f"{RUTA}/otra", json=CUERPO | {"clave": ["a", CLAVE]}, headers=CSRF),
                await c.put(f"{RUTA}/otra", json=CUERPO | {"clave": CLAVE * 100}, headers=CSRF),
            ]
            assert [r.status_code for r in respuestas] == [200, 200, 200, 200, 422, 422, 422, 422, 422, 422]
            assert all(CLAVE not in r.text for r in respuestas), [
                r.text for r in respuestas if CLAVE in r.text
            ]
            lista = respuestas[1].json()
            assert (
                lista["cifrado"]["disponible"] is True
                and lista["cifrado"]["variable"] == "RAILSPEC_CLAVE_MAESTRA"
            )
            assert all("clave" not in suscripcion for suscripcion in lista["suscripciones"])

    correr(caso())
    assert CLAVE not in _todo(m)


def test_solo_el_administrador_de_la_organizacion_escribe_y_cualquier_rol_lee():
    m = _montaje()
    asignar(m.almacen, Rol.org_admin, ANA_ID, workspace=None)
    asignar(m.almacen, Rol.workspace_admin, LUIS_ID)

    async def caso():
        async with m.cliente("tk-julian") as c:  # administrador de la plataforma
            assert (await c.put(f"{RUTA}/foundry-eu", json=CUERPO, headers=CSRF)).status_code == 200
        async with m.cliente("tk-luis") as c:  # workspace-admin: lee, no escribe
            assert (await c.get(RUTA)).status_code == 200 and (
                await c.get(f"{RUTA}/foundry-eu")
            ).status_code == 200
            for r in (
                await c.put(f"{RUTA}/otra", json=CUERPO, headers=CSRF),
                await c.put(f"{RUTA}/foundry-eu", json=CUERPO | {"version": 1}, headers=CSRF),
                await c.delete(f"{RUTA}/foundry-eu", headers=CSRF),
                await c.post(f"{RUTA}/foundry-eu/descubrir", headers=CSRF),
                await c.put(
                    f"{RUTA}/foundry-eu/modelos", json={"seleccionados": [], "version": 1}, headers=CSRF
                ),
                await c.post(
                    f"{RUTA}/foundry-eu/modelos",
                    json={"modelo": "m", "despliegue": "d", "sku": "Standard", "version": 1},
                    headers=CSRF,
                ),
                await c.delete(f"{RUTA}/foundry-eu/modelos/d", params={"version": 1}, headers=CSRF),
            ):
                assert r.status_code == 403, r.text
        async with m.cliente("tk-ana") as c:  # org-admin
            assert (
                await c.put(f"{RUTA}/otra", json=CUERPO | {"nombre": "Otra"}, headers=CSRF)
            ).status_code == 200
            assert (await c.delete(f"{RUTA}/otra", headers=CSRF)).status_code == 204
            assert (await c.delete(f"{RUTA}/otra", headers=CSRF)).status_code == 404

    correr(caso())
    # Sin rol alguno en la organización ni siquiera se lee la lista.
    m2 = _montaje()

    async def sin_rol():
        async with m2.cliente("tk-luis") as c:
            assert (await c.get(RUTA)).status_code == 403

    correr(sin_rol())


def test_sin_clave_maestra_la_lista_lo_dice_y_guardar_una_clave_da_503():
    m = _montaje(cifrador=None)

    async def caso():
        async with m.cliente("tk-julian") as c:
            lista = (await c.get(RUTA)).json()
            assert lista["cifrado"] == {"disponible": False, "variable": "RAILSPEC_CLAVE_MAESTRA"}
            r = await c.put(f"{RUTA}/foundry-eu", json=CUERPO, headers=CSRF)
            assert r.status_code == 503 and r.json()["codigo"] == "clave-maestra-ausente"
            assert "RAILSPEC_CLAVE_MAESTRA" in r.json()["detalle"] and CLAVE not in r.text
            # La identidad del servidor no necesita clave maestra.
            sin_clave = {k: v for k, v in CUERPO.items() if k != "clave"} | {
                "autenticacion": "identidad-servidor"
            }
            assert (await c.put(f"{RUTA}/foundry-eu", json=sin_clave, headers=CSRF)).status_code == 200

    correr(caso())


def test_un_servidor_sin_servicio_de_suscripciones_responde_409():
    m = Montaje()  # sin suscripciones

    async def caso():
        async with m.cliente("tk-julian") as c:
            assert (await c.get(RUTA)).status_code == 409

    correr(caso())


def test_descubrir_con_error_responde_502_registra_el_resultado_y_conserva_la_eleccion():
    estado = {"codigo": 200}
    m = _montaje(
        transporte=httpx.MockTransport(
            lambda p: httpx.Response(
                estado["codigo"], json=CUERPO_FOUNDRY if estado["codigo"] == 200 else {"e": CLAVE}
            )
        )
    )

    async def caso():
        async with m.cliente("tk-julian") as c:
            await c.put(f"{RUTA}/foundry-eu", json=CUERPO, headers=CSRF)
            v = (await c.post(f"{RUTA}/foundry-eu/descubrir", headers=CSRF)).json()["suscripcion"]["version"]
            await c.put(
                f"{RUTA}/foundry-eu/modelos", json={"seleccionados": ["opus-dz"], "version": v}, headers=CSRF
            )
            estado["codigo"] = 403
            r = await c.post(f"{RUTA}/foundry-eu/descubrir", headers=CSRF)
            assert r.status_code == 502 and CLAVE not in r.text
            cuerpo = r.json()
            assert (cuerpo["resultado"], cuerpo["codigo"]) == ("error", "permiso") and "HTTP 403" in cuerpo[
                "detalle"
            ]
            s = (await c.get(f"{RUTA}/foundry-eu")).json()
            assert (
                s["ultima_lectura"]["resultado"] == "error"
                and s["ultima_lectura"]["error_codigo"] == "permiso"
            )
            assert [x["clave"] for x in s["modelos"] if x["seleccionado"]] == ["opus-dz"]

    correr(caso())
    detalles = [
        r["detalle"] for r in m.almacen.db.auditoria.find({}) if r["detalle"].get("entidad") == "suscripcion"
    ]
    descubrir = [d for d in detalles if d["accion"] == "descubrir"]
    assert [(d["resultado"], d.get("error")) for d in descubrir] == [("ok", None), ("error", "permiso")]


def test_declarar_y_retirar_modelos_por_la_api_y_conflicto_de_version():
    m = _montaje()

    async def caso():
        async with m.cliente("tk-julian") as c:
            s = (await c.put(f"{RUTA}/foundry-eu", json=CUERPO, headers=CSRF)).json()
            declaracion = {
                "modelo": "claude-opus-5-5",
                "despliegue": "mi-opus",
                "sku": "DataZoneStandard",
                "efforts": ["high"],
                "contexto": 400000,
                "version": s["version"],
            }
            r = await c.post(f"{RUTA}/foundry-eu/modelos", json=declaracion, headers=CSRF)
            assert r.status_code == 200, r.text
            (modelo,) = r.json()["modelos"]
            assert (modelo["origen"], modelo["seleccionado"], modelo["region"]) == (
                "declarado",
                True,
                "zona-eu",
            )
            # Otra persona cambió la suscripción antes: 409 con la versión actual.
            r = await c.post(f"{RUTA}/foundry-eu/modelos", json=declaracion, headers=CSRF)
            assert r.status_code == 409 and r.json()["version_actual"] == s["version"] + 1
            r = await c.post(
                f"{RUTA}/foundry-eu/modelos",
                json=declaracion | {"sku": "no es sku", "version": 2},
                headers=CSRF,
            )
            assert r.status_code == 422 and r.json()["codigo"] == "sku-invalido"
            r = await c.delete(f"{RUTA}/foundry-eu/modelos/mi-opus", params={"version": 2}, headers=CSRF)
            assert r.status_code == 200 and r.json()["modelos"] == []

    correr(caso())


def test_el_perfil_exige_suscripcion_si_la_organizacion_tiene_alguna_y_valida_capacidades():
    m = _montaje()
    base = {
        "roles": {"redactor": {"modelo": {"foundry": "gpt-5"}, "effort": "high", "structured_outputs": True}},
        "gate": {"bajo": {"criticos": 1, "iteraciones": 1, "adversarial": False}},
        "exploradores": {"bajo": 1},
    }

    async def caso():
        async with m.cliente("tk-julian") as c:
            ruta = f"/consola/api/orgs/{ORG}/perfiles/estandar"
            # Sin suscripciones: el comportamiento anterior (catálogo del servidor).
            assert (await c.put(ruta, json=base, headers=CSRF)).status_code == 200
            await c.put(f"{RUTA}/foundry-eu", json=CUERPO, headers=CSRF)
            r = await c.put(ruta, json=base | {"version": 1}, headers=CSRF)
            assert r.status_code == 422 and "elige la suscripción" in r.json()["detalle"]
            r = await c.put(ruta, json=base | {"version": 1, "suscripcion": "fantasma"}, headers=CSRF)
            assert r.status_code == 422 and "no existe" in r.json()["detalle"]
            v = (await c.post(f"{RUTA}/foundry-eu/descubrir", headers=CSRF)).json()["suscripcion"]["version"]
            await c.put(
                f"{RUTA}/foundry-eu/modelos",
                json={"seleccionados": ["opus-dz", "sonnet-gl"], "version": v},
                headers=CSRF,
            )
            propio = base | {"version": 1, "suscripcion": "foundry-eu"}
            # El perfil pide un modelo para foundry; el de la suscripción tiene que existir ahí.
            r = await c.put(ruta, json=propio, headers=CSRF)
            assert (
                r.status_code == 422 and "gpt-5 no está entre los modelos disponibles" in r.json()["detalle"]
            )
            r = await c.put(
                ruta,
                json=propio | {"roles": {"redactor": {"modelo": {"anthropic": "claude-opus-5-5"}}}},
                headers=CSRF,
            )
            assert r.status_code == 422 and "no da modelo para foundry" in r.json()["detalle"]
            ok = propio | {"roles": {"redactor": {"modelo": {"foundry": "sonnet-gl"}, "effort": "max"}}}
            r = await c.put(ruta, json=ok, headers=CSRF)
            assert r.status_code == 200, r.text
            # Global ya no da aviso: la región no restringe qué repositorios sirve el modelo.
            assert r.json()["avisos"] == []
            # Un perfil de workspace también tiene que elegir suscripción de la organización.
            r = await c.put(ruta, params={"workspace": WS}, json=base, headers=CSRF)
            assert r.status_code == 422

    correr(caso())


def test_el_cambio_de_suscripcion_de_un_perfil_queda_en_la_auditoria():
    m = _montaje()
    perfil = {
        "roles": {"redactor": {"modelo": {"foundry": "opus-dz"}, "structured_outputs": True}},
        "gate": {"bajo": {"criticos": 1, "iteraciones": 1, "adversarial": False}},
        "exploradores": {"bajo": 1},
        "suscripcion": "uno",
    }

    async def caso():
        async with m.cliente("tk-julian") as c:
            for id_ in ("uno", "dos"):
                await c.put(f"{RUTA}/{id_}", json=CUERPO | {"nombre": id_}, headers=CSRF)
                v = (await c.post(f"{RUTA}/{id_}/descubrir", headers=CSRF)).json()["suscripcion"]["version"]
                await c.put(
                    f"{RUTA}/{id_}/modelos", json={"seleccionados": ["opus-dz"], "version": v}, headers=CSRF
                )
            ruta = f"/consola/api/orgs/{ORG}/perfiles/estandar"
            assert (await c.put(ruta, json=perfil, headers=CSRF)).status_code == 200
            assert (
                await c.put(ruta, json=perfil | {"suscripcion": "dos", "version": 1}, headers=CSRF)
            ).status_code == 200

    correr(caso())
    auditados = [
        r["detalle"] for r in m.almacen.db.auditoria.find({}) if r["detalle"].get("entidad") == "perfil"
    ]
    assert [(d["suscripcion"], d.get("suscripcion_previa")) for d in auditados] == [
        ("uno", None),
        ("dos", "uno"),
    ]


def test_la_auditoria_de_cada_cambio_no_lleva_la_clave():
    m = _montaje()

    async def caso():
        async with m.cliente("tk-julian") as c:
            await c.put(f"{RUTA}/foundry-eu", json=CUERPO, headers=CSRF)
            await c.put(
                f"{RUTA}/foundry-eu",
                json=CUERPO | {"version": 1, "nombre": "Renombrada", "clave": None},
                headers=CSRF,
            )
            await c.put(
                f"{RUTA}/foundry-eu", json=CUERPO | {"version": 2, "clave": CLAVE + "-nueva"}, headers=CSRF
            )
            await c.delete(f"{RUTA}/foundry-eu", headers=CSRF)

    correr(caso())
    registros = [
        r
        for r in m.almacen.db.auditoria.find({"evento": EventoAuditoria.cambio_configuracion.value})
        if r["detalle"].get("entidad") == "suscripcion"
    ]
    acciones = [(r["detalle"]["accion"], r["detalle"].get("clave_escrita")) for r in registros]
    assert acciones == [("crear", True), ("editar", False), ("editar", True), ("borrar", None)]
    assert all(
        r["alcance"]["org"] == ORG and r["actor"]["login"] == "juliancardonagaleano" for r in registros
    )
    assert CLAVE not in json.dumps(registros, default=str) and CLAVE not in _todo(m)
    assert m.almacen.db.suscripciones_claves.count_documents({}) == 0  # borrar se llevó la clave cifrada


# --- el motor usa la suscripción del perfil -----------------------------------------------------


def _motor_con_suscripcion(nivel=NivelCodigo.restringido, zona_workspace=None, sku="DataZoneStandard"):
    motor, _ = construir(nivel=nivel)
    doble = ProveedorGuionado(critico_sin_hallazgos, region="swedencentral")
    almacen = motor.n.almacen
    from railspec.server.consola.almacen import AlmacenConsola

    servicio = ServicioSuscripciones(AlmacenConsola(almacen.db), _cifrador(), fabrica=lambda s, clave: doble)
    servicio.guardar(
        ORG,
        "foundry-eu",
        Proveedor.foundry,
        _foundry(),
        None,
        AUDITORIA,
    )
    for modelo in ("claude-opus-5-5", "claude-sonnet-5-5"):
        s = servicio.obtener(ORG, "foundry-eu")
        servicio.declarar(
            ORG,
            "foundry-eu",
            s.version,
            modelo=modelo,
            despliegue=modelo.replace("claude-", "").replace("-5-5", "-eu"),
            sku=sku,
            efforts=None,
            structured_outputs=None,
            contexto=None,
            auditoria=AUDITORIA,
        )
    motor.n.proveedores = Proveedores({}, suscripciones=servicio)
    perfil = perfil_por_defecto(WS_ALCANCE, Perfil.estandar).model_copy(update={"suscripcion": "foundry-eu"})
    almacen.guardar_configuracion([perfil])
    if zona_workspace:
        from railspec.contracts.repositorio import Workspace

        almacen.guardar_configuracion(
            [
                Workspace(
                    version=1,
                    auditoria=AUDITORIA,
                    alcance=WS_ALCANCE,
                    nombre="Certificados",
                    zona_datos_azure=zona_workspace,
                )
            ]
        )
    return motor, doble


def test_unit_start_con_perfil_asociado_a_suscripcion_valida_contra_sus_modelos_sin_mirar_zona_ni_nivel():
    async def caso():
        motor, _ = _motor_con_suscripcion(zona_workspace="eu")
        salida = await motor.start(entrada_start(), JULIAN)
        assert salida.estado.unidad.unidad
        # Un workspace de otra zona ya no impide servirlo: la zona es informativa.
        motor2, _ = _motor_con_suscripcion(zona_workspace="us")
        await motor2.start(entrada_start(), JULIAN)
        # Con SKU Global en un repositorio restringido tampoco: el nivel no restringe proveedores.
        motor3, _ = _motor_con_suscripcion(sku="GlobalStandard")
        await motor3.start(entrada_start(), JULIAN)
        motor4, _ = _motor_con_suscripcion(nivel=NivelCodigo.abierto, sku="GlobalStandard")
        await motor4.start(entrada_start(), JULIAN)
        # Lo que sí sigue validándose: que el perfil encuentre sus modelos en la suscripción.
        motor5, _ = _motor_con_suscripcion()
        motor5.n.proveedores.suscripciones._datos.borrar_suscripcion(ORG, "foundry-eu")
        with pytest.raises(ErrorNegocio) as exc:
            await motor5.start(entrada_start(), JULIAN)
        assert exc.value.codigo == CodigoError.perfil_insatisfacible

    correr(caso())


def test_el_gate_llama_al_despliegue_de_la_suscripcion_y_la_auditoria_la_nombra():
    from apoyo_motor import aprobar, avanzar, reporte

    async def caso():
        motor, doble = _motor_con_suscripcion(zona_workspace="eu")
        salida = await motor.start(entrada_start(), JULIAN)
        alcance = salida.estado.unidad
        for _ in range(6):
            av = await avanzar(motor, alcance)
            if av.tipo == "orden":
                await motor.report(reporte(av.orden), JULIAN)
            elif av.tipo == "checkpoint":
                await aprobar(motor, alcance, av.checkpoint.id)
            if doble.peticiones:
                break
        return motor, doble

    motor, doble = correr(caso())
    assert doble.peticiones, "el gate no llegó a llamar al modelo"
    p = doble.peticiones[0]
    assert (
        p.suscripcion == "foundry-eu" and p.despliegue in {"opus-eu", "sonnet-eu"} and p.region == "zona-eu"
    )
    llamadas = [
        r for r in motor.n.almacen.db.auditoria.find({"evento": EventoAuditoria.llamada_modelo.value})
    ]
    assert llamadas and all(r["detalle"]["suscripcion"] == "foundry-eu" for r in llamadas)
    assert all(r["region"] == "zona-eu" for r in llamadas)
    assert CLAVE not in json.dumps(llamadas, default=str)


def test_un_perfil_con_suscripcion_borrada_es_perfil_insatisfacible_y_no_cae_al_entorno():
    async def caso():
        motor, _ = _motor_con_suscripcion()
        # El servidor también tiene Foundry por entorno: no respalda a un perfil con suscripción.
        motor.n.proveedores._disponibles[Proveedor.foundry] = ProveedorGuionado(lambda p: None)
        motor.n.proveedores.suscripciones._datos.borrar_suscripcion(ORG, "foundry-eu")
        with pytest.raises(ErrorNegocio) as exc:
            await motor.start(entrada_start(), JULIAN)
        assert exc.value.codigo == CodigoError.perfil_insatisfacible and "foundry-eu" in exc.value.detalle

    correr(caso())


def test_el_chat_usa_la_suscripcion_del_perfil_por_defecto_del_workspace():
    from railspec.server.chat.servicio import ConfigChat, ServicioChat

    async def caso():
        for zona in ("eu", "us", None):  # la zona del workspace ya no cambia lo que sirve al chat
            motor, doble = _motor_con_suscripcion(zona_workspace=zona)
            servicio = ServicioChat(
                almacen=motor.n.almacen,
                chat=None,
                registro=None,
                autorizador=None,
                proveedores=motor.n.proveedores,
                config=ConfigChat(),
            )
            proveedor, modelo, extra = await servicio._elegir(ORG, {"modelos": None}, WS_ALCANCE)
            assert proveedor is doble and modelo == "claude-sonnet-5-5"
            assert extra == {"despliegue": "sonnet-eu", "region": "zona-eu"}

    correr(caso())
