"""Sincronización del catálogo de modelos: errores clasificados, estado por proveedor y consola.

Sin credenciales reales: las fuentes son dobles, o ``httpx.MockTransport`` para el parseo del proyecto de
Foundry (cuya forma de respuesta no está verificada contra un recurso real: aquí solo se prueba lo que ya
se asumía y que lo que no encaja se dice en vez de vaciar el catálogo).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from apoyo_motor import ORG
from railspec.contracts.comun import Effort, Proveedor
from railspec.contracts.repositorio import Capacidades, EventoAuditoria, Rol
from railspec.server.estado import almacen_en_memoria
from railspec.server.proveedores import Catalogo
from railspec.server.proveedores.catalogo import (
    LECTURA_MINIMA_S,
    EntradaCatalogo,
    ErrorCatalogo,
    FuenteFoundryDeclarada,
    FuenteFoundryProyecto,
    clasificar,
)
from test_consola import ANA_ID, CSRF, LUIS_ID, Montaje, asignar

SECRETO_URL = "https://interno.example/api/projects/p?key=SECRETO-DE-LA-URL"


def correr(coro):
    return asyncio.run(coro)


def _entrada(despliegue, proveedor=Proveedor.foundry, modelo="claude-opus-5-5"):
    return EntradaCatalogo(
        proveedor,
        modelo,
        despliegue if proveedor == Proveedor.foundry else None,
        "azure" if proveedor == Proveedor.foundry else "anthropic",
        "zona-us" if proveedor == Proveedor.foundry else None,
        Capacidades(efforts=list(Effort), structured_outputs=True, contexto_max_tokens=1_000_000),
    )


class Fuente:
    """Fuente de catálogo doble: devuelve ``entradas``, o lanza ``fallo``, o se cuelga (``colgar``)."""

    def __init__(
        self, entradas=(), fallo=None, proveedor=Proveedor.foundry, nombres=("proyecto",), colgar=False
    ):
        self.entradas, self.fallo, self.proveedor, self.nombres, self.colgar = (
            list(entradas),
            fallo,
            proveedor,
            nombres,
            colgar,
        )
        self.lecturas = 0

    async def leer(self):
        self.lecturas += 1
        if self.colgar:
            await asyncio.sleep(3600)
        if self.fallo is not None:
            raise self.fallo
        return list(self.entradas)


def _estado_http(codigo: int) -> httpx.HTTPStatusError:
    peticion = httpx.Request("GET", SECRETO_URL)
    return httpx.HTTPStatusError(
        f"{codigo} para {SECRETO_URL}", request=peticion, response=httpx.Response(codigo, request=peticion)
    )


# --- clasificación de errores -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "codigo"),
    [
        (_estado_http(401), "autenticacion"),
        (_estado_http(403), "permiso"),
        (_estado_http(404), "no-encontrado"),
        (_estado_http(429), "limite"),
        (_estado_http(503), "proveedor"),
        (_estado_http(418), "proveedor"),
        (SimpleNamespace(status_code=401), "autenticacion"),  # SDK de Anthropic / OpenAI
        (httpx.ConnectError(f"no conecta con {SECRETO_URL}"), "red"),
        (httpx.ReadTimeout(f"lento: {SECRETO_URL}"), "tiempo"),
        (TimeoutError(), "tiempo"),
        (ConnectionRefusedError(SECRETO_URL), "red"),
        (json.JSONDecodeError(f"no es json {SECRETO_URL}", "x", 0), "forma"),
        (type("ClientAuthenticationError", (Exception,), {})(f"sin token {SECRETO_URL}"), "autenticacion"),
        (RuntimeError(f"algo raro con {SECRETO_URL}"), "interno"),
    ],
)
def test_clasificar_da_un_codigo_estable_y_nunca_filtra_la_url(exc, codigo):
    if isinstance(exc, SimpleNamespace):
        exc = type("APIStatusError", (Exception,), {"status_code": exc.status_code})(SECRETO_URL)
    error = clasificar(exc, Proveedor.foundry)
    assert error.codigo == codigo
    assert "foundry" in error.detalle
    assert "SECRETO" not in error.detalle and "http" not in error.detalle.replace("HTTP ", "")
    assert error.a_dict() == {"codigo": codigo, "detalle": error.detalle}


def test_un_error_catalogo_pasa_tal_cual():
    propio = ErrorCatalogo("configuracion", "texto nuestro")
    assert clasificar(propio, Proveedor.anthropic) is propio


# --- Foundry por proyecto: lo que no encaja se dice, no vacía el catálogo ---------------------------


def _proyecto(cuerpo, estado=200):
    def responder(peticion: httpx.Request) -> httpx.Response:
        return httpx.Response(estado, json=cuerpo)

    return FuenteFoundryProyecto(
        "https://acme.services.ai.azure.com/api/projects/p",
        "k",
        "eastus2",
        "us",
        transporte=httpx.MockTransport(responder),
    )


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"data": [{"name": "opus", "modelName": "claude-opus-5-5"}]},  # otra clave de lista
        [{"name": "opus", "modelName": "claude-opus-5-5"}],  # sin objeto
        {"value": [{"deployment": "opus", "model": "claude-opus-5-5"}]},  # otros nombres de campo
        {"value": ["opus"]},
    ],
)
def test_foundry_con_otra_forma_de_respuesta_es_un_error_de_forma(cuerpo):
    with pytest.raises(ErrorCatalogo) as exc:
        correr(_proyecto(cuerpo).leer())
    assert exc.value.codigo == "forma" and "no verificada" in exc.value.detalle


def test_foundry_sin_despliegues_de_modelo_no_es_un_error():
    assert correr(_proyecto({"value": []}).leer()) == []
    # Solo conexiones u otros tipos: la forma se reconoce, simplemente no hay modelos.
    assert correr(_proyecto({"value": [{"name": "c", "type": "Connection"}]}).leer()) == []


def test_foundry_mantiene_el_parseo_de_siempre():
    cuerpo = {
        "value": [
            {"name": "opus", "modelName": "claude-opus-5-5", "sku": {"name": "DataZoneStandard"}},
            {"name": "c", "type": "Connection"},
        ]
    }
    (e,) = correr(_proyecto(cuerpo).leer())
    assert (e.despliegue, e.modelo, e.region) == ("opus", "claude-opus-5-5", "zona-us")


def test_foundry_sin_credencial_es_un_error_de_configuracion():
    fuente = FuenteFoundryProyecto("https://x/api/projects/p", None, None, None)
    with pytest.raises(ErrorCatalogo) as exc:
        correr(fuente.leer())
    assert exc.value.codigo == "configuracion"


def test_despliegues_declarados_mal_escritos_son_un_error_de_configuracion():
    with pytest.raises(ErrorCatalogo) as exc:
        correr(FuenteFoundryDeclarada("sin-igual", None, None).leer())
    assert exc.value.codigo == "configuracion" and "RAILSPEC_FOUNDRY_DESPLIEGUES" in exc.value.detalle
    with pytest.raises(ErrorCatalogo):
        correr(FuenteFoundryDeclarada('[{"despliegue": "a"}]', None, None).leer())  # falta modelo
    assert FuenteFoundryDeclarada.nombres == ("declarados",)


# --- Catalogo.sincronizar -----------------------------------------------------------------------


def _catalogo(*fuentes, **kw):
    almacen = almacen_en_memoria()
    estados = []
    reloj = [0.0]
    cat = Catalogo(
        fuentes,
        almacen,
        ttl_s=3600,
        monotono=lambda: reloj[0],
        registrar=estados.append,
        **kw,
    )
    return cat, almacen, estados, reloj


def test_sincronizar_lee_persiste_y_reutiliza_una_lectura_reciente():
    fuente = Fuente([_entrada("opus-dz"), _entrada("sonnet-dz", modelo="claude-sonnet-5-5")])
    cat, almacen, estados, reloj = _catalogo(fuente)
    (e,) = correr(cat.sincronizar("acme", por="ana"))
    assert (e.resultado, e.modelos, e.reutilizada, e.error, e.por, e.origen) == (
        "ok",
        2,
        False,
        None,
        "ana",
        "consola",
    )
    assert e.leido_en is not None and fuente.lecturas == 1
    assert [m.despliegue for m in almacen.catalogo("acme")] == ["opus-dz", "sonnet-dz"]
    # Otra pulsación enseguida no vuelve a llamar al proveedor.
    reloj[0] = LECTURA_MINIMA_S - 1
    (e2,) = correr(cat.sincronizar("acme", por="ana"))
    assert e2.reutilizada and e2.resultado == "ok" and fuente.lecturas == 1
    # Pasado el mínimo, vuelve a leer.
    reloj[0] = LECTURA_MINIMA_S + 1
    (e3,) = correr(cat.sincronizar("acme", por="ana"))
    assert not e3.reutilizada and fuente.lecturas == 2
    assert [x.reutilizada for x in estados] == [False, True, False]
    # Otra organización recibe el catálogo ya leído, sin llamar de nuevo.
    (e4,) = correr(cat.sincronizar("otra", por="luis"))
    assert e4.reutilizada and fuente.lecturas == 2 and len(almacen.catalogo("otra")) == 2


def test_un_fallo_conserva_el_catalogo_anterior_y_dice_por_que():
    fuente = Fuente([_entrada("opus-dz")])
    cat, almacen, _, reloj = _catalogo(fuente)
    correr(cat.sincronizar("acme"))
    fuente.fallo = _estado_http(401)
    reloj[0] = 1000
    (e,) = correr(cat.sincronizar("acme"))
    assert e.resultado == "error" and e.error.codigo == "autenticacion"
    assert e.modelos == 1 and e.leido_en is not None  # sigue rigiendo la lectura anterior
    assert "SECRETO" not in json.dumps(e.a_doc())
    assert [m.despliegue for m in almacen.catalogo("acme")] == ["opus-dz"]
    assert cat.buscar(Proveedor.foundry, "opus-dz")
    # Se recupera al volver a leer bien.
    fuente.fallo = None
    reloj[0] = 2000
    (e,) = correr(cat.sincronizar("acme"))
    assert e.resultado == "ok" and e.error is None


def test_un_fallo_sin_lectura_previa_ni_copia_guardada_deja_el_catalogo_vacio_con_su_error():
    cat, almacen, _, _ = _catalogo(Fuente(fallo=httpx.ConnectError("no")))
    (e,) = correr(cat.sincronizar("acme"))
    assert (e.resultado, e.modelos, e.leido_en, e.error.codigo) == ("error", 0, None, "red")
    assert almacen.catalogo("acme") == []


def test_un_fallo_tras_reinicio_usa_la_copia_de_mongo_y_lo_dice():
    almacen = almacen_en_memoria()
    primero = Catalogo([Fuente([_entrada("opus-dz")])], almacen)
    correr(primero.sincronizar("acme"))
    nuevo = Catalogo([Fuente(fallo=_estado_http(503))], almacen)
    (e,) = correr(nuevo.sincronizar("acme"))
    assert e.resultado == "error" and e.error.codigo == "proveedor" and e.modelos == 1
    assert [x.despliegue for x in nuevo.entradas()] == ["opus-dz"]


def test_una_lectura_que_se_cuelga_se_corta_con_error_de_tiempo():
    cat, _, _, _ = _catalogo(Fuente(colgar=True), tope_lectura_s=0.05)
    (e,) = correr(cat.sincronizar("acme"))
    assert e.resultado == "error" and e.error.codigo == "tiempo"


def test_sincronizar_un_proveedor_no_toca_a_los_demas():
    foundry = Fuente([_entrada("opus-dz")])
    anthropic = Fuente([_entrada(None, Proveedor.anthropic)], proveedor=Proveedor.anthropic, nombres=("api",))
    cat, _, _, _ = _catalogo(foundry, anthropic)
    (e,) = correr(cat.sincronizar("acme", [Proveedor.anthropic]))
    assert e.proveedor == Proveedor.anthropic and foundry.lecturas == 0 and anthropic.lecturas == 1
    assert [x.proveedor for x in correr(cat.sincronizar("acme"))] == [Proveedor.anthropic, Proveedor.foundry]
    assert cat.fuentes(Proveedor.foundry) == ["proyecto"] and cat.fuentes(Proveedor.anthropic) == ["api"]
    with pytest.raises(ValueError, match="sin fuente"):
        solo = Catalogo([foundry])
        correr(solo.sincronizar("acme", [Proveedor.anthropic]))


def test_el_refresco_automatico_registra_cada_lectura_una_vez_por_organizacion():
    fuente = Fuente([_entrada("opus-dz")])
    cat, _, estados, reloj = _catalogo(fuente)
    correr(cat.refrescar("acme"))
    correr(cat.refrescar("acme"))  # fresco: ni lee ni vuelve a registrar
    assert [(x.org, x.origen, x.resultado) for x in estados] == [("acme", "automatica", "ok")]
    correr(cat.refrescar("otra"))  # misma lectura, otra organización
    assert [x.org for x in estados] == ["acme", "otra"]
    reloj[0] = 4000
    fuente.fallo = _estado_http(500)
    correr(cat.refrescar("acme"))
    assert estados[-1].resultado == "error" and estados[-1].error.codigo == "proveedor"
    correr(cat.refrescar(None))  # sin organización no se registra nada
    assert len(estados) == 3


def test_si_registrar_falla_la_lectura_sigue_valiendo():
    def mal(_):
        raise RuntimeError("Mongo caído")

    cat = Catalogo([Fuente([_entrada("opus-dz")])], almacen_en_memoria(), registrar=mal)
    (e,) = correr(cat.sincronizar("acme"))
    assert e.resultado == "ok" and cat.buscar(Proveedor.foundry, "opus-dz")


# --- API de la consola ---------------------------------------------------------------------------

RUTA = f"/consola/api/orgs/{ORG}/catalogo"


def _montaje(*fuentes):
    m = Montaje()
    m.reloj_s = 0.0
    m.ctx.catalogo = Catalogo(
        fuentes, m.almacen, registrar=m.ctx.datos.guardar_estado_catalogo, monotono=lambda: m.reloj_s
    )
    return m


def test_estado_antes_y_despues_de_sincronizar():
    foundry = Fuente([_entrada("opus-dz")])
    m = _montaje(foundry)

    async def caso():
        async with m.cliente("tk-julian") as c:
            antes = (await c.get(f"{RUTA}/estado")).json()
            assert antes["sincronizable"] is True
            (fila,) = antes["proveedores"]
            assert (fila["proveedor"], fila["configurado"], fila["fuentes"], fila["modelos"]) == (
                "foundry",
                True,
                ["proyecto"],
                0,
            )
            assert fila["ultimo_intento"] is None and fila["aviso"] is None
            r = await c.post(f"{RUTA}/sincronizar", headers=CSRF)
            assert r.status_code == 200, r.text
            cuerpo = r.json()
            assert (cuerpo["resultado"], cuerpo["modelos"]) == ("ok", 1)
            (p,) = cuerpo["proveedores"]
            assert p["proveedor"] == "foundry" and p["resultado"] == "ok" and p["error"] is None
            assert {"org", "por", "origen"}.isdisjoint(p)
            despues = (await c.get(f"{RUTA}/estado")).json()["proveedores"][0]
            assert despues["modelos"] == 1 and despues["leido_en"]
            intento = despues["ultimo_intento"]
            assert intento["resultado"] == "ok" and intento["por"] == "juliancardonagaleano"
            assert intento["origen"] == "consola" and "org" not in intento
            assert [x["modelo"] for x in (await c.get(RUTA)).json()] == ["claude-opus-5-5"]

    correr(caso())
    auditoria = [
        r
        for r in m.almacen.db.auditoria.find({"evento": EventoAuditoria.cambio_configuracion.value})
        if r["detalle"].get("entidad") == "catalogo"
    ]
    (a,) = auditoria
    assert a["detalle"] | {} == {
        "entidad": "catalogo",
        "accion": "sincronizar",
        "resultado": "ok",
        "modelos": 1,
        "foundry": "ok",
    }
    assert a["alcance"]["org"] == ORG and a["actor"]["login"] == "juliancardonagaleano"


def test_si_todos_fallan_responde_502_con_el_error_claro_y_se_audita():
    foundry = Fuente([_entrada("opus-dz")])
    m = _montaje(foundry)

    async def caso():
        async with m.cliente("tk-julian") as c:
            assert (await c.post(f"{RUTA}/sincronizar", headers=CSRF)).status_code == 200
            foundry.fallo = _estado_http(403)
            m.reloj_s = 1000.0
            r = await c.post(f"{RUTA}/sincronizar", headers=CSRF)
            assert r.status_code == 502
            cuerpo = r.json()
            assert cuerpo["resultado"] == "error" and cuerpo["proveedores"][0]["error"]["codigo"] == "permiso"
            assert "HTTP 403" in cuerpo["detalle"] and "SECRETO" not in r.text
            # El catálogo anterior sigue ahí y el estado persistido cuenta el fallo.
            assert len((await c.get(RUTA)).json()) == 1
            fila = (await c.get(f"{RUTA}/estado")).json()["proveedores"][0]
            assert fila["modelos"] == 1 and fila["ultimo_intento"]["error"]["codigo"] == "permiso"
            assert "SECRETO" not in json.dumps(fila)

    correr(caso())
    detalles = [
        r["detalle"] for r in m.almacen.db.auditoria.find({}) if r["detalle"].get("entidad") == "catalogo"
    ]
    assert [d["resultado"] for d in detalles] == ["ok", "error"] and detalles[1]["foundry"] == "error:permiso"


def test_un_proveedor_que_falla_y_otro_que_no_dan_200_parcial():
    foundry = Fuente([_entrada("opus-dz")])
    anthropic = Fuente(fallo=_estado_http(401), proveedor=Proveedor.anthropic, nombres=("api",))
    m = _montaje(foundry, anthropic)

    async def caso():
        async with m.cliente("tk-julian") as c:
            r = await c.post(f"{RUTA}/sincronizar", headers=CSRF)
            assert r.status_code == 200
            cuerpo = r.json()
            assert cuerpo["resultado"] == "parcial"
            por = {p["proveedor"]: p for p in cuerpo["proveedores"]}
            assert (
                por["foundry"]["resultado"] == "ok" and por["anthropic"]["error"]["codigo"] == "autenticacion"
            )
            # Solo el que falla: 502 porque todos los pedidos fallaron.
            r = await c.post(f"{RUTA}/sincronizar", params={"proveedor": "anthropic"}, headers=CSRF)
            assert r.status_code == 502 and "anthropic" in r.json()["detalle"]
            filas = {f["proveedor"]: f for f in (await c.get(f"{RUTA}/estado")).json()["proveedores"]}
            assert filas["anthropic"]["ultimo_intento"]["resultado"] == "error"

    correr(caso())


def test_proveedor_desconocido_o_no_configurado_y_servidor_sin_proveedores():
    m = _montaje(Fuente([_entrada("opus-dz")]))
    vacio = Montaje()

    async def caso():
        async with m.cliente("tk-julian") as c:
            assert (
                await c.post(f"{RUTA}/sincronizar", params={"proveedor": "zzz"}, headers=CSRF)
            ).status_code == 422
            r = await c.post(f"{RUTA}/sincronizar", params={"proveedor": "anthropic"}, headers=CSRF)
            assert r.status_code == 404 and "no está configurado" in r.json()["detalle"]
        async with vacio.cliente("tk-julian") as c:
            r = await c.post(f"{RUTA}/sincronizar", headers=CSRF)
            assert r.status_code == 409 and "no tiene proveedores de modelo" in r.json()["detalle"]
            estado = (await c.get(f"{RUTA}/estado")).json()
            assert estado == {"sincronizable": False, "proveedores": []}

    correr(caso())


def test_avisos_por_proveedor_sin_fuente_o_ya_no_configurado():
    sin_fuente = Fuente(nombres=())
    m = _montaje(sin_fuente)
    # Un catálogo guardado de un proveedor que el servidor ya no tiene.
    otra = Catalogo([Fuente([_entrada(None, Proveedor.anthropic)], proveedor=Proveedor.anthropic)], m.almacen)
    correr(otra.sincronizar(ORG))

    async def caso():
        async with m.cliente("tk-julian") as c:
            filas = {f["proveedor"]: f for f in (await c.get(f"{RUTA}/estado")).json()["proveedores"]}
            assert "RAILSPEC_FOUNDRY_DESPLIEGUES" in filas["foundry"]["aviso"]
            assert (
                filas["anthropic"]["configurado"] is False
                and "ya no está configurado" in filas["anthropic"]["aviso"]
            )

    correr(caso())


def test_quien_puede_ver_y_quien_puede_sincronizar():
    m = _montaje(Fuente([_entrada("opus-dz")]))
    asignar(m.almacen, Rol.lector, ANA_ID)
    asignar(m.almacen, Rol.workspace_admin, LUIS_ID)

    async def caso():
        async with m.cliente("tk-ana") as c:
            assert (await c.get(f"{RUTA}/estado")).status_code == 200
            assert (await c.post(f"{RUTA}/sincronizar", headers=CSRF)).status_code == 403
        async with m.cliente("tk-luis") as c:  # workspace-admin no es org-admin
            assert (await c.post(f"{RUTA}/sincronizar", headers=CSRF)).status_code == 403
        async with m.cliente() as c:
            assert (await c.get(f"{RUTA}/estado")).status_code == 401
            assert (await c.post(f"{RUTA}/sincronizar", headers=CSRF)).status_code == 401
        asignar(m.almacen, Rol.org_admin, ANA_ID, workspace=None)
        async with m.cliente("tk-ana") as c:
            assert (await c.post(f"{RUTA}/sincronizar", headers=CSRF)).status_code == 200

    correr(caso())
    assert m.almacen.db.auditoria.count_documents({"detalle.entidad": "catalogo"}) == 1


def test_el_estado_de_una_organizacion_no_se_ve_en_otra():
    m = _montaje(Fuente([_entrada("opus-dz")]))
    correr(m.ctx.catalogo.sincronizar("otra-org", por="x"))

    async def caso():
        async with m.cliente("tk-julian") as c:
            fila = (await c.get(f"{RUTA}/estado")).json()["proveedores"][0]
            assert fila["modelos"] == 0 and fila["ultimo_intento"] is None

    correr(caso())


def test_ensamblar_conecta_el_catalogo_con_la_consola_y_con_el_registro_de_estados():
    from apoyo_motor import GobernanzaFija, ProveedorGuionado, critico_sin_hallazgos
    from railspec.server.app import ensamblar
    from railspec.server.config import Configuracion
    from railspec.server.consola import ConfigConsola
    from railspec.server.proveedores import Proveedores

    fuente = Fuente([_entrada("opus-dz")])
    catalogo = Catalogo([fuente])
    config = Configuracion(
        tokens_desarrollo={"tk": ("ana", 7)},
        permitir_desarrollo=True,
        consola=ConfigConsola(administradores=frozenset({7})),
    )
    motor, app = ensamblar(
        config,
        proveedores=Proveedores({Proveedor.foundry: ProveedorGuionado(critico_sin_hallazgos)}, catalogo),
        gobernanza=GobernanzaFija(),
    )
    assert catalogo.registrar is not None  # el estado de cada lectura va a Mongo sin cableado extra

    async def caso():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://railspec.test"
        ) as c:
            assert (await c.post("/consola/api/auth/desarrollo", json={"token": "tk"})).status_code == 204
            r = await c.post(f"{RUTA}/sincronizar", headers=CSRF)
            assert r.status_code == 200 and r.json()["proveedores"][0]["modelos"] == 1
            fila = (await c.get(f"{RUTA}/estado")).json()["proveedores"][0]
            assert fila["ultimo_intento"]["por"] == "ana"

    correr(caso())
    assert motor.n.almacen.db.catalogo_estado.count_documents({"org": ORG}) == 1
