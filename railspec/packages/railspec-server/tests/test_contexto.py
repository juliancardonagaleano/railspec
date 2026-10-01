"""Herramientas de contexto conectables: política por rol, fases, presupuesto, secretos y caché."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from apoyo_motor import JULIAN, ORG, WS
from railspec.contracts.comun import AlcanceUnidad, Fase, GateFase, GobernanzaConsultada
from railspec.contracts.orden import ItemGobernanza
from railspec.contracts.repositorio import Auditoria, ProveedorContexto, RolContexto
from railspec.server.contexto import (
    ClientePce,
    ContextoConectable,
    FuenteContexto,
    PoliticaDestinos,
    ResolutorSecretos,
)
from railspec.server.contexto.secretos import SecretoNoDisponible
from railspec.server.estado import almacen_en_memoria

UNIDAD = AlcanceUnidad(org=ORG, workspace=WS, unidad="0001-emitir-pdf")


def correr(coro):
    return asyncio.run(coro)


def item(id_, tipo="adr", resumen=""):
    return ItemGobernanza(id=id_, tipo=tipo, titulo=f"Título {id_}", resumen=resumen)


class ClienteDoble:
    """Responde por URL; ``None`` en una consulta = esa consulta falló."""

    def __init__(self, respuestas):
        self.respuestas = respuestas
        self.consultas = []

    async def buscar(self, consultas):
        self.consultas.append(consultas)
        r = self.respuestas
        if isinstance(r, Exception):
            raise r
        return [r(tipo) if callable(r) else r for tipo, _ in consultas]


def contexto(fuentes, clientes, almacen=None, entorno=None, tmp=None, destinos=None):
    vistos = []

    def fabrica(f, clave):
        vistos.append((f.nombre, clave))
        return clientes[f.nombre]

    c = ContextoConectable(
        almacen,
        fuentes,
        ResolutorSecretos(tmp or "/nonexistent", entorno or {}),
        fabrica=fabrica,
        destinos=destinos or PoliticaDestinos(),
    )
    c.vistos = vistos
    return c


GOB = FuenteContexto(rol=RolContexto.gobernanza, nombre="pce", url="http://pce", api_key="k")


def test_sin_proveedor_de_gobernanza_no_hay_gate():
    doc = FuenteContexto(
        rol=RolContexto.documentacion, nombre="docs", url="http://d", politica_fallo="blanda"
    )
    r = correr(contexto([doc], {}).consultar(UNIDAD, GateFase.spec, "firmar pdf"))
    assert r.consultada == GobernanzaConsultada.no and "sin proveedor" in r.detalle


def test_gobernanza_consulta_tres_tipos_y_agrega():
    cliente = ClienteDoble(lambda tipo: [item(f"{tipo}-1", tipo), item("comun", tipo)])
    r = correr(contexto([GOB], {"pce": cliente}).consultar(UNIDAD, GateFase.spec, "firmar pdf"))
    assert r.consultada == GobernanzaConsultada.si
    assert [t for t, _ in cliente.consultas[0]] == ["adr", "policy", "principle"]
    assert [i.id for i in r.items] == ["adr-1", "comun", "policy-1", "principle-1"]


def test_fallos_parciales_y_totales_de_gobernanza():
    parcial = ClienteDoble(lambda tipo: None if tipo == "policy" else [item(tipo)])
    r = correr(contexto([GOB], {"pce": parcial}).consultar(UNIDAD, GateFase.plan, "x"))
    assert r.consultada == GobernanzaConsultada.parcial and "1/3" in r.detalle
    caido = ClienteDoble(RuntimeError("red"))
    r = correr(contexto([GOB], {"pce": caido}).consultar(UNIDAD, GateFase.plan, "x"))
    assert r.consultada == GobernanzaConsultada.no and r.items == []


def test_documentacion_blanda_suma_y_nunca_bloquea():
    doc = FuenteContexto(
        rol=RolContexto.documentacion, nombre="docs", url="http://d", politica_fallo="blanda"
    )
    gob = ClienteDoble(lambda tipo: [item(f"{tipo}-1", tipo)])
    r = correr(
        contexto([GOB, doc], {"pce": gob, "docs": ClienteDoble(RuntimeError("caído"))}).consultar(
            UNIDAD, GateFase.spec, "x"
        )
    )
    assert r.consultada == GobernanzaConsultada.si and "documentacion/docs" in r.detalle
    docs_ok = ClienteDoble([item("guia", "documentacion")])
    r = correr(contexto([GOB, doc], {"pce": gob, "docs": docs_ok}).consultar(UNIDAD, GateFase.spec, "x"))
    assert [i.id for i in r.items][-1] == "guia"  # gobernanza primero
    assert docs_ok.consultas[0] == [(None, "x")]
    # Estricta: su fallo cuenta como el de gobernanza.
    estricta = FuenteContexto(
        rol=RolContexto.memoria, nombre="mem", url="http://m", politica_fallo="estricta"
    )
    r = correr(
        contexto([GOB, estricta], {"pce": gob, "mem": ClienteDoble(RuntimeError("x"))}).consultar(
            UNIDAD, GateFase.spec, "x"
        )
    )
    assert r.consultada == GobernanzaConsultada.parcial


def test_fases_y_presupuesto_de_tokens():
    doc = FuenteContexto(
        rol=RolContexto.documentacion,
        nombre="docs",
        url="http://d",
        politica_fallo="blanda",
        fases=(Fase.implement,),
        presupuesto_tokens=75,
    )
    largos = ClienteDoble([item(f"d{i}", "documentacion", "x" * 100) for i in range(5)])
    gob = ClienteDoble(lambda tipo: [])
    c = contexto([GOB, doc], {"pce": gob, "docs": largos})
    correr(c.consultar(UNIDAD, GateFase.plan, "x"))
    assert largos.consultas == []  # fuera de sus fases
    r = correr(c.consultar(UNIDAD, GateFase.codigo, "x"))  # código = fase implement
    assert [i.id for i in r.items] == ["d0", "d1"]  # ~35 tokens cada uno, tope 75


def test_configuracion_en_mongo_sustituye_al_entorno_y_resuelve_secretos(tmp_path):
    almacen = almacen_en_memoria()
    ahora = datetime(2026, 9, 30, tzinfo=UTC)
    aud = Auditoria(creado_por=JULIAN, creado_en=ahora, actualizado_por=JULIAN, actualizado_en=ahora)

    def proveedor(workspace, nombre, ref):
        return ProveedorContexto(
            version=1,
            auditoria=aud,
            org=ORG,
            workspace=workspace,
            rol=RolContexto.gobernanza,
            nombre=nombre,
            url=f"https://{nombre}.acme.com/mcp",
            credencial_ref=ref,
            politica_fallo="estricta",
        )

    almacen.guardar_configuracion(
        [
            proveedor(None, "pce-org", f"secret://{ORG}--pce/clave"),
            proveedor(WS, "pce-org", f"secret://{ORG}--pce-ws/clave"),
        ]
    )
    (tmp_path / f"{ORG}--pce-ws").mkdir()
    (tmp_path / f"{ORG}--pce-ws" / "clave").write_text("secreto-ws\n")
    cliente = ClienteDoble(lambda tipo: [item(tipo)])
    destinos = PoliticaDestinos(hosts=frozenset({"*.acme.com"}))
    c = contexto(
        [GOB], {"pce-org": cliente, "pce": ClienteDoble([])}, almacen=almacen, tmp=tmp_path, destinos=destinos
    )
    r = correr(c.consultar(UNIDAD, GateFase.spec, "x"))
    assert r.consultada == GobernanzaConsultada.si
    assert c.vistos == [("pce-org", "secreto-ws")]  # el del workspace gana; el del entorno no se usa
    # En otro workspace rige el de la organización, cuyo secreto no existe: gobernanza "no".
    otra = AlcanceUnidad(org=ORG, workspace="reporteria", unidad="0001-x")
    r = correr(c.consultar(otra, GateFase.spec, "x"))
    assert r.consultada == GobernanzaConsultada.no and f"secret://{ORG}--pce/clave" in r.detalle


def test_resolutor_de_secretos(tmp_path):
    (tmp_path / "acme--foundry").mkdir()
    (tmp_path / "acme--foundry" / "api-key").write_text("de-archivo")
    r = ResolutorSecretos(tmp_path, {"RAILSPEC_SECRETO_ACME__PCE_API_KEY": "de-entorno"})
    assert r.resolver("secret://acme--foundry/api-key", "acme") == "de-archivo"
    assert r.resolver("secret://acme--pce/api-key", "acme") == "de-entorno"
    for ref in ("secret://acme--nada/x", "https://no-es-ref"):
        with pytest.raises(SecretoNoDisponible):
            r.resolver(ref, "acme")


def test_cliente_pce_cachea_lo_que_respondio():
    reloj = [0.0]
    pce = ClientePce("http://pce", None, cache_s=60, monotono=lambda: reloj[0])
    llamadas = []

    async def llamar(consultas):
        llamadas.append(consultas)
        return [None if tipo == "policy" else [item(tipo)] for tipo, _ in consultas]

    pce._llamar = llamar
    consultas = [("adr", "x"), ("policy", "x")]
    assert correr(pce.buscar(consultas))[1] is None
    correr(pce.buscar(consultas))
    assert llamadas[1] == [("policy", "x")]  # la fallida se repite; la buena sale de caché
    reloj[0] = 61
    correr(pce.buscar(consultas))
    assert llamadas[2] == consultas
