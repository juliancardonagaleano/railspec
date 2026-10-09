"""``railspec login``: device flow de GitHub (simulado), credenciales por usuario y uso en el proxy.

GitHub se simula con ``httpx2.MockTransport`` y un reloj falso, así que ninguna prueba espera ni sale a la
red; el proxy real por stdio se prueba contra un servidor MCP Streamable HTTP local que devuelve la
cabecera ``Authorization`` que recibió.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx2
import mcp.types as types
import pytest
from local_fabricas import repo_git
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from railspec.contracts._base import VERSION_CONTRATO
from railspec.local import cli, config, credenciales, dispositivo
from railspec.local.errores import (
    NOTA_TOKEN_GENERICA,
    CredencialesInvalidas,
    LoginFallido,
    ServidorRechazo,
    SinConexion,
)

URL = "https://railspec.example/mcp"
CLIENT_ID = "Iv23liRailspec"
TOKEN = "ghu_" + "a" * 36
REFRESCO = "ghr_" + "b" * 36
AHORA = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)

#: Windows no tiene bits de modo (0600…): las credenciales las protege la ACL de la carpeta de usuario.
MODOS_POSIX = sys.platform != "win32"
SOLO_POSIX = pytest.mark.skipif(not MODOS_POSIX, reason="los bits de modo no existen en Windows")


# --- GitHub simulado ------------------------------------------------------------------------


class Reloj:
    """Reloj monótono falso: ``dormir`` solo lo adelanta y recuerda cuánto se esperó."""

    def __init__(self) -> None:
        self.t = 0.0
        self.dormidos: list[float] = []

    def __call__(self) -> float:
        return self.t

    def dormir(self, segundos: float) -> None:
        self.dormidos.append(segundos)
        self.t += segundos


def _exito(**extra) -> dict:
    return {"access_token": TOKEN, "token_type": "bearer", "scope": "", **extra}


class GithubFalso:
    """Los tres endpoints que usa el flujo; ``sondeos`` son las respuestas sucesivas de ``access_token``."""

    def __init__(self, sondeos: list[dict], usuario: tuple[int, object] = (200, {"login": "ana", "id": 7})):
        self.sondeos = list(sondeos)
        self.usuario = usuario
        self.codigo = {
            "device_code": "dc-secreto",
            "user_code": "ABCD-1234",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900,
            "interval": 5,
        }
        self.peticiones: list[httpx2.Request] = []

    def __call__(self, peticion: httpx2.Request) -> httpx2.Response:
        self.peticiones.append(peticion)
        if peticion.url.path == "/login/device/code":
            return httpx2.Response(200, json=self.codigo)
        if peticion.url.path == "/login/oauth/access_token":
            return httpx2.Response(200, json=self.sondeos.pop(0))
        if peticion.url.host == "api.github.com" and peticion.url.path == "/user":
            estado, cuerpo = self.usuario
            return httpx2.Response(estado, json=cuerpo)
        raise AssertionError(f"petición inesperada: {peticion.method} {peticion.url}")

    def formulario(self, i: int) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(self.peticiones[i].content.decode()).items()}

    def flujo(self, reloj: Reloj, client_id: str | None = CLIENT_ID) -> dispositivo.FlujoDispositivo:
        cliente = httpx2.Client(transport=httpx2.MockTransport(self))
        return dispositivo.FlujoDispositivo(client_id, cliente, dormir=reloj.dormir, reloj=reloj)


def _pendiente() -> dict:
    return {"error": "authorization_pending"}


# --- device flow ----------------------------------------------------------------------------


def test_flujo_pide_el_codigo_espera_con_el_intervalo_y_devuelve_el_token():
    github, reloj = (
        GithubFalso([_pendiente(), _pendiente(), _exito(expires_in=28800, refresh_token=REFRESCO)]),
        Reloj(),
    )
    with github.flujo(reloj) as flujo:
        codigo = flujo.solicitar_codigo()
        token = flujo.esperar_token(codigo)

    assert (codigo.user_code, codigo.verification_uri) == ("ABCD-1234", "https://github.com/login/device")
    assert (token.access_token, token.expires_in) == (TOKEN, 28800)
    assert reloj.dormidos == [5, 5, 5]
    # Cliente público: solo client id y código de dispositivo, nunca un secret ni scopes.
    assert github.formulario(0) == {"client_id": CLIENT_ID}
    assert github.formulario(1) == {
        "client_id": CLIENT_ID,
        "device_code": "dc-secreto",
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }
    assert all(p.headers["accept"] == "application/json" for p in github.peticiones)
    assert "dc-secreto" not in repr(codigo) and TOKEN not in repr(token)


def test_flujo_obedece_slow_down_con_el_intervalo_nuevo_o_sumando_cinco():
    sondeos = [{"error": "slow_down", "interval": 12}, {"error": "slow_down"}, _exito()]
    github, reloj = GithubFalso(sondeos), Reloj()
    with github.flujo(reloj) as flujo:
        assert flujo.esperar_token(flujo.solicitar_codigo()).expires_in is None  # sin vencimiento
    assert reloj.dormidos == [5, 12, 17]


@pytest.mark.parametrize(
    ("error", "fragmento"),
    [
        ("access_denied", "Cancelaste la autorización"),
        ("expired_token", "venció sin que lo autorizaras"),
        ("device_flow_disabled", "Enable Device Flow"),
        ("incorrect_client_credentials", f"client id '{CLIENT_ID}'"),
        ("unsupported_grant_type", "unsupported_grant_type"),
    ],
)
def test_flujo_traduce_los_errores_de_github(error, fragmento):
    github, reloj = (
        GithubFalso([_pendiente(), {"error": error, "error_description": "detalle de GitHub"}]),
        Reloj(),
    )
    with github.flujo(reloj) as flujo, pytest.raises(LoginFallido) as exc:
        flujo.esperar_token(flujo.solicitar_codigo())
    assert fragmento in str(exc.value)


def test_flujo_se_rinde_cuando_el_codigo_vence_aunque_github_siga_diciendo_pendiente():
    github, reloj = GithubFalso([_pendiente()] * 400), Reloj()
    with github.flujo(reloj) as flujo, pytest.raises(LoginFallido, match="venció sin que lo autorizaras"):
        flujo.esperar_token(flujo.solicitar_codigo())
    assert reloj.t >= 900 and len(github.peticiones) < 400


def test_flujo_client_id_desconocido_en_el_primer_paso():
    class SinApp(GithubFalso):
        def __call__(self, peticion):
            return httpx2.Response(404, json={"error": "Not Found"})

    with SinApp([]).flujo(Reloj()) as flujo, pytest.raises(LoginFallido, match="no reconoce el client id"):
        flujo.solicitar_codigo()


def test_flujo_respuestas_ilegibles_y_red_caida():
    html = httpx2.Client(
        transport=httpx2.MockTransport(lambda p: httpx2.Response(502, text="<html>bad gateway</html>"))
    )
    with pytest.raises(LoginFallido, match="502"):
        dispositivo.FlujoDispositivo(CLIENT_ID, html).solicitar_codigo()

    sin_campos = GithubFalso([])
    sin_campos.codigo = {"user_code": "X"}
    with sin_campos.flujo(Reloj()) as flujo, pytest.raises(LoginFallido, match="sin los campos"):
        flujo.solicitar_codigo()

    def caida(peticion):
        raise httpx2.ConnectError("sin red", request=peticion)

    cliente = httpx2.Client(transport=httpx2.MockTransport(caida))
    with pytest.raises(SinConexion, match="No se pudo contactar con GitHub"):
        dispositivo.FlujoDispositivo(CLIENT_ID, cliente).solicitar_codigo()


def test_flujo_sin_client_id_no_pide_nada():
    github = GithubFalso([])
    with (
        github.flujo(Reloj(), client_id=None) as flujo,
        pytest.raises(LoginFallido, match="Falta el client id"),
    ):
        flujo.solicitar_codigo()
    assert github.peticiones == []


def test_persona_y_comprobar():
    ok = GithubFalso([])
    with ok.flujo(Reloj(), None) as flujo:
        assert flujo.comprobar(TOKEN) == dispositivo.Persona("ana", 7)
        assert flujo.persona(TOKEN) == dispositivo.Persona("ana", 7)
    assert ok.peticiones[0].headers["authorization"] == f"Bearer {TOKEN}"

    revocado = GithubFalso([], usuario=(401, {"message": "Bad credentials"}))
    with revocado.flujo(Reloj(), None) as flujo:
        assert flujo.comprobar(TOKEN) is None

    roto = GithubFalso([], usuario=(500, {}))
    with roto.flujo(Reloj(), None) as flujo:
        with pytest.raises(LoginFallido, match="500"):
            flujo.comprobar(TOKEN)
        # Tras `login`, no saber de quién es el token no impide guardarlo.
        assert flujo.persona(TOKEN) is None

    sin_usuario = GithubFalso([], usuario=(200, {"otro": 1}))
    with (
        sin_usuario.flujo(Reloj(), None) as flujo,
        pytest.raises(LoginFallido, match="no devolvió el usuario"),
    ):
        flujo.comprobar(TOKEN)


# --- credenciales por usuario ---------------------------------------------------------------


def _credencial(**kw) -> credenciales.Credencial:
    datos = {
        "access_token": TOKEN,
        "client_id": CLIENT_ID,
        "login": "ana",
        "github_id": 7,
        "obtenido_en": AHORA,
    }
    return credenciales.Credencial(**(datos | kw))


def test_la_credencial_se_guarda_con_permisos_cerrados_y_sin_restos(tmp_path):
    almacen = credenciales.AlmacenCredenciales(tmp_path / "nueva" / "railspec" / "credenciales.json")

    almacen.guardar(URL, _credencial())

    carpeta = almacen.ruta.parent
    if MODOS_POSIX:
        assert stat.S_IMODE(carpeta.stat().st_mode) == 0o700
        assert stat.S_IMODE(almacen.ruta.stat().st_mode) == 0o600
    # Solo el archivo y su candado (``bloqueo``): ningún temporal ni copia del token.
    assert sorted(p.name for p in carpeta.iterdir()) == ["credenciales.json", "credenciales.json.lock"]
    guardado = json.loads(almacen.ruta.read_text(encoding="utf-8"))
    assert guardado["version"] == 1 and guardado["servidores"][URL]["access_token"] == TOKEN
    leida = almacen.leer(URL)
    assert leida == _credencial()
    assert TOKEN not in repr(leida)


def test_la_clave_del_servidor_ignora_mayusculas_barra_final_y_query(tmp_path):
    almacen = credenciales.AlmacenCredenciales(tmp_path / "c.json")
    almacen.guardar("HTTPS://Railspec.Example/mcp/", _credencial())

    assert almacen.leer("https://railspec.example/mcp") is not None
    assert almacen.leer("https://railspec.example/mcp?x=1") is not None
    assert almacen.leer("https://otro.example/mcp") is None
    assert almacen.leer("https://railspec.example/otra") is None


def test_borrar_quita_solo_ese_servidor_y_el_archivo_cuando_queda_vacio(tmp_path):
    almacen = credenciales.AlmacenCredenciales(tmp_path / "c.json")
    almacen.guardar(URL, _credencial())
    almacen.guardar("https://staging.example/mcp", _credencial(login="luis"))

    assert almacen.borrar(URL) is True
    assert almacen.leer(URL) is None and almacen.leer("https://staging.example/mcp").login == "luis"
    assert almacen.borrar(URL) is False
    assert almacen.borrar("https://staging.example/mcp") is True
    assert not almacen.ruta.exists()
    assert almacen.borrar(URL) is False  # sin archivo tampoco falla


@SOLO_POSIX
def test_un_archivo_legible_por_otros_se_cierra_al_leerlo(tmp_path):
    almacen = credenciales.AlmacenCredenciales(tmp_path / "c.json")
    almacen.guardar(URL, _credencial())
    almacen.ruta.chmod(0o644)

    assert almacen.leer(URL) is not None
    assert stat.S_IMODE(almacen.ruta.stat().st_mode) == 0o600


@SOLO_POSIX
def test_si_no_se_pueden_cerrar_los_permisos_no_se_usa_la_credencial(tmp_path, monkeypatch):
    almacen = credenciales.AlmacenCredenciales(tmp_path / "c.json")
    almacen.guardar(URL, _credencial())
    almacen.ruta.chmod(0o644)

    def denegar(self, modo):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(type(almacen.ruta), "chmod", denegar)
    with pytest.raises(CredencialesInvalidas, match="lo pueden leer otros usuarios"):
        almacen.leer(URL)


@pytest.mark.parametrize(
    "contenido",
    [
        "{ roto",
        '["lista"]',
        json.dumps({"version": 2, "servidores": {}}),
        json.dumps({"version": 1}),
        json.dumps({"version": 1, "servidores": {URL: TOKEN}}),  # el token donde iba un objeto
        json.dumps({"version": 1, "servidores": {URL: {"access_token": TOKEN, "client_id": "x"}}}),
    ],
)
def test_un_archivo_danado_se_explica_sin_repetir_el_token_y_login_lo_reescribe(tmp_path, contenido):
    almacen = credenciales.AlmacenCredenciales(tmp_path / "c.json")
    almacen.ruta.write_text(contenido, encoding="utf-8")
    almacen.ruta.chmod(0o600)

    with pytest.raises(CredencialesInvalidas) as exc:
        almacen.leer(URL)
    assert TOKEN not in str(exc.value) and "railspec login" in str(exc.value)

    almacen.guardar(URL, _credencial())
    assert almacen.leer(URL) is not None


def test_el_vencimiento_deja_un_margen_para_no_mandar_un_token_a_punto_de_caducar():
    c = _credencial(expira_en=AHORA + timedelta(seconds=credenciales.MARGEN_VENCIMIENTO_S + 5))
    assert not c.vencida(AHORA) and c.vencida(AHORA + timedelta(seconds=10))
    assert not _credencial().vencida(AHORA + timedelta(days=3650))  # sin expira_en no vence


def test_la_ruta_por_defecto_sigue_xdg_y_se_puede_fijar(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "casa"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "casa"))  # Path.home() en Windows
    assert (
        credenciales.ruta_por_defecto({}) == tmp_path / "casa" / ".config" / "railspec" / "credenciales.json"
    )
    xdg = {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    assert credenciales.ruta_por_defecto(xdg) == tmp_path / "xdg" / "railspec" / "credenciales.json"
    # La especificación XDG manda ignorar una ruta relativa.
    assert credenciales.ruta_por_defecto({"XDG_CONFIG_HOME": "relativa"}).parts[-3:] == (
        ".config",
        "railspec",
        "credenciales.json",
    )
    fija = {config.ENV_CREDENCIALES: "~/mis/credenciales.json", **xdg}
    assert credenciales.ruta_por_defecto(fija) == tmp_path / "casa" / "mis" / "credenciales.json"


def test_sin_carpeta_personal_no_hay_donde_guardar_pero_el_token_del_entorno_sigue_valiendo(monkeypatch):
    def sin_casa():
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", sin_casa)
    with pytest.raises(CredencialesInvalidas, match=f"fija {config.ENV_CREDENCIALES}"):
        credenciales.ruta_por_defecto({})
    almacen = credenciales.AlmacenCredenciales(entorno={})  # no resuelve la ruta hasta usarla
    # Con RAILSPEC_TOKEN el almacén ni se toca; sin él, la fuente no lanza y lo explica.
    assert credenciales.FuenteToken(URL, "ghu_x", almacen).token() == "ghu_x"
    sin_token = credenciales.FuenteToken(URL, None, almacen)
    assert sin_token.token() is None and "carpeta personal" in sin_token.nota()


# --- de dónde sale el token -----------------------------------------------------------------


def _fuente(almacen, entorno=None, ahora=AHORA) -> credenciales.FuenteToken:
    return credenciales.FuenteToken(URL, entorno, almacen, lambda: ahora)


def test_el_token_del_entorno_manda_sobre_la_sesion_guardada(tmp_path):
    almacen = credenciales.AlmacenCredenciales(tmp_path / "c.json")
    almacen.guardar(URL, _credencial())

    sesion = _fuente(almacen, "ghu_del_entorno").sesion()
    assert (sesion.origen, sesion.token) == ("entorno", "ghu_del_entorno")
    assert "ghu_del_entorno" not in repr(sesion)
    sesion = _fuente(almacen).sesion()
    assert (sesion.origen, sesion.token, sesion.credencial.login) == ("credenciales", TOKEN, "ana")


def test_una_sesion_vencida_no_se_manda_y_se_explica(tmp_path):
    almacen = credenciales.AlmacenCredenciales(tmp_path / "c.json")
    almacen.guardar(URL, _credencial(expira_en=AHORA + timedelta(hours=8)))

    assert _fuente(almacen).token() == TOKEN
    tarde = _fuente(almacen, ahora=AHORA + timedelta(hours=9))
    assert tarde.token() is None and tarde.sesion().vencida
    assert "venció el 2026-10-02 20:00 UTC" in tarde.nota() and "railspec login" in tarde.nota()


def test_sin_sesion_o_con_el_archivo_danado_la_fuente_no_lanza_y_manda_a_iniciar_sesion(tmp_path):
    almacen = credenciales.AlmacenCredenciales(tmp_path / "c.json")
    assert _fuente(almacen).token() is None and "No hay sesión iniciada" in _fuente(almacen).nota()

    almacen.ruta.write_text("{ roto", encoding="utf-8")
    fuente = _fuente(almacen)
    assert fuente.token() is None  # dentro de una petición HTTP un error mataría la sesión MCP
    assert "no es válido" in fuente.nota() and "railspec login" in fuente.nota()

    assert credenciales.FuenteToken(None).token() is None
    assert credenciales.FuenteToken(URL, almacen=None).sesion().origen == "ninguna"


def test_el_rechazo_del_servidor_sugiere_railspec_login_segun_el_origen_del_token(tmp_path):
    almacen = credenciales.AlmacenCredenciales(tmp_path / "c.json")
    almacen.guardar(URL, _credencial())

    guardada = str(
        ServidorRechazo("unit.status", "GitHub no reconoce el token (404)", _fuente(almacen).nota())
    )
    assert (
        "GitHub no reconoce el token (404)" in guardada and "sesión de `railspec login` como ana" in guardada
    )
    entorno = str(ServidorRechazo("unit.status", "", _fuente(almacen, "x").nota()))
    assert "sin detalle" in entorno and "RAILSPEC_TOKEN" in entorno and "rsc1" in entorno
    for texto in (
        str(ServidorRechazo("unit.status", "falta")),
        str(ServidorRechazo("unit.status", "falta", None)),
    ):
        assert NOTA_TOKEN_GENERICA in texto and "`railspec login`" in texto


def test_el_transporte_explica_el_rechazo_con_la_sesion_que_uso(tmp_path):
    from railspec.local.transporte_mcp import TransporteMcpHttp

    almacen = credenciales.AlmacenCredenciales(tmp_path / "c.json")
    almacen.guardar(URL, _credencial(expira_en=AHORA + timedelta(hours=8)))

    class ClienteFalso:
        async def call_tool(self, nombre, argumentos):
            texto = types.TextContent(type="text", text="falta Authorization: Bearer")
            return types.CallToolResult(content=[texto], is_error=True)

    async def llamar(transporte):
        async def abrir():
            return ClienteFalso()

        transporte._abrir = abrir
        return await transporte.llamar("unit.status", {})

    vencida = TransporteMcpHttp(URL, _fuente(almacen, ahora=AHORA + timedelta(days=1)))
    with pytest.raises(ServidorRechazo, match="venció el"):
        asyncio.run(llamar(vencida))
    sin_nada = TransporteMcpHttp(URL, None)
    with pytest.raises(ServidorRechazo, match="No hay sesión iniciada: ejecuta `railspec login`"):
        asyncio.run(llamar(sin_nada))


# --- comandos -------------------------------------------------------------------------------


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Lo que lee el CLI, aislado: URL de prueba, credenciales en tmp, sin token ni client id previos."""

    monkeypatch.setenv(config.ENV_URL, URL)
    monkeypatch.setenv(config.ENV_CREDENCIALES, str(tmp_path / "credenciales.json"))
    monkeypatch.delenv(config.ENV_TOKEN, raising=False)
    monkeypatch.delenv(config.ENV_GITHUB_CLIENT_ID, raising=False)
    return credenciales.AlmacenCredenciales(tmp_path / "credenciales.json")


