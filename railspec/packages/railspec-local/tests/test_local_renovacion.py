"""Renovación de la sesión de ``railspec login`` con el refresh token (a través del servidor).

El servidor se simula con ``httpx2.MockTransport``; el candado de archivo se prueba con hilos que usan
almacenes distintos sobre el mismo archivo (``flock`` bloquea por descriptor, igual que entre procesos).
El proxy real por stdio contra un servidor local está en ``test_local_login``.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from railspec.local import cli, config, credenciales, doctor, renovacion
from railspec.local.errores import CredencialesInvalidas, RenovacionFallida, ServidorRechazo

URL = "https://railspec.example/mcp"
CLIENT_ID = "Iv23liRailspec"
TOKEN = "ghu_" + "a" * 36
REFRESCO = "ghr_" + "b" * 36
TOKEN_NUEVO = "ghu_" + "c" * 36
REFRESCO_NUEVO = "ghr_" + "d" * 36
AHORA = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
VENCIDA = AHORA - timedelta(hours=1)


def _credencial(**kw) -> credenciales.Credencial:
    datos = {
        "access_token": TOKEN,
        "client_id": CLIENT_ID,
        "login": "ana",
        "github_id": 7,
        "obtenido_en": AHORA - timedelta(hours=9),
        "expira_en": VENCIDA,
        "refresh_token": REFRESCO,
        "refresh_expira_en": AHORA + timedelta(days=100),
    }
    return credenciales.Credencial(**(datos | kw))


def _bien(**extra) -> httpx2.Response:
    cuerpo = {
        "access_token": TOKEN_NUEVO,
        "expires_in": 28800,
        "refresh_token": REFRESCO_NUEVO,
        "refresh_token_expires_in": 15811200,
    }
    return httpx2.Response(200, json=cuerpo | extra)


def _error(estado: int, codigo: str, detalle: str = "") -> httpx2.Response:
    return httpx2.Response(estado, json={"codigo": codigo, "detalle": detalle})


class ServidorFalso:
    """``POST {servidor}/v1/auth/renovar``; ``respuestas`` se consumen en orden y la última se repite."""

    def __init__(self, *respuestas, demora_s: float = 0.0):
        self.respuestas = list(respuestas) or [_bien()]
        self.demora_s = demora_s
        self.peticiones: list[httpx2.Request] = []
        self._cerrojo = threading.Lock()

    def __call__(self, peticion: httpx2.Request) -> httpx2.Response:
        with self._cerrojo:
            self.peticiones.append(peticion)
            respuesta = self.respuestas.pop(0) if len(self.respuestas) > 1 else self.respuestas[0]
        time.sleep(self.demora_s)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    def cliente(self) -> httpx2.Client:
        return httpx2.Client(transport=httpx2.MockTransport(self))

    def cuerpo(self, i: int = 0) -> dict:
        return json.loads(self.peticiones[i].content)


class Reloj:
    def __init__(self, t: datetime = AHORA):
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def _fuente(tmp_path, servidor: ServidorFalso, reloj=None, url=URL, entorno=None):
    almacen = credenciales.AlmacenCredenciales(tmp_path / "c.json")
    renovador = renovacion.RenovadorServidor(almacen, servidor.cliente())
    fuente = credenciales.FuenteToken(url, entorno, almacen, reloj or Reloj(), renovador=renovador)
    return fuente, almacen


# --- modelo ---------------------------------------------------------------------------------


def test_una_credencial_antigua_sin_refresh_token_se_sigue_leyendo(tmp_path):
    almacen = credenciales.AlmacenCredenciales(tmp_path / "c.json")
    almacen.guardar(URL, _credencial(refresh_token=None, refresh_expira_en=None))
    datos = json.loads(almacen.ruta.read_text(encoding="utf-8"))
    for clave in ("refresh_token", "refresh_expira_en"):  # el archivo de la versión anterior no los tenía
        datos["servidores"][URL].pop(clave)
    almacen.ruta.write_text(json.dumps(datos))

    leida = almacen.leer(URL)

    assert leida.refresh_token is None and not leida.renovable(AHORA)


def test_renovable_exige_refresh_token_y_que_no_haya_vencido():
    assert _credencial().renovable(AHORA)
    assert _credencial(refresh_expira_en=None).renovable(AHORA)  # GitHub no dijo cuándo vence: se intenta
    assert not _credencial(refresh_token=None).renovable(AHORA)
    assert not _credencial(refresh_expira_en=AHORA + timedelta(seconds=30)).renovable(AHORA)  # margen
    assert not _credencial(refresh_expira_en=AHORA - timedelta(days=1)).renovable(AHORA)
    assert "ghr_" not in repr(_credencial())


# --- renovar --------------------------------------------------------------------------------


def test_una_sesion_vencida_se_renueva_y_la_nueva_queda_guardada(tmp_path):
    servidor = ServidorFalso()
    fuente, almacen = _fuente(tmp_path, servidor)
    almacen.guardar(URL, _credencial())

    assert fuente.token() == TOKEN_NUEVO

    # Se llama al servidor (no a GitHub), sin el client secret, con lo que identifica la sesión.
    (peticion,) = servidor.peticiones
    assert (peticion.method, str(peticion.url)) == ("POST", "https://railspec.example/v1/auth/renovar")
    assert servidor.cuerpo() == {"refresh_token": REFRESCO, "client_id": CLIENT_ID}
    assert "authorization" not in peticion.headers
    guardada = almacen.leer(URL)
    assert guardada.access_token == TOKEN_NUEVO and guardada.obtenido_en == AHORA
    assert guardada.expira_en == AHORA + timedelta(seconds=28800)
    # GitHub rota el refresh token: el viejo ya no vale y el nuevo es el que queda.
    assert guardada.refresh_token == REFRESCO_NUEVO
    assert guardada.refresh_expira_en == AHORA + timedelta(seconds=15811200)
    assert (guardada.login, guardada.github_id, guardada.client_id) == ("ana", 7, CLIENT_ID)
    assert fuente.renovada
    # Ya vigente: ni se vuelve a llamar ni se toca el archivo.
    assert fuente.token() == TOKEN_NUEVO and len(servidor.peticiones) == 1


def test_una_sesion_vigente_no_llama_al_servidor(tmp_path):
    servidor = ServidorFalso()
    fuente, almacen = _fuente(tmp_path, servidor)
    almacen.guardar(URL, _credencial(expira_en=AHORA + timedelta(hours=3)))

    assert fuente.token() == TOKEN and servidor.peticiones == [] and not fuente.renovada


def test_la_sesion_se_renueva_un_poco_antes_de_vencer(tmp_path):
    servidor = ServidorFalso()
    fuente, almacen = _fuente(tmp_path, servidor)
    almacen.guardar(URL, _credencial(expira_en=AHORA + timedelta(seconds=30)))

    assert fuente.token() == TOKEN_NUEVO  # no se manda un token que llegaría vencido


def test_sesion_solo_mira_el_archivo_y_dice_que_se_renovaria(tmp_path):
    servidor = ServidorFalso()
    fuente, almacen = _fuente(tmp_path, servidor)
    almacen.guardar(URL, _credencial())

    sesion = fuente.sesion()

    assert sesion.vencida and sesion.renovable and sesion.token is None
    assert servidor.peticiones == []  # whoami/doctor consultan el estado sin renovar por su cuenta
    assert "se renueva sola" in sesion.nota()


def test_sin_refresh_token_la_sesion_vencida_no_se_renueva_y_se_explica(tmp_path):
    servidor = ServidorFalso()
    fuente, almacen = _fuente(tmp_path, servidor)
    almacen.guardar(URL, _credencial(refresh_token=None, refresh_expira_en=None))

    assert fuente.token() is None and servidor.peticiones == []
    nota = fuente.nota()
    assert "venció el 2026-10-02 11:00 UTC" in nota and "no tiene con qué renovarse" in nota
    assert "`railspec login`" in nota


def test_el_token_del_entorno_nunca_se_renueva(tmp_path):
    servidor = ServidorFalso()
    fuente, almacen = _fuente(tmp_path, servidor, entorno="ghu_del_entorno")
    almacen.guardar(URL, _credencial())

    assert fuente.token() == "ghu_del_entorno" and servidor.peticiones == []


def test_sin_renovador_se_comporta_como_antes(tmp_path):
    almacen = credenciales.AlmacenCredenciales(tmp_path / "c.json")
    almacen.guardar(URL, _credencial())

    assert credenciales.FuenteToken(URL, None, almacen, Reloj()).token() is None


def test_si_el_servidor_ya_no_devuelve_refresh_token_la_sesion_queda_sin_renovacion(tmp_path):
    servidor = ServidorFalso(_bien(refresh_token=None, refresh_token_expires_in=None, expires_in=None))
    fuente, almacen = _fuente(tmp_path, servidor)
    almacen.guardar(URL, _credencial())

    assert fuente.token() == TOKEN_NUEVO

    guardada = almacen.leer(URL)
    assert guardada.refresh_token is None and guardada.refresh_expira_en is None
    assert guardada.expira_en is None  # la App dejó de emitir tokens que vencen


# --- fallos ---------------------------------------------------------------------------------


def test_un_refresh_token_rechazado_se_descarta_y_manda_a_iniciar_sesion(tmp_path):
    detalle = "GitHub no acepta el refresh token (venció, ya se usó o se revocó la App)"
    servidor = ServidorFalso(_error(401, "refresh-token-invalido", detalle))
    fuente, almacen = _fuente(tmp_path, servidor)
    almacen.guardar(URL, _credencial())

    assert fuente.token() is None

    nota = fuente.nota()
    assert "no se pudo renovar" in nota and "GitHub no acepta el refresh token" in nota
    assert nota.endswith("Ejecuta `railspec login` otra vez.")
    # El refresh token inservible se quita: no se vuelve a canjear en cada petición del proxy.
    guardada = almacen.leer(URL)
    assert guardada.refresh_token is None and guardada.access_token == TOKEN
    assert fuente.token() is None and len(servidor.peticiones) == 1
    # Otro proceso (sin la memoria de este) tampoco insiste: la sesión ya no es renovable.
    otra, _ = _fuente(tmp_path, servidor)
    assert otra.token() is None and len(servidor.peticiones) == 1
    assert "no tiene con qué renovarse" in otra.nota()


def test_un_client_id_de_otra_app_tambien_es_definitivo(tmp_path):
    servidor = ServidorFalso(
        _error(400, "client-id-incorrecto", "el client id de tu sesión no es el del servidor")
    )
    fuente, almacen = _fuente(tmp_path, servidor)
    almacen.guardar(URL, _credencial())

    assert fuente.token() is None

    assert "no es el del servidor" in fuente.nota()
    assert almacen.leer(URL).refresh_token is None


def test_un_refresh_token_vencido_no_se_manda_y_se_descarta(tmp_path):
    servidor = ServidorFalso()
    reloj = Reloj()
    fuente, almacen = _fuente(tmp_path, servidor, reloj)
    almacen.guardar(URL, _credencial(refresh_expira_en=AHORA + timedelta(hours=1)))
    reloj.t = AHORA + timedelta(hours=2)

    assert fuente.token() is None  # ``renovable`` ya dice que no: ni se intenta
    assert servidor.peticiones == [] and "su refresh token también venció" in fuente.nota()
    # Y si la carrera la gana otro proceso que lo gastó, el candado releído lo descarta sin llamar.
    renovador = renovacion.RenovadorServidor(almacen, servidor.cliente())
    with pytest.raises(RenovacionFallida, match="ya no sirve") as exc:
        renovador.renovar(URL, reloj.t)
    assert exc.value.definitiva and almacen.leer(URL).refresh_token is None


def test_un_fallo_pasajero_no_toca_la_sesion_y_se_reintenta_pasado_un_rato(tmp_path):
    servidor = ServidorFalso(httpx2.ConnectError("sin red"), _bien())
    reloj = Reloj()
    fuente, almacen = _fuente(tmp_path, servidor, reloj)
    almacen.guardar(URL, _credencial())

    assert fuente.token() is None

    assert (
        "no se pudo contactar con https://railspec.example" in fuente.nota()
        and "ConnectError" in fuente.nota()
    )
    assert almacen.leer(URL) == _credencial()  # nada cambió: el refresh token sigue siendo bueno
    # Dentro del tiempo de espera no se vuelve a golpear al servidor en cada petición.
    reloj.t += timedelta(seconds=10)
    assert fuente.token() is None and len(servidor.peticiones) == 1
    assert "no se pudo contactar" in fuente.nota()
    reloj.t += timedelta(seconds=credenciales.REINTENTO_RENOVACION_S)
    assert fuente.token() == TOKEN_NUEVO and len(servidor.peticiones) == 2
    assert "no se pudo" not in fuente.nota()


@pytest.mark.parametrize(
    ("respuesta", "fragmento"),
    [
        (
            httpx2.Response(503, json={"codigo": "github-no-disponible", "detalle": "GitHub caído"}),
            "respondió 503",
        ),
        (httpx2.Response(502, text="<html>bad gateway</html>"), "respondió 502"),
        (_error(404, "renovacion-no-disponible", "sin GitHub App"), "no ofrece la renovación de sesión"),
        (
            httpx2.Response(404, text="Not Found"),
            "no ofrece la renovación de sesión",
        ),  # servidor sin actualizar
        (httpx2.Response(405), "no ofrece la renovación de sesión"),
        (httpx2.Response(307, headers={"location": "https://otro.example/v1/auth/renovar"}), "respondió 307"),
        (httpx2.Response(200, json={"token_type": "bearer"}), "sin un access_token"),
        (httpx2.Response(200, text="no es json"), "sin un access_token"),
    ],
)
def test_otras_respuestas_del_servidor_no_tocan_la_sesion_y_dicen_que_paso(tmp_path, respuesta, fragmento):
    servidor = ServidorFalso(respuesta)
    fuente, almacen = _fuente(tmp_path, servidor)
    almacen.guardar(URL, _credencial())

    assert fuente.token() is None

    assert fragmento in fuente.nota() and "no se pudo renovar" in fuente.nota()
    assert almacen.leer(URL) == _credencial()
    assert [p.url.host for p in servidor.peticiones] == ["railspec.example"]  # no se siguen redirecciones


def test_el_refresh_token_no_viaja_por_un_canal_sin_cifrar(tmp_path):
    servidor = ServidorFalso()
    url = "http://railspec.example/mcp"
    fuente, almacen = _fuente(tmp_path, servidor, url=url)
    almacen.guardar(url, _credencial())

    assert fuente.token() is None

    assert servidor.peticiones == [] and "no es https" in fuente.nota()
    # Un servidor local de desarrollo sí.
    local = "http://127.0.0.1:8080/mcp"
    almacen.guardar(local, _credencial())
    fuente_local = credenciales.FuenteToken(
        local, None, almacen, Reloj(), renovador=renovacion.RenovadorServidor(almacen, servidor.cliente())
    )
    assert fuente_local.token() == TOKEN_NUEVO
    assert str(servidor.peticiones[0].url) == "http://127.0.0.1:8080/v1/auth/renovar"


@pytest.mark.parametrize(
    ("mcp", "base"),
    [
        ("https://h.example/mcp", "https://h.example"),
        ("https://h.example/mcp/", "https://h.example"),
        ("https://H.example:8443/railspec/mcp?x=1", "https://H.example:8443/railspec"),
        ("https://h.example", "https://h.example"),
    ],
)
def test_la_url_base_quita_el_endpoint_mcp(mcp, base):
    assert renovacion.url_base(mcp) == base


def test_un_error_inesperado_del_renovador_nunca_rompe_la_peticion(tmp_path):
    class Roto:
        def renovar(self, url, ahora):
            raise RuntimeError("bug")

    almacen = credenciales.AlmacenCredenciales(tmp_path / "c.json")
    almacen.guardar(URL, _credencial())
    fuente = credenciales.FuenteToken(URL, None, almacen, Reloj(), renovador=Roto())

    assert fuente.token() is None and "error inesperado al renovar (RuntimeError)" in fuente.nota()


def test_si_la_sesion_se_cierra_mientras_se_renueva_se_dice(tmp_path):
    servidor = ServidorFalso()
    almacen = credenciales.AlmacenCredenciales(tmp_path / "c.json")
    with pytest.raises(RenovacionFallida, match="logout") as exc:
        renovacion.RenovadorServidor(almacen, servidor.cliente()).renovar(URL, AHORA)
    assert exc.value.definitiva and servidor.peticiones == []


# --- varios arneses a la vez ----------------------------------------------------------------


def test_varios_procesos_con_la_misma_sesion_canjean_el_refresh_token_una_sola_vez(tmp_path):
    """GitHub rota el refresh token: un segundo canje con el viejo dejaría a todos sin sesión."""

    servidor = ServidorFalso(demora_s=0.2)
    credenciales.AlmacenCredenciales(tmp_path / "c.json").guardar(URL, _credencial())
    resultados: list[str | None] = []

    def arnes() -> None:  # un arnés: su propio almacén, fuente y cliente, como un proceso aparte
        fuente, _ = _fuente(tmp_path, servidor)
        resultados.append(fuente.token())

    hilos = [threading.Thread(target=arnes) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(30)

    assert resultados == [TOKEN_NUEVO] * 8
    assert len(servidor.peticiones) == 1
    assert credenciales.AlmacenCredenciales(tmp_path / "c.json").leer(URL).refresh_token == REFRESCO_NUEVO


def test_los_hilos_de_un_mismo_proceso_tambien_comparten_una_sola_renovacion(tmp_path):
    servidor = ServidorFalso(demora_s=0.2)
    fuente, almacen = _fuente(tmp_path, servidor)
    almacen.guardar(URL, _credencial())
    resultados: list[str | None] = []

    hilos = [threading.Thread(target=lambda: resultados.append(fuente.token())) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(30)

    assert resultados == [TOKEN_NUEVO] * 8 and len(servidor.peticiones) == 1


def test_el_candado_es_reentrante_y_vence_si_otro_proceso_no_lo_suelta(tmp_path):
    ruta = tmp_path / "c.json"
    uno, otro = credenciales.AlmacenCredenciales(ruta), credenciales.AlmacenCredenciales(ruta)
    with uno.bloqueo():
        uno.guardar(URL, _credencial())  # guardar toma el candado que este hilo ya tiene
        with pytest.raises(CredencialesInvalidas, match="bloqueado"):
            with otro.bloqueo(espera_s=0.2):
                pass
    with otro.bloqueo(espera_s=0.2):  # soltado
        otro.borrar(URL)
    assert uno.leer(URL) is None


def test_si_el_archivo_esta_bloqueado_la_fuente_no_lanza_y_lo_cuenta(tmp_path, monkeypatch):
    servidor = ServidorFalso()
    fuente, almacen = _fuente(tmp_path, servidor)
    almacen.guardar(URL, _credencial())
    monkeypatch.setattr(credenciales, "ESPERA_CANDADO_S", 0.1)
    otro = credenciales.AlmacenCredenciales(almacen.ruta)

    with otro.bloqueo():
        assert fuente.token() is None

    assert "bloqueado" in fuente.nota() and servidor.peticiones == []


def test_logout_y_login_esperan_a_una_renovacion_en_curso(tmp_path):
    servidor = ServidorFalso(demora_s=0.4)
    fuente, almacen = _fuente(tmp_path, servidor)
    almacen.guardar(URL, _credencial())
    hilo = threading.Thread(target=fuente.token)
    hilo.start()
    while not servidor.peticiones:
        time.sleep(0.01)

    otro = credenciales.AlmacenCredenciales(almacen.ruta)
    inicio = time.monotonic()
    assert otro.borrar(URL)  # `railspec logout` en otra terminal: espera y borra la sesión ya renovada
    assert time.monotonic() - inicio > 0.1
    hilo.join(10)

    assert almacen.leer(URL) is None


# --- transporte -----------------------------------------------------------------------------


def test_el_transporte_renueva_antes_de_mandar_la_peticion_sin_parar_el_bucle(tmp_path):
    from railspec.local.transporte_mcp import _BearerVigente

    servidor = ServidorFalso(demora_s=0.3)
    fuente, almacen = _fuente(tmp_path, servidor)
    almacen.guardar(URL, _credencial())
    vistos: list[str | None] = []

    def responder(peticion: httpx2.Request) -> httpx2.Response:
        vistos.append(peticion.headers.get("authorization"))
        return httpx2.Response(200)

    async def caso() -> int:
        ticks = 0

        async def latido():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        tarea = asyncio.create_task(latido())
        async with httpx2.AsyncClient(
            transport=httpx2.MockTransport(responder), auth=_BearerVigente(fuente)
        ) as c:
            await c.get("https://railspec.example/mcp")
        tarea.cancel()
        return ticks

    assert asyncio.run(caso()) >= 5  # el bucle siguió corriendo mientras se esperaba al servidor
    assert vistos == [f"Bearer {TOKEN_NUEVO}"]


def test_el_transporte_explica_por_que_no_pudo_renovar(tmp_path):
    import mcp.types as types
    from railspec.local.transporte_mcp import TransporteMcpHttp

    servidor = ServidorFalso(_error(401, "refresh-token-invalido", "GitHub no acepta el refresh token"))
    fuente, almacen = _fuente(tmp_path, servidor)
    almacen.guardar(URL, _credencial())

    class ClienteFalso:
        async def call_tool(self, nombre, argumentos):
            fuente.token()  # lo que hace el Auth al mandar la petición
            texto = types.TextContent(type="text", text="falta Authorization: Bearer")
            return types.CallToolResult(content=[texto], is_error=True)

    async def llamar():
        transporte = TransporteMcpHttp(URL, fuente)

        async def abrir():
            return ClienteFalso()

        transporte._abrir = abrir
        return await transporte.llamar("unit.status", {})

    with pytest.raises(ServidorRechazo) as exc:
        asyncio.run(llamar())
    assert "no se pudo renovar" in str(exc.value) and "GitHub no acepta el refresh token" in str(exc.value)
    assert str(exc.value).endswith("Ejecuta `railspec login` otra vez.")


# --- comandos -------------------------------------------------------------------------------


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv(config.ENV_URL, URL)
    monkeypatch.setenv(config.ENV_CREDENCIALES, str(tmp_path / "credenciales.json"))
    monkeypatch.delenv(config.ENV_TOKEN, raising=False)
    monkeypatch.delenv(config.ENV_GITHUB_CLIENT_ID, raising=False)
    return credenciales.AlmacenCredenciales(tmp_path / "credenciales.json")


def _con_servidor(monkeypatch, servidor: ServidorFalso) -> None:
    original = renovacion.RenovadorServidor.__init__

    def init(self, almacen, cliente=None, **kw):
        original(self, almacen, cliente or servidor.cliente(), **kw)

    monkeypatch.setattr(renovacion.RenovadorServidor, "__init__", init)


def _salida(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_whoami_renueva_la_sesion_vencida_como_lo_haria_el_proxy(entorno, monkeypatch, capsys):
    servidor = ServidorFalso()
    _con_servidor(monkeypatch, servidor)
    entorno.guardar(URL, _credencial(expira_en=credenciales.ahora_utc() - timedelta(hours=1)))

    assert cli.main(["whoami"]) == 0

    salida = _salida(capsys)
    assert salida["vencida"] is False and salida["renovable"] is True
    assert "se renovó" in salida["renovada"] and "siguiente" not in salida
    assert TOKEN_NUEVO not in json.dumps(salida) and REFRESCO_NUEVO not in json.dumps(salida)
    assert entorno.leer(URL).access_token == TOKEN_NUEVO


def test_whoami_con_la_renovacion_fallida_sale_con_1_y_dice_por_que(entorno, monkeypatch, capsys):
    _con_servidor(
        monkeypatch, ServidorFalso(_error(401, "refresh-token-invalido", "GitHub no acepta el refresh token"))
    )
    entorno.guardar(URL, _credencial(expira_en=credenciales.ahora_utc() - timedelta(hours=1)))

    assert cli.main(["whoami"]) == 1

    salida = _salida(capsys)
    assert salida["vencida"] is True and "renovada" not in salida
    assert "no se pudo renovar" in salida["siguiente"] and "railspec login" in salida["siguiente"]


def test_whoami_con_sesion_vigente_dice_que_tiene_refresh_token_sin_llamar_al_servidor(
    entorno, monkeypatch, capsys
):
    servidor = ServidorFalso()
    _con_servidor(monkeypatch, servidor)
    entorno.guardar(URL, _credencial(expira_en=credenciales.ahora_utc() + timedelta(hours=3)))

    assert cli.main(["whoami"]) == 0

    salida = _salida(capsys)
    assert salida["renovable"] is True and salida["refresh_expira_en"] and "renovada" not in salida
    assert servidor.peticiones == []


# --- doctor ---------------------------------------------------------------------------------


def _sesion_de_doctor(tmp_path, credencial, ahora=AHORA):
    almacen = credenciales.AlmacenCredenciales(tmp_path / "c.json", cerrar_permisos=False)
    almacen.guardar(URL, credencial)
    fuente = credenciales.FuenteToken(URL, None, almacen, lambda: ahora)
    return doctor._sesion_local(fuente, ahora)


def test_doctor_una_sesion_vencida_con_refresh_token_vigente_no_es_un_fallo(tmp_path):
    comprobacion, hay_token = _sesion_de_doctor(tmp_path, _credencial())

    assert comprobacion.estado == "ok" and not hay_token  # doctor solo lee: no renueva ni prueba el token
    assert "venció el 2026-10-02 11:00 UTC" in comprobacion.detalle
    assert (
        "renueva con su refresh token" in comprobacion.detalle and "railspec whoami" in comprobacion.detalle
    )


def test_doctor_una_sesion_que_vence_pronto_pero_se_renueva_no_avisa(tmp_path):
    comprobacion, hay_token = _sesion_de_doctor(
        tmp_path, _credencial(expira_en=AHORA + timedelta(minutes=20))
    )

    assert comprobacion.estado == "ok" and hay_token and "se renueva sola" in comprobacion.detalle


def test_doctor_avisa_cuando_el_refresh_token_esta_por_vencer(tmp_path):
    comprobacion, hay_token = _sesion_de_doctor(
        tmp_path,
        _credencial(expira_en=AHORA + timedelta(hours=2), refresh_expira_en=AHORA + timedelta(days=3)),
    )

    assert comprobacion.estado == "aviso" and hay_token
    assert "refresh token" in comprobacion.detalle and "2026-10-05" in comprobacion.detalle
    assert comprobacion.remedio == "`railspec login`"


@pytest.mark.parametrize(
    ("credencial", "fragmento"),
    [
        (_credencial(refresh_token=None, refresh_expira_en=None), "no tiene refresh token"),
        (_credencial(refresh_expira_en=AHORA - timedelta(days=1)), "su refresh token también venció"),
    ],
)
def test_doctor_una_sesion_vencida_sin_con_que_renovarse_es_un_fallo(tmp_path, credencial, fragmento):
    comprobacion, hay_token = _sesion_de_doctor(tmp_path, credencial)

    assert comprobacion.estado == "fallo" and not hay_token
    assert fragmento in comprobacion.detalle and comprobacion.remedio == "`railspec login`"


def test_doctor_sin_refresh_token_sigue_avisando_del_vencimiento(tmp_path):
    comprobacion, _ = _sesion_de_doctor(
        tmp_path,
        _credencial(expira_en=AHORA + timedelta(minutes=20), refresh_token=None, refresh_expira_en=None),
    )

    assert comprobacion.estado == "aviso" and "no se renueva" in comprobacion.detalle


# --- client id publicado por el servidor ---------------------------------------------------------


def _cliente(respuesta, peticiones=None) -> httpx2.Client:
    def manejar(peticion: httpx2.Request) -> httpx2.Response:
        if peticiones is not None:
            peticiones.append(peticion)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    return httpx2.Client(transport=httpx2.MockTransport(manejar))


def test_descubrir_client_id_lee_la_configuracion_publica_del_servidor_sin_el_mcp_de_la_url():
    vistas: list[httpx2.Request] = []
    cliente = _cliente(httpx2.Response(200, json={"github_client_id": CLIENT_ID}), vistas)
    assert renovacion.descubrir_client_id(URL, cliente) == CLIENT_ID
    (peticion,) = vistas
    assert peticion.method == "GET" and str(peticion.url) == "https://railspec.example/v1/auth/config"
    assert "authorization" not in peticion.headers


@pytest.mark.parametrize(
    "respuesta",
    [
        httpx2.Response(404, json={"codigo": "login-no-disponible", "detalle": ""}),
        httpx2.Response(500),
        httpx2.Response(200, text="<html>no es JSON</html>"),
        httpx2.Response(200, json=["Iv"]),
        httpx2.Response(200, json={"github_client_id": ""}),
        httpx2.Response(200, json={"github_client_id": 7}),
        httpx2.Response(200, json={"github_client_id": "Iv con espacios\nY saltos"}),
        httpx2.Response(200, json={"github_client_id": "I" * 129}),
        httpx2.ConnectError("sin red"),
        httpx2.ReadTimeout("dormido"),
    ],
)
def test_descubrir_client_id_devuelve_none_ante_cualquier_respuesta_que_no_sea_un_id_valido(respuesta):
    assert renovacion.descubrir_client_id(URL, _cliente(respuesta)) is None


def test_descubrir_client_id_no_consulta_por_un_canal_sin_cifrar_salvo_en_local():
    vistas: list[httpx2.Request] = []
    cliente = _cliente(httpx2.Response(200, json={"github_client_id": CLIENT_ID}), vistas)
    assert renovacion.descubrir_client_id("http://railspec.example/mcp", cliente) is None and not vistas
    assert renovacion.descubrir_client_id("http://localhost:8080/mcp", cliente) == CLIENT_ID
