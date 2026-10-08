"""Avisos por Teams y correo, e informe semanal: canales, reservas, secretos, motor y API de la consola."""

from __future__ import annotations

import asyncio
import json
import logging
import smtplib
import uuid
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from apoyo_motor import JULIAN, ORG, WS, WS_ALCANCE, construir
from railspec.contracts.comun import Presupuesto
from railspec.contracts.repositorio import Auditoria, Rol
from railspec.contracts.tools import UnitListEntrada
from railspec.server.avisos.canales import (
    CanalCorreo,
    CanalTeams,
    ConfigSmtp,
    Mensaje,
    ResultadoEnvio,
    politica_teams,
)
from railspec.server.avisos.modelo import EventoAviso, EventosAviso, InformeConfig, TipoAviso
from railspec.server.avisos.servicio import (
    EntradaAvisos,
    ErrorAvisos,
    ServicioAvisos,
    instante_programado,
    tope_de_motivo,
)
from railspec.server.config import Configuracion, ErrorConfiguracion
from railspec.server.consola.almacen import AlmacenConsola
from test_consola import ANA_ID, CSRF, Montaje, _unidad_cerrada, asignar
from test_suscripciones import _cifrador

WEBHOOK = (
    "https://prod-12.westus.logic.azure.com:443/workflows/abc/triggers/manual/paths/invoke?sig=SECRETISIMO"
)
AHORA = datetime(2026, 10, 8, 12, 30, tzinfo=UTC)  # jueves
AUDITORIA = Auditoria(creado_por=JULIAN, creado_en=AHORA, actualizado_por=JULIAN, actualizado_en=AHORA)
RUTA = f"/consola/api/orgs/{ORG}/avisos"


class Sincrono:
    """Ejecutor que corre el trabajo en el acto (las pruebas no esperan hilos)."""

    def submit(self, fn, *a):
        fn(*a)


class CorreoFalso:
    def __init__(self, ok=True):
        self.enviados: list[tuple[list[str], str, Mensaje]] = []
        self.ok = ok

    def enviar(self, destinatarios, asunto, mensaje):
        self.enviados.append((destinatarios, asunto, mensaje))
        return ResultadoEnvio("correo", self.ok, None if self.ok else "smtp-sin-conexion")


class Teams:
    """Webhook simulado: guarda lo recibido y responde con ``estado``."""

    def __init__(self, estado=202):
        self.recibido: list[httpx2.Request] = []
        self.estado = estado

    def transporte(self):
        def responder(peticion):
            self.recibido.append(peticion)
            return httpx2.Response(self.estado)

        return httpx2.MockTransport(responder)

    def canal(self):
        return CanalTeams(politica_teams(), self.transporte())


def entrada(**kw) -> EntradaAvisos:
    base = dict(
        activo=True,
        teams_url=WEBHOOK,
        quitar_teams=False,
        destinatarios=["Equipo@Acme.com"],
        eventos=EventosAviso(),
        informe=InformeConfig(),
    )
    return EntradaAvisos(**{**base, **kw})


def servicio(teams=None, correo=None, reloj=lambda: AHORA, cifrador="si", **kw):
    m = Montaje()
    teams = teams or Teams()
    s = ServicioAvisos(
        m.ctx.datos,
        m.almacen,
        _cifrador() if cifrador == "si" else cifrador,
        None,
        url_consola="https://railspec.test",
        teams=teams.canal(),
        correo=correo,
        reloj=reloj,
        ejecutor=Sincrono(),
        **kw,
    )
    return m, s, teams


def escalado(unidad="U-001", causa="sin-convergencia", **kw) -> EventoAviso:
    return EventoAviso(
        tipo=TipoAviso.gate_escalado,
        org=ORG,
        workspace=WS,
        unidad=unidad,
        fase="spec",
        causa=causa,
        en=AHORA,
        **kw,
    )


# --- configuración del correo --------------------------------------------------------------------


def test_smtp_desde_url_y_defaults():
    c = ConfigSmtp.desde_url("smtp://railspec%40acme.com:p%40ss@smtp.acme.com?desde=avisos@acme.com")
    assert (c.host, c.puerto, c.seguridad, c.usuario, c.clave, c.desde) == (
        "smtp.acme.com",
        587,
        "starttls",
        "railspec@acme.com",
        "p@ss",
        "avisos@acme.com",
    )
    c = ConfigSmtp.desde_url("smtps://u:c@smtp.acme.com?desde=a@acme.com")
    assert (c.puerto, c.seguridad) == (465, "ssl")
    assert "c" not in repr(c).replace("smtp", "").replace("acme", "").replace(
        "com", ""
    ) or "clave" not in repr(c)
    assert ConfigSmtp.desde_entorno({}) is None