def _con_github(monkeypatch, github: GithubFalso) -> Reloj:
    reloj = Reloj()
    monkeypatch.setattr(cli, "crear_flujo", lambda client_id: github.flujo(reloj, client_id))
    return reloj


def _salida(capsys) -> tuple[dict | None, str]:
    visto = capsys.readouterr()
    return (json.loads(visto.out) if visto.out.strip() else None), visto.err


def test_login_guarda_la_sesion_y_no_deja_ver_ningun_token(entorno, monkeypatch, capsys):
    github = GithubFalso(
        [_pendiente(), _exito(expires_in=28800, refresh_token=REFRESCO, refresh_token_expires_in=15811200)]
    )
    _con_github(monkeypatch, github)

    assert cli.main(["login", "--client-id", CLIENT_ID]) == 0

    salida, error = _salida(capsys)
    assert (salida["servidor"], salida["login"], salida["github_id"]) == (URL, "ana", 7)
    assert salida["credenciales"] == str(entorno.ruta) and "no hace falta reiniciar" in salida["siguiente"]
    assert salida["renovable"] is True and "renueva solo" in salida["renovacion"]
    assert "aviso_vencimiento" not in salida
    assert "https://github.com/login/device" in error and "ABCD-1234" in error
    for secreto in (TOKEN, REFRESCO, "dc-secreto"):
        assert secreto not in json.dumps(salida) and secreto not in error
    guardada = entorno.leer(URL)
    assert (guardada.access_token, guardada.client_id, guardada.login) == (TOKEN, CLIENT_ID, "ana")
    assert guardada.expira_en - guardada.obtenido_en == timedelta(seconds=28800)
    # El refresh token se guarda para renovar la sesión (el archivo es 0600) y no se imprime ni se repite.
    assert guardada.refresh_token == REFRESCO
    assert guardada.refresh_expira_en - guardada.obtenido_en == timedelta(seconds=15811200)
    assert REFRESCO not in repr(guardada)
    if MODOS_POSIX:
        assert stat.S_IMODE(entorno.ruta.stat().st_mode) == 0o600


def test_login_con_tokens_que_vencen_pero_sin_refresh_token_avisa_que_no_se_renovara(
    entorno, monkeypatch, capsys
):
    _con_github(monkeypatch, GithubFalso([_exito(expires_in=28800)]))

    assert cli.main(["login", "--client-id", CLIENT_ID]) == 0

    salida, _ = _salida(capsys)
    assert salida["renovable"] is False and "no entregó un refresh token" in salida["aviso_vencimiento"]
    assert "renovacion" not in salida
    assert entorno.leer(URL).refresh_token is None


def test_login_recuerda_el_client_id_y_el_de_la_variable_vale_si_no_hay_flag(entorno, monkeypatch, capsys):
    github = GithubFalso([_exito()])
    _con_github(monkeypatch, github)
    monkeypatch.setenv(config.ENV_GITHUB_CLIENT_ID, "Iv-del-entorno")
    assert cli.main(["login"]) == 0
    assert github.formulario(0) == {"client_id": "Iv-del-entorno"}
    assert entorno.leer(URL).expira_en is None  # App con tokens que no vencen
    assert "aviso_vencimiento" not in _salida(capsys)[0]

    # Sin flag ni variable, el siguiente login reutiliza el de la sesión guardada.
    monkeypatch.delenv(config.ENV_GITHUB_CLIENT_ID)
    github2 = GithubFalso([_exito()])
    _con_github(monkeypatch, github2)
    assert cli.main(["login"]) == 0
    assert github2.formulario(0) == {"client_id": "Iv-del-entorno"}

    # El flag gana a todo.
    github3 = GithubFalso([_exito()])
    _con_github(monkeypatch, github3)
    assert cli.main(["login", "--client-id", "Iv-flag"]) == 0
    assert github3.formulario(0) == {"client_id": "Iv-flag"} and entorno.leer(URL).client_id == "Iv-flag"