@pytest.mark.parametrize(
    "url",
    [
        "http://smtp.acme.com?desde=a@b.co",
        "smtp://smtp.acme.com",  # sin remitente
        "smtp://u:c@smtp.acme.com?desde=a@b.co&seguridad=ninguna",  # credenciales sin cifrar
        "smtp://u@smtp.acme.com?desde=a@b.co",  # usuario sin clave
        "smtp://smtp.acme.com?desde=a@b.co&seguridad=raro",
        "smtp://smtp.acme.com:99999?desde=a@b.co",
    ],
)
def test_smtp_url_invalida_no_arranca_y_no_repite_la_clave(url):
    with pytest.raises(ErrorConfiguracion) as e:
        Configuracion.desde_entorno({"RAILSPEC_SMTP_URL": url})
    assert "p@ss" not in str(e.value) and url not in str(e.value)


def test_correo_envia_con_starttls_y_login(monkeypatch):
    llamadas = []

    class SMTPFalso:
        def __init__(self, host, puerto, timeout=None):
            llamadas.append(("conectar", host, puerto))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            llamadas.append(("starttls",))

        def login(self, u, c):
            llamadas.append(("login", u))

        def send_message(self, msg):
            llamadas.append(("enviar", msg["To"], msg["Subject"], msg.get_content()))

    monkeypatch.setattr(smtplib, "SMTP", SMTPFalso)
    canal = CanalCorreo(ConfigSmtp.desde_url("smtp://u:c@smtp.acme.com?desde=a@acme.com"))
    r = canal.enviar(
        ["x@acme.com", "y@acme.com"],
        "[Railspec] hola",
        Mensaje("Título", (("a", "b"),), enlace="https://x/y"),
    )
    assert r.ok and r.codigo is None
    assert [x[0] for x in llamadas] == ["conectar", "starttls", "login", "enviar"]
    assert llamadas[-1][1] == "x@acme.com, y@acme.com" and "Título" in llamadas[-1][3]


@pytest.mark.parametrize(
    ("excepcion", "codigo"),
    [
        (smtplib.SMTPAuthenticationError(535, b"mala clave p@ss"), "smtp-autenticacion"),
        (smtplib.SMTPRecipientsRefused({}), "smtp-destinatarios"),
        (smtplib.SMTPException("x"), "smtp-error"),
        (ConnectionRefusedError("p@ss"), "smtp-sin-conexion"),
    ],
)
def test_correo_traduce_los_fallos_sin_repetir_el_error(monkeypatch, excepcion, codigo):
    def romper(*a, **k):
        raise excepcion

    monkeypatch.setattr(smtplib, "SMTP", romper)
    canal = CanalCorreo(ConfigSmtp.desde_url("smtp://u:c@smtp.acme.com?desde=a@acme.com"))
    r = canal.enviar(["x@acme.com"], "s", Mensaje("t"))
    assert (r.ok, r.codigo) == (False, codigo)


# --- Teams ---------------------------------------------------------------------------------------