def test_login_sin_client_id_o_sin_url_dice_que_falta(entorno, monkeypatch, capsys):
    github = GithubFalso([])
    _con_github(monkeypatch, github)
    assert cli.main(["login"]) == 1
    error = _salida(capsys)[1]
    assert "Falta el client id" in error and config.ENV_GITHUB_CLIENT_ID in error and "--client-id" in error
    assert github.peticiones == [] and not entorno.ruta.exists()

    monkeypatch.delenv(config.ENV_URL)
    assert cli.main(["login", "--client-id", CLIENT_ID]) == 1
    assert f"Falta {config.ENV_URL}" in _salida(capsys)[1]


def test_login_avisa_si_railspec_token_esta_exportada(entorno, monkeypatch, capsys):
    _con_github(monkeypatch, GithubFalso([_exito()]))
    monkeypatch.setenv(config.ENV_TOKEN, "ghu_otro")
    assert cli.main(["login", "--client-id", CLIENT_ID]) == 0
    assert f"{config.ENV_TOKEN} está exportada y tiene prioridad" in _salida(capsys)[1]


def test_login_sin_donde_guardar_falla_antes_de_pedir_nada_a_github(entorno, monkeypatch, capsys):
    github = GithubFalso([])
    _con_github(monkeypatch, github)
    monkeypatch.delenv(config.ENV_CREDENCIALES)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    def sin_casa():
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", sin_casa)
    assert cli.main(["login", "--client-id", CLIENT_ID]) == 1
    assert "carpeta personal" in _salida(capsys)[1] and github.peticiones == []


def test_login_denegado_no_guarda_nada(entorno, monkeypatch, capsys):
    _con_github(monkeypatch, GithubFalso([{"error": "access_denied"}]))
    assert cli.main(["login", "--client-id", CLIENT_ID]) == 1
    assert "Cancelaste la autorización" in _salida(capsys)[1]
    assert not entorno.ruta.exists()


def test_login_cancelado_con_ctrl_c_no_guarda_nada(entorno, monkeypatch, capsys):
    github = GithubFalso([])
    reloj = _con_github(monkeypatch, github)

    def interrumpir(segundos):
        raise KeyboardInterrupt

    reloj.dormir = interrumpir
    assert cli.main(["login", "--client-id", CLIENT_ID]) == 1
    assert "Cancelado" in _salida(capsys)[1] and not entorno.ruta.exists()


def test_login_guarda_la_sesion_aunque_github_no_diga_de_quien_es(entorno, monkeypatch, capsys):
    _con_github(monkeypatch, GithubFalso([_exito()], usuario=(500, {})))
    assert cli.main(["login", "--client-id", CLIENT_ID]) == 0
    salida, _ = _salida(capsys)
    assert salida["login"] is None and "se guardó igual" in salida["aviso"]
    assert entorno.leer(URL).access_token == TOKEN