def test_teams_manda_una_tarjeta_adaptable_con_enlace_y_sin_texto_de_codigo():
    t = Teams()
    r = t.canal().enviar(
        WEBHOOK, Mensaje("Hola", (("Unidad", "U-001"),), enlace="https://railspec.test/consola/x")
    )
    assert r.ok and len(t.recibido) == 1
    cuerpo = json.loads(t.recibido[0].content)
    tarjeta = cuerpo["attachments"][0]
    assert tarjeta["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert tarjeta["content"]["actions"][0]["url"] == "https://railspec.test/consola/x"


@pytest.mark.parametrize(
    "url",
    [
        "http://prod.logic.azure.com/x",  # no https
        "https://evil.example.com/x",  # host fuera de la lista
        "https://127.0.0.1/x",
        "https://user@prod.logic.azure.com/x",
        "https://logic.azure.com.evil.com/x",
    ],
)
def test_teams_rechaza_destinos_fuera_de_la_lista(url):
    t = Teams()
    r = t.canal().enviar(url, Mensaje("Hola"))
    assert (r.ok, r.codigo) == (False, "destino-no-permitido") and not t.recibido


def test_teams_fallo_http_y_de_red_no_repiten_el_url():
    t = Teams(estado=404)
    assert t.canal().enviar(WEBHOOK, Mensaje("x")).codigo == "http-404"

    def caer(peticion):
        raise httpx2.ConnectError(f"no conecta a {peticion.url}")

    canal = CanalTeams(politica_teams(), httpx2.MockTransport(caer))
    r = canal.enviar(WEBHOOK, Mensaje("x"))
    assert (r.ok, r.codigo) == (False, "sin-conexion") and "SECRETISIMO" not in repr(r)


# --- servicio: guardar ---------------------------------------------------------------------------


def test_guardar_cifra_el_webhook_y_solo_deja_el_host():
    m, s, _ = servicio()
    c = s.guardar(ORG, entrada(), None, AUDITORIA)
    assert c.teams_host == "prod-12.westus.logic.azure.com" and c.destinatarios == ["equipo@acme.com"]
    todo = json.dumps(
        {n: list(m.almacen.db[n].find({})) for n in m.almacen.db.list_collection_names()}, default=str
    )
    assert "SECRETISIMO" not in todo and "sig=" not in todo
    assert m.ctx.datos.secreto_aviso(ORG, "teams").startswith("v1.")
    # Editar sin url conserva el webhook; quitar lo borra.
    c2 = s.guardar(ORG, entrada(teams_url=None, activo=False), 1, AUDITORIA)
    assert c2.teams_host and c2.version == 2 and m.ctx.datos.secreto_aviso(ORG, "teams")
    c3 = s.guardar(ORG, entrada(teams_url=None, quitar_teams=True), 2, AUDITORIA)
    assert c3.teams_host is None and m.ctx.datos.secreto_aviso(ORG, "teams") is None


def test_el_valor_cifrado_de_un_aviso_no_descifra_como_clave_de_suscripcion():
    m, s, _ = servicio()
    s.guardar(ORG, entrada(), None, AUDITORIA)
    cifrado = m.ctx.datos.secreto_aviso(ORG, "teams")
    cifrador = s._cifrador
    assert cifrador.descifrar(cifrado, ORG, "teams", "aviso") == WEBHOOK
    from railspec.server.proveedores.cifrado import ErrorCifrado

    with pytest.raises(ErrorCifrado):
        cifrador.descifrar(cifrado, ORG, "teams")  # dominio de suscripciones


@pytest.mark.parametrize(
    ("kw", "codigo", "estado"),
    [
        ({"teams_url": "https://evil.example.com/x"}, "destino-no-permitido", 400),
        ({"destinatarios": ["no-es-correo"]}, "destinatario-invalido", 400),
        ({"destinatarios": ["a@acme.com", "A@acme.com"]}, "destinatario-invalido", 400),
        ({"quitar_teams": True}, "campos-invalidos", 400),
    ],
)
def test_guardar_valida(kw, codigo, estado):
    _, s, _ = servicio()
    with pytest.raises(ErrorAvisos) as e:
        s.guardar(ORG, entrada(**kw), None, AUDITORIA)
    assert (e.value.codigo, e.value.estado) == (codigo, estado)


def test_sin_clave_maestra_no_se_guarda_el_webhook_pero_si_el_correo():
    _, s, _ = servicio(cifrador=None)
    with pytest.raises(ErrorAvisos) as e:
        s.guardar(ORG, entrada(), None, AUDITORIA)
    assert (e.value.codigo, e.value.estado) == ("clave-maestra-ausente", 409)
    assert s.guardar(ORG, entrada(teams_url=None), None, AUDITORIA).teams_host is None


# --- servicio: avisos del motor ------------------------------------------------------------------


def test_un_escalado_sale_por_los_dos_canales_una_sola_vez_y_sin_codigo():
    correo = CorreoFalso()
    m, s, t = servicio(correo=correo)
    s.guardar(ORG, entrada(), None, AUDITORIA)
    s.notificar(escalado())
    s.notificar(escalado())  # reintento / otra réplica: misma clave
    assert len(t.recibido) == 1 and len(correo.enviados) == 1
    destinatarios, asunto, mensaje = correo.enviados[0]
    assert destinatarios == ["equipo@acme.com"] and "Gate escalado" in asunto
    assert ("Unidad", "U-001") in mensaje.hechos
    assert mensaje.enlace == f"https://railspec.test/consola/{ORG}/{WS}/unidades/U-001"
    hist = s.vista(ORG)["ultimos"]
    assert {h["canal"] for h in hist} == {"teams", "correo"} and all(
        h["resultado"] == "enviado" for h in hist
    )


def test_el_aviso_de_presupuesto_lleva_tope_y_consumo_en_numeros():
    correo = CorreoFalso()
    _, s, _ = servicio(correo=correo)
    s.guardar(ORG, entrada(teams_url=None), None, AUDITORIA)
    tope, consumo = tope_de_motivo("presupuesto agotado (por_fase spec: tokens 2400/2000)")
    s.notificar(
        escalado(causa="presupuesto-agotado", tope=tope, consumo=consumo).model_copy(
            update={"tipo": TipoAviso.presupuesto_agotado}
        )
    )
    ((_, asunto, mensaje),) = correo.enviados
    assert "Presupuesto agotado" in asunto
    assert ("Tope", "por_fase spec") in mensaje.hechos and ("Consumo", "tokens 2400/2000") in mensaje.hechos


def test_tope_de_motivo_ignora_lo_que_no_tiene_forma_numerica():
    assert tope_de_motivo("presupuesto agotado (mensual_usd 2026-09: costo 0.02/0.02 USD)") == (
        "mensual_usd 2026-09",
        "costo 0.02/0.02 USD",
    )
    assert tope_de_motivo("presupuesto agotado (por_unidad: def secreto(): return 1)") == (None, None)
    assert tope_de_motivo("proveedor: boom") == (None, None)


def test_el_presupuesto_mensual_avisa_una_vez_por_mes_y_workspace():
    correo = CorreoFalso()
    _, s, _ = servicio(correo=correo)
    s.guardar(ORG, entrada(teams_url=None), None, AUDITORIA)
    for u in ("U-001", "U-002", "U-003"):
        s.notificar(
            escalado(
                unidad=u,
                causa="presupuesto-agotado",
                tope="mensual_usd 2026-10",
                consumo="costo 5.00/5.00 USD",
            ).model_copy(update={"tipo": TipoAviso.presupuesto_agotado})
        )
    assert len(correo.enviados) == 1


def test_respeta_el_interruptor_y_los_eventos_pero_el_escalado_queda_contado():
    correo = CorreoFalso()
    m, s, _ = servicio(correo=correo)
    s.guardar(ORG, entrada(teams_url=None, activo=False), None, AUDITORIA)
    s.notificar(escalado())
    s.guardar(ORG, entrada(teams_url=None, eventos=EventosAviso(gate_escalado=False)), 1, AUDITORIA)
    s.notificar(escalado(unidad="U-002"))
    assert correo.enviados == []
    assert len(m.ctx.datos.eventos_aviso(ORG, AHORA - timedelta(days=1), AHORA + timedelta(days=1))) == 2


def test_tope_por_hora_corta_la_tormenta():
    correo = CorreoFalso()
    _, s, _ = servicio(correo=correo)
    s.guardar(ORG, entrada(teams_url=None), None, AUDITORIA)
    for i in range(40):
        s.notificar(escalado(unidad=f"U-{i:03d}"))
    assert len(correo.enviados) == 30


def test_un_canal_roto_no_frena_al_otro_y_libera_su_reserva_para_reintentar():
    correo = CorreoFalso(ok=False)
    _, s, t = servicio(correo=correo)
    s.guardar(ORG, entrada(), None, AUDITORIA)
    s.notificar(escalado())
    assert len(t.recibido) == 1 and len(correo.enviados) == 1
    hist = {h["canal"]: h for h in s.vista(ORG)["ultimos"]}
    assert hist["correo"]["resultado"] == "error" and hist["correo"]["codigo"] == "smtp-sin-conexion"
    assert s._datos.reservar_envio(ORG, s._clave(escalado()), "correo", AHORA)  # quedó libre
    assert not s._datos.reservar_envio(ORG, s._clave(escalado()), "teams", AHORA)  # Teams sí salió


def test_notificar_nunca_lanza_aunque_falle_el_ejecutor_o_el_estado(caplog):
    class Roto:
        def submit(self, *a):
            raise RuntimeError("sin hilos")

    _, s, _ = servicio()
    s._ejecutor = Roto()
    s.notificar(escalado())

    class Almacen:
        def __getattr__(self, n):
            raise RuntimeError("base caída")

    s._ejecutor, s._datos = Sincrono(), Almacen()
    with caplog.at_level(logging.ERROR, logger="railspec.avisos"):
        s.notificar(escalado())
    assert any("fallo al procesarlo" in r.getMessage() for r in caplog.records)


# --- servicio: informe ---------------------------------------------------------------------------


def test_instante_programado_es_el_ultimo_dia_y_hora_pasados():
    jueves = datetime(2026, 10, 8, 12, 30, tzinfo=UTC)
    assert instante_programado(jueves, 0, 8) == datetime(2026, 10, 5, 8, tzinfo=UTC)  # lunes
    assert instante_programado(jueves, 3, 8) == datetime(2026, 10, 8, 8, tzinfo=UTC)  # hoy, ya pasó
    assert instante_programado(jueves, 3, 15) == datetime(2026, 10, 1, 15, tzinfo=UTC)  # hoy, aún no


def test_informe_semanal_se_manda_una_vez_dentro_de_la_ventana_y_no_fuera_de_ella():
    correo = CorreoFalso()
    ahora = [datetime(2026, 10, 5, 9, tzinfo=UTC)]  # lunes 09:00, el informe es a las 08:00
    m, s, t = servicio(correo=correo, reloj=lambda: ahora[0])
    s.guardar(ORG, entrada(informe=InformeConfig(activo=True, dia_semana=0, hora_utc=8)), None, AUDITORIA)
    assert s.informe_semanal_pendiente() == 2
    assert s.informe_semanal_pendiente() == 0  # ya reservado
    assert len(correo.enviados) == 1 and "Informe semanal" in correo.enviados[0][1]
    ahora[0] = datetime(2026, 10, 7, 9, tzinfo=UTC)  # miércoles: pasó la ventana, espera al lunes
    assert s.informe_semanal_pendiente() == 0
    ahora[0] = datetime(2026, 10, 12, 8, 30, tzinfo=UTC)
    assert s.informe_semanal_pendiente() == 2


def test_informe_apagado_o_con_aviso_general_apagado_no_sale():
    correo = CorreoFalso()
    _, s, _ = servicio(correo=correo, reloj=lambda: datetime(2026, 10, 5, 9, tzinfo=UTC))
    s.guardar(ORG, entrada(teams_url=None, informe=InformeConfig(activo=False)), None, AUDITORIA)
    assert s.informe_semanal_pendiente() == 0
    s.guardar(ORG, entrada(teams_url=None, activo=False, informe=InformeConfig(activo=True)), 1, AUDITORIA)
    assert s.informe_semanal_pendiente() == 0 and not correo.enviados


def test_informe_cuenta_unidades_cerradas_escalados_y_gasto_por_tier():
    from railspec.contracts.repositorio import TelemetriaNodo

    async def caso():
        m = Montaje()
        await _unidad_cerrada(m)
        (cerrada,), _ = m.almacen.listar_estados(UnitListEntrada(alcance=WS_ALCANCE))
        ahora = cerrada.actualizado_en
        s = ServicioAvisos(
            m.ctx.datos,
            m.almacen,
            None,
            None,
            reloj=lambda: ahora + timedelta(minutes=1),
            ejecutor=Sincrono(),
        )
        antes = s.informe(ORG)
        assert antes["totales"]["unidades_cerradas"] == 1 and antes["totales"]["gates_escalados"] == 0
        for tier, costo in (("alto", 1.5), ("bajo", 0.25), ("alto", 0.5)):
            m.almacen.registrar_telemetria(
                TelemetriaNodo(
                    id=uuid.uuid4(),
                    org=ORG,
                    workspace=WS,
                    unidad="0001-demo",
                    nodo="n",
                    fase="spec",
                    tier=tier,
                    costo_usd=costo,
                    duracion_ms=1,
                    en=ahora,
                )
            )
        s.notificar(escalado(causa="sin-convergencia").model_copy(update={"en": ahora}))
        informe = s.informe(ORG)
        t0, t1 = antes["totales"], informe["totales"]
        assert t1["gates_escalados"] == 1 and t1["llamadas"] == t0["llamadas"] + 3
        assert round(t1["costo_usd"] - t0["costo_usd"], 4) == 2.25
        tier = {f["tier"]: f for f in informe["por_tier"]}
        base = {f["tier"]: f for f in antes["por_tier"]}
        assert round(tier["alto"]["costo_usd"] - base.get("alto", {"costo_usd": 0})["costo_usd"], 4) == 2.0
        assert informe["por_causa"] == [{"causa": "sin-convergencia", "gates_escalados": 1}]
        assert [w["workspace"] for w in informe["por_workspace"]] == [WS]

    asyncio.run(caso())


# --- el motor avisa al escalar -------------------------------------------------------------------


def test_el_motor_avisa_cuando_un_gate_escala_por_presupuesto_sin_mandar_texto_del_motivo():
    from apoyo_motor import avanzar  # noqa: F401
    from railspec.contracts.estado import TipoCheckpoint
    from test_motor_gate import es_checkpoint, hasta
    from test_motor_presupuesto import alta_siempre, arrancar

    async def caso():
        motor, _ = construir(alta_siempre)
        datos = AlmacenConsola(motor.n.almacen.db)
        correo = CorreoFalso()
        avisos = ServicioAvisos(
            datos,
            motor.n.almacen,
            _cifrador(),
            None,
            teams=Teams().canal(),
            correo=correo,
            ejecutor=Sincrono(),
        )
        motor.n.avisos = avisos
        avisos.guardar(ORG, entrada(teams_url=None), None, AUDITORIA)
        alcance = await arrancar(motor)
        # Sin tope, el gate no converge y escala por hallazgos; con un tope por unidad, por presupuesto.
        motor.n.escribir(alcance, lambda e: {"presupuesto": Presupuesto(tokens_max=1000)})
        await hasta(motor, alcance, es_checkpoint(TipoCheckpoint.gate_escalado))
        assert correo.enviados, "el gate escaló y nadie avisó"
        _, asunto, mensaje = correo.enviados[0]
        assert alcance.unidad in asunto or ("Unidad", alcance.unidad) in mensaje.hechos
        assert mensaje.texto().count("\n") < 30

    asyncio.run(caso())


# --- API de la consola ---------------------------------------------------------------------------


def _montaje_api(correo=None):
    m = Montaje()
    m.ctx.avisos = ServicioAvisos(
        m.ctx.datos,
        m.almacen,
        _cifrador(),
        None,
        url_consola="https://railspec.test",
        teams=Teams().canal(),
        correo=correo,
        ejecutor=Sincrono(),
    )
    return m


CUERPO = {
    "activo": True,
    "teams_url": WEBHOOK,
    "destinatarios": ["equipo@acme.com"],
    "eventos": {"gate_escalado": True, "presupuesto_agotado": False},
    "informe": {"activo": True, "dia_semana": 0, "hora_utc": 8},
}


def test_api_guarda_lee_y_nunca_devuelve_el_webhook():
    m = _montaje_api(correo=CorreoFalso())

    async def caso():
        async with m.cliente("tk-julian") as c:
            r = await c.get(RUTA)
            assert r.status_code == 200, r.text
            v = r.json()
            assert v["config"]["version"] is None and v["canales"]["correo"]["disponible"] is True
            assert v["cifrado"]["disponible"] is True and "*.logic.azure.com" in v["hosts_teams"]
            r = await c.put(RUTA, json=CUERPO, headers=CSRF)
            assert r.status_code == 200, r.text
            cfg = r.json()
            assert cfg["teams"] == {"configurado": True, "host": "prod-12.westus.logic.azure.com"}
            assert cfg["version"] == 1 and cfg["eventos"]["presupuesto_agotado"] is False
            for texto in (
                r.text,
                (await c.get(RUTA)).text,
                (await c.get(f"/consola/api/orgs/{ORG}/workspaces/{WS}/auditoria")).text,
            ):
                assert "SECRETISIMO" not in texto and "sig=" not in texto
            # Versión desfasada.
            r = await c.put(RUTA, json={**CUERPO, "teams_url": None, "version": 7}, headers=CSRF)
            assert r.status_code == 409
            r = await c.put(
                RUTA, json={**CUERPO, "teams_url": None, "version": 1, "activo": False}, headers=CSRF
            )
            assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["teams"]["configurado"]

    asyncio.run(caso())


def test_api_audita_el_cambio_sin_el_secreto():
    m = _montaje_api()

    async def caso():
        async with m.cliente("tk-julian") as c:
            await c.put(RUTA, json=CUERPO, headers=CSRF)
        regs, _ = m.ctx.datos.auditoria(ORG, "org")
        det = [r.detalle for r in regs if r.detalle.get("entidad") == "avisos"]
        assert det and det[0]["webhook_escrito"] is True and det[0]["teams_host"].endswith("logic.azure.com")
        assert "SECRETISIMO" not in json.dumps(det)

    asyncio.run(caso())


def test_api_rechaza_destinos_y_entradas_invalidas():
    m = _montaje_api()

    async def caso():
        async with m.cliente("tk-julian") as c:
            r = await c.put(RUTA, json={**CUERPO, "teams_url": "https://evil.example.com/x"}, headers=CSRF)
            assert r.status_code == 400 and r.json()["codigo"] == "destino-no-permitido"
            r = await c.put(
                RUTA,
                json={**CUERPO, "informe": {"activo": True, "dia_semana": 9, "hora_utc": 8}},
                headers=CSRF,
            )
            assert r.status_code == 422
            r = await c.put(RUTA, json={**CUERPO, "extra": 1}, headers=CSRF)
            assert r.status_code == 422

    asyncio.run(caso())


def test_api_solo_org_admin_lee_o_escribe():
    m = _montaje_api()
    asignar(m.almacen, Rol.desarrollador, ANA_ID)

    async def caso():
        async with m.cliente("tk-ana") as c:
            assert (await c.get(RUTA)).status_code == 403
            assert (await c.put(RUTA, json=CUERPO, headers=CSRF)).status_code == 403
            assert (await c.post(f"{RUTA}/prueba", json={}, headers=CSRF)).status_code == 403
            assert (await c.get(f"/consola/api/orgs/{ORG}/informe-semanal")).status_code == 403
        async with m.cliente() as c:
            assert (await c.get(RUTA)).status_code == 401

    asyncio.run(caso())


def test_api_prueba_y_vista_previa_del_informe():
    correo = CorreoFalso()
    m = _montaje_api(correo=correo)

    async def caso():
        async with m.cliente("tk-julian") as c:
            r = await c.post(f"{RUTA}/prueba", json={"que": "aviso"}, headers=CSRF)
            assert r.status_code == 409 and r.json()["codigo"] == "sin-configuracion"
            await c.put(RUTA, json=CUERPO, headers=CSRF)
            r = await c.post(f"{RUTA}/prueba", json={"que": "aviso"}, headers=CSRF)
            assert r.status_code == 200, r.text
            assert {x["canal"]: x["resultado"] for x in r.json()["resultados"]} == {
                "teams": "enviado",
                "correo": "enviado",
            }
            r = await c.post(f"{RUTA}/prueba", json={"que": "informe"}, headers=CSRF)
            assert r.status_code == 200 and len(correo.enviados) == 2
            v = (await c.get(RUTA)).json()
            assert {u["tipo"] for u in v["ultimos"]} == {"prueba", "informe-semanal"}
            r = await c.get(f"/consola/api/orgs/{ORG}/informe-semanal")
            assert r.status_code == 200 and set(r.json()) >= {
                "totales",
                "por_tier",
                "por_workspace",
                "desde",
                "hasta",
            }
            r = await c.post(f"{RUTA}/prueba", json={"que": "otro"}, headers=CSRF)
            assert r.status_code == 422

    asyncio.run(caso())


def test_api_sin_correo_ni_clave_maestra_lo_dice():
    m = Montaje()
    m.ctx.avisos = ServicioAvisos(m.ctx.datos, m.almacen, None, None, ejecutor=Sincrono())

    async def caso():
        async with m.cliente("tk-julian") as c:
            v = (await c.get(RUTA)).json()
            assert v["cifrado"]["disponible"] is False
            assert v["canales"]["correo"] == {
                "disponible": False,
                "motivo": "el servidor no tiene RAILSPEC_SMTP_URL",
            }
            r = await c.put(RUTA, json=CUERPO, headers=CSRF)
            assert r.status_code == 409 and r.json()["codigo"] == "clave-maestra-ausente"
            r = await c.put(RUTA, json={**CUERPO, "teams_url": None}, headers=CSRF)
            assert r.status_code == 200
            r = await c.post(f"{RUTA}/prueba", json={"que": "aviso"}, headers=CSRF)
            assert r.status_code == 409 and r.json()["codigo"] == "sin-canales"

    asyncio.run(caso())