def test_login_repara_un_archivo_de_credenciales_danado(entorno, monkeypatch, capsys):
    entorno.ruta.write_text("{ roto", encoding="utf-8")
    _con_github(monkeypatch, GithubFalso([_exito()]))
    assert cli.main(["login", "--client-id", CLIENT_ID]) == 0
    assert entorno.leer(URL).login == "ana"


def test_whoami_sin_sesion_manda_a_iniciarla(entorno, capsys):
    assert cli.main(["whoami"]) == 1
    salida, _ = _salida(capsys)
    assert salida["origen"] is None and "railspec login" in salida["siguiente"]


def test_whoami_con_sesion_guardada(entorno, monkeypatch, capsys):
    entorno.guardar(URL, _credencial(expira_en=credenciales.ahora_utc() + timedelta(hours=3)))
    assert cli.main(["whoami"]) == 0
    salida, _ = _salida(capsys)
    assert (salida["origen"], salida["login"], salida["github_id"], salida["vencida"]) == (
        "railspec login",
        "ana",
        7,
        False,
    )
    assert TOKEN not in json.dumps(salida) and "github" not in salida and "siguiente" not in salida

    github = GithubFalso([])
    _con_github(monkeypatch, github)
    assert cli.main(["whoami", "--comprobar"]) == 0
    assert _salida(capsys)[0]["github"] == {"valido": True, "login": "ana", "github_id": 7}
    assert github.peticiones[0].headers["authorization"] == f"Bearer {TOKEN}"


def test_whoami_detecta_la_sesion_vencida_y_la_revocada(entorno, monkeypatch, capsys):
    entorno.guardar(URL, _credencial(expira_en=credenciales.ahora_utc() - timedelta(hours=1)))
    github = GithubFalso([])
    _con_github(monkeypatch, github)
    assert cli.main(["whoami", "--comprobar"]) == 1
    salida, _ = _salida(capsys)
    assert salida["vencida"] is True and "venció el" in salida["siguiente"]
    assert github.peticiones == []  # una sesión vencida no se manda ni a comprobar

    entorno.guardar(URL, _credencial())
    _con_github(monkeypatch, GithubFalso([], usuario=(401, {"message": "Bad credentials"})))
    assert cli.main(["whoami", "--comprobar"]) == 1
    salida, _ = _salida(capsys)
    assert salida["github"] == {"valido": False} and "railspec login" in salida["siguiente"]


def test_whoami_con_token_del_entorno_dice_que_ignora_la_sesion_guardada(entorno, monkeypatch, capsys):
    entorno.guardar(URL, _credencial())
    monkeypatch.setenv(config.ENV_TOKEN, "ghu_del_entorno")
    _con_github(monkeypatch, GithubFalso([], usuario=(200, {"login": "luis", "id": 9})))

    assert cli.main(["whoami", "--comprobar"]) == 0
    salida, _ = _salida(capsys)
    assert salida["origen"] == config.ENV_TOKEN and salida["github"]["login"] == "luis"
    assert "sesión guardada de ana" in salida["ignorada"] and "login" not in salida
    assert "ghu_del_entorno" not in json.dumps(salida)


def test_whoami_con_el_archivo_danado_lo_dice(entorno, capsys):
    entorno.ruta.write_text("{ roto", encoding="utf-8")
    assert cli.main(["whoami"]) == 1
    assert "no es válido" in _salida(capsys)[0]["problema"]


def test_logout_borra_la_sesion_y_es_idempotente(entorno, monkeypatch, capsys):
    entorno.guardar(URL, _credencial())
    assert cli.main(["logout"]) == 0
    salida, _ = _salida(capsys)
    assert salida["cerrada"] is True and "https://github.com/settings/apps/authorizations" in salida["aviso"]
    assert not entorno.ruta.exists()

    assert cli.main(["logout"]) == 0
    salida, _ = _salida(capsys)
    assert salida["cerrada"] is False and "aviso" not in salida

    monkeypatch.setenv(config.ENV_TOKEN, "ghu_x")
    assert cli.main(["logout"]) == 0
    assert "sigue exportada" in _salida(capsys)[0]["entorno"]


def test_logout_y_whoami_sin_url_dicen_que_falta(entorno, monkeypatch, capsys):
    monkeypatch.delenv(config.ENV_URL)
    for comando in ("logout", "whoami"):
        assert cli.main([comando]) == 1
        assert f"Falta {config.ENV_URL}" in _salida(capsys)[1]


def test_instalar_sugiere_railspec_login_en_vez_de_exportar_el_token_a_mano(tmp_path, capsys):
    raiz = repo_git(tmp_path / "repo")
    args = ["--repo", str(raiz), "instalar", "--org", "acme", "--workspace", "w", "--repositorio", "r"]
    assert cli.main([*args, "--arnes", "claude-code"]) == 0
    assert "railspec login" in _salida(capsys)[0]["siguiente"]


def test_crear_proxy_usa_la_sesion_guardada_sin_railspec_token(tmp_path, entorno, monkeypatch):
    raiz = repo_git(tmp_path / "repo")
    config.escribir_config_repositorio(
        raiz, config.ConfigRepositorio(org="acme", workspace="w", repositorio="r")
    )
    entorno.guardar(URL, _credencial())

    fuente = cli.crear_proxy(raiz).cliente.transporte.fuente
    assert (fuente.sesion().origen, fuente.token()) == ("credenciales", TOKEN)

    monkeypatch.setenv(config.ENV_TOKEN, "ghu_del_entorno")
    fuente = cli.crear_proxy(raiz).cliente.transporte.fuente
    assert (fuente.sesion().origen, fuente.token()) == ("entorno", "ghu_del_entorno")


# --- proxy real por stdio -------------------------------------------------------------------

SERVIDOR_ECO = textwrap.dedent(
    """
    import sys
    from typing import Any

    from mcp.server.mcpserver import Context, MCPServer

    servidor = MCPServer("railspec-eco")


    def unit_list(
        alcance: dict[str, Any],
        ctx: Context,
        version_contrato: str | None = None,
        repositorio: str | None = None,
        fase: list[str] | None = None,
        estado: list[str] | None = None,
        integradas: bool | None = None,
        cursor: str | None = None,
        limite: int = 50,
    ) -> dict[str, Any]:
        # El servidor devuelve la cabecera que le llegó: así la prueba ve qué token mandó el proxy.
        return {{
            "version_contrato": "{version}",
            "unidades": [],
            "cursor_siguiente": ctx.headers.get("authorization", "(sin cabecera)"),
        }}


    # Lo que hace railspec-server en /v1/auth/renovar: canjea el refresh token y rota el que entrega.
    renovaciones: list[str] = []


    @servidor.custom_route("/v1/auth/renovar", methods=["POST"])
    async def renovar(request):
        from starlette.responses import JSONResponse

        refresh = (await request.json())["refresh_token"]
        renovaciones.append(refresh)
        if refresh != "ghr_vigente":
            return JSONResponse(
                {{"codigo": "refresh-token-invalido", "detalle": "GitHub no acepta el refresh token"}}, 401
            )
        return JSONResponse(
            {{
                "access_token": "ghu_renovado",
                "expires_in": 28800,
                "refresh_token": "ghr_rotado",
                "refresh_token_expires_in": 15811200,
            }}
        )


    @servidor.custom_route("/_renovaciones", methods=["GET"])
    async def cuantas(request):
        from starlette.responses import JSONResponse

        return JSONResponse(renovaciones)


    servidor.add_tool(unit_list, name="unit_list", structured_output=False)
    servidor.run("streamable-http", host="127.0.0.1", port=int(sys.argv[1]))
    """
)


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def servidor_eco(tmp_path):
    script = tmp_path / "servidor_eco.py"
    script.write_text(SERVIDOR_ECO.format(version=VERSION_CONTRATO), encoding="utf-8")
    puerto = _puerto_libre()
    proceso = subprocess.Popen(
        [sys.executable, str(script), str(puerto)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    try:
        fin = time.monotonic() + 20
        while True:
            if proceso.poll() is not None:
                pytest.fail(f"el servidor eco terminó: {proceso.stderr.read().decode()}")
            try:
                with socket.create_connection(("127.0.0.1", puerto), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() > fin:
                    pytest.fail("el servidor eco no abrió el puerto")
                time.sleep(0.1)
        yield f"http://127.0.0.1:{puerto}/mcp"
    finally:
        proceso.terminate()
        proceso.wait(timeout=10)


def _parametros_proxy(raiz, url, extra_env) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "railspec.local.cli", "--repo", str(raiz), "mcp"],
        env={config.ENV_URL: url, **extra_env},
        cwd=str(raiz),
    )


async def _cabecera_vista(cliente) -> str:
    resultado = await cliente.call_tool("unit_list", {})
    assert not resultado.is_error, resultado.content
    return json.loads(resultado.content[0].text)["cursor_siguiente"]


def test_el_proxy_manda_la_sesion_guardada_y_la_relee_sin_reiniciar(tmp_path, servidor_eco):
    raiz = repo_git(tmp_path / "repo")
    config.escribir_config_repositorio(
        raiz, config.ConfigRepositorio(org="acme", workspace="w", repositorio="r")
    )
    almacen = credenciales.AlmacenCredenciales(tmp_path / "cred" / "credenciales.json")
    almacen.guardar(servidor_eco, _credencial(access_token="ghu_primero"))
    parametros = _parametros_proxy(raiz, servidor_eco, {config.ENV_CREDENCIALES: str(almacen.ruta)})

    async def flujo() -> list[str]:
        async with Client(stdio_client(parametros)) as cliente:
            vistas = [await _cabecera_vista(cliente)]
            # Otro `railspec login` con el arnés abierto: la siguiente llamada ya lleva el token nuevo.
            almacen.guardar(servidor_eco, _credencial(access_token="ghu_segundo"))
            vistas.append(await _cabecera_vista(cliente))
            # `railspec logout`: sin sesión no viaja ninguna cabecera.
            almacen.borrar(servidor_eco)
            vistas.append(await _cabecera_vista(cliente))
            return vistas

    vistas = asyncio.run(asyncio.wait_for(flujo(), timeout=90))
    assert vistas == ["Bearer ghu_primero", "Bearer ghu_segundo", "(sin cabecera)"]


def test_el_proxy_prefiere_railspec_token_a_la_sesion_guardada(tmp_path, servidor_eco):
    raiz = repo_git(tmp_path / "repo")
    config.escribir_config_repositorio(
        raiz, config.ConfigRepositorio(org="acme", workspace="w", repositorio="r")
    )
    almacen = credenciales.AlmacenCredenciales(tmp_path / "credenciales.json")
    almacen.guardar(servidor_eco, _credencial(access_token="ghu_guardado"))
    entorno_proxy = {config.ENV_CREDENCIALES: str(almacen.ruta), config.ENV_TOKEN: "ghu_del_entorno"}

    async def flujo() -> str:
        async with Client(stdio_client(_parametros_proxy(raiz, servidor_eco, entorno_proxy))) as cliente:
            return await _cabecera_vista(cliente)

    assert asyncio.run(asyncio.wait_for(flujo(), timeout=90)) == "Bearer ghu_del_entorno"
    assert os.environ.get(config.ENV_TOKEN) is None


def test_el_proxy_renueva_la_sesion_vencida_una_sola_vez_y_manda_el_token_nuevo(tmp_path, servidor_eco):
    raiz = repo_git(tmp_path / "repo")
    config.escribir_config_repositorio(
        raiz, config.ConfigRepositorio(org="acme", workspace="w", repositorio="r")
    )
    almacen = credenciales.AlmacenCredenciales(tmp_path / "cred" / "credenciales.json")
    ahora = credenciales.ahora_utc()
    almacen.guardar(
        servidor_eco,
        _credencial(
            access_token="ghu_vencido",
            obtenido_en=ahora - timedelta(hours=9),
            expira_en=ahora - timedelta(hours=1),
            refresh_token="ghr_vigente",
            refresh_expira_en=ahora + timedelta(days=100),
        ),
    )
    parametros = _parametros_proxy(raiz, servidor_eco, {config.ENV_CREDENCIALES: str(almacen.ruta)})

    async def flujo() -> list[str]:
        async with Client(stdio_client(parametros)) as cliente:
            return [await _cabecera_vista(cliente), await _cabecera_vista(cliente)]

    vistas = asyncio.run(asyncio.wait_for(flujo(), timeout=90))

    assert vistas == ["Bearer ghu_renovado", "Bearer ghu_renovado"]
    guardada = almacen.leer(servidor_eco)
    assert (guardada.access_token, guardada.refresh_token) == ("ghu_renovado", "ghr_rotado")
    assert httpx2.get(servidor_eco.removesuffix("/mcp") + "/_renovaciones").json() == ["ghr_vigente"]
