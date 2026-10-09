"""``railspec instalar`` sin parámetros: el servidor dice a qué vínculo pertenece el clon (``origin``)."""

from __future__ import annotations

import json

import httpx2
import pytest
from local_fabricas import repo_git, sh
from railspec.local import cli, config, git, resolucion
from railspec.local.errores import ConfigInvalida, SinConexion

URL = "https://railspec.example/mcp"
REPO_URL = "https://github.com/acme/certificados-api"
VINCULO = {
    "org": "acme",
    "workspace": "certificados",
    "repositorio": "certificados-api",
    "nivel_codigo": "interno",
    "rama_por_defecto": "main",
}


# --- remoto de git -> URL https de GitHub --------------------------------------------------------


@pytest.mark.parametrize(
    "remoto",
    [
        "https://github.com/acme/certificados-api",
        "https://github.com/acme/certificados-api.git",
        "https://github.com/acme/certificados-api/",
        "https://x-access-token:ghs_secreto@github.com/acme/certificados-api.git",
        "https://ana@github.com/acme/certificados-api",
        "git@github.com:acme/certificados-api.git",
        "git@github.com:acme/certificados-api",
        "ssh://git@github.com/acme/certificados-api.git",
        "ssh://git@github.com:22/acme/certificados-api.git",
    ],
)
def test_el_remoto_se_normaliza_a_https_sin_credenciales(remoto):
    assert git.url_https_de_remoto(remoto) == REPO_URL


@pytest.mark.parametrize(
    "remoto",
    [
        "https://gitlab.com/acme/certificados-api",
        "git@gitlab.com:acme/certificados-api.git",
        "http://github.com/acme/certificados-api",
        "https://github.com/acme",
        "https://github.com/acme/api/extra",
        "https://github.com/acme/..",
        "/ruta/local/repo.git",
        "",
    ],
)
def test_un_remoto_que_no_es_de_github_no_se_traduce(remoto):
    assert git.url_https_de_remoto(remoto) is None


def test_url_del_remoto_lee_origin_y_no_lanza_si_falta(tmp_path):
    raiz = repo_git(tmp_path / "repo")
    assert git.url_del_remoto(raiz) is None
    sh(raiz, "remote", "add", "origin", "git@github.com:acme/certificados-api.git")
    assert git.url_del_remoto(raiz) == REPO_URL
    assert git.url_del_remoto(raiz, "otro") is None


# --- cliente de /v1/repositorios/resolver --------------------------------------------------------


def _cliente(respuesta, vistas=None) -> httpx2.Client:
    def manejar(peticion: httpx2.Request) -> httpx2.Response:
        if vistas is not None:
            vistas.append(peticion)
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    return httpx2.Client(transport=httpx2.MockTransport(manejar))


def test_resolver_manda_la_url_y_el_token_al_servidor_sin_el_mcp():
    vistas: list[httpx2.Request] = []
    cliente = _cliente(httpx2.Response(200, json={"coincidencias": [VINCULO, "basura"]}), vistas)
    assert resolucion.resolver_repositorio(URL, "ghu_t", REPO_URL, cliente) == [VINCULO]
    (peticion,) = vistas
    assert peticion.url.path == "/v1/repositorios/resolver" and peticion.url.host == "railspec.example"
    assert peticion.url.params["url"] == REPO_URL
    assert peticion.headers["authorization"] == "Bearer ghu_t"


def test_resolver_no_manda_el_token_por_http_ni_sin_sesion():
    cliente = _cliente(httpx2.Response(200, json={"coincidencias": []}))
    with pytest.raises(ConfigInvalida, match="no es https"):
        resolucion.resolver_repositorio("http://railspec.example/mcp", "ghu_t", REPO_URL, cliente)
    with pytest.raises(ConfigInvalida, match="railspec login"):
        resolucion.resolver_repositorio(URL, None, REPO_URL, cliente)
    assert resolucion.resolver_repositorio("http://localhost:8080/mcp", "ghu_t", REPO_URL, cliente) == []


@pytest.mark.parametrize(
    ("respuesta", "mensaje"),
    [
        (httpx2.Response(401, json={"detalle": "token vencido"}), "token vencido"),
        (httpx2.Response(404), "sin actualizar"),
        (httpx2.Response(500, text="boom"), "respondió 500"),
        (httpx2.Response(200, text="<html>"), "respondió 200"),
    ],
)
def test_resolver_traduce_los_errores_del_servidor(respuesta, mensaje):
    with pytest.raises(ConfigInvalida, match=mensaje):
        resolucion.resolver_repositorio(URL, "ghu_t", REPO_URL, _cliente(respuesta))


def test_resolver_sin_red_es_un_error_de_conexion():
    with pytest.raises(SinConexion):
        resolucion.resolver_repositorio(URL, "ghu_t", REPO_URL, _cliente(httpx2.ConnectError("sin red")))


# --- railspec instalar sin parámetros -----------------------------------------------------------


@pytest.fixture
def clon(tmp_path, monkeypatch):
    raiz = repo_git(tmp_path / "certificados-api")
    sh(raiz, "remote", "add", "origin", "git@github.com:acme/certificados-api.git")
    monkeypatch.setenv(config.ENV_URL, URL)
    monkeypatch.setenv(config.ENV_CREDENCIALES, str(tmp_path / "credenciales.json"))
    monkeypatch.delenv(config.ENV_TOKEN, raising=False)
    monkeypatch.delenv(config.ENV_GITHUB_CLIENT_ID, raising=False)
    return raiz


def _servidor(monkeypatch, coincidencias):
    llamadas = []

    def resolver(url, token, url_repositorio, *a, **kw):
        llamadas.append((url, token, url_repositorio))
        return coincidencias

    monkeypatch.setattr(resolucion, "resolver_repositorio", resolver)
    monkeypatch.setenv(config.ENV_TOKEN, "ghu_de_prueba")
    return llamadas


def test_instalar_sin_parametros_escribe_la_configuracion_con_lo_que_dice_el_servidor(
    clon, monkeypatch, capsys
):
    llamadas = _servidor(monkeypatch, [VINCULO])
    assert cli.main(["--repo", str(clon), "instalar", "--arnes", "claude-code"]) == 0
    assert llamadas == [(URL, "ghu_de_prueba", REPO_URL)]
    escrito = json.loads((clon / config.ARCHIVO_CONFIG).read_text(encoding="utf-8"))
    assert escrito["org"] == "acme" and escrito["workspace"] == "certificados"
    assert escrito["repositorio"] == "certificados-api" and escrito["servidor"] == URL
    assert escrito["nivel_codigo"] == "interno" and escrito["arnes"] == "claude-code"
    salida = json.loads(capsys.readouterr().out)
    assert salida["servidor"] == URL and "RAILSPEC_URL" not in salida["siguiente"]


def test_el_nivel_pedido_manda_sobre_el_del_vinculo(clon, monkeypatch):
    _servidor(monkeypatch, [VINCULO])
    assert cli.main(["--repo", str(clon), "instalar", "--nivel", "restringido"]) == 0
    assert config.leer_config_repositorio(clon).nivel_codigo.value == "restringido"


def test_sin_coincidencias_o_con_varias_no_escribe_nada_y_explica_que_hacer(clon, monkeypatch, capsys):
    _servidor(monkeypatch, [])
    assert cli.main(["--repo", str(clon), "instalar"]) == 1
    error = capsys.readouterr().err
    assert REPO_URL in error and "--org" in error and not (clon / config.ARCHIVO_CONFIG).exists()

    _servidor(monkeypatch, [VINCULO, VINCULO | {"workspace": "otro"}])
    assert cli.main(["--repo", str(clon), "instalar"]) == 1
    error = capsys.readouterr().err
    assert "varios workspaces" in error and "--workspace otro" in error
    assert not (clon / config.ARCHIVO_CONFIG).exists()


def test_sin_origin_de_github_pide_los_parametros(tmp_path, monkeypatch, capsys):
    raiz = repo_git(tmp_path / "sin-remoto")
    monkeypatch.setenv(config.ENV_URL, URL)
    llamadas = _servidor(monkeypatch, [VINCULO])
    assert cli.main(["--repo", str(raiz), "instalar"]) == 1
    assert "--repositorio" in capsys.readouterr().err and llamadas == []


def test_con_parametros_o_con_configuracion_previa_no_se_consulta_al_servidor(clon, monkeypatch):
    llamadas = _servidor(monkeypatch, [VINCULO])
    args = ["--repo", str(clon), "instalar", "--org", "a", "--workspace", "w", "--repositorio", "r"]
    assert cli.main(args) == 0
    assert config.leer_config_repositorio(clon).servidor is None
    assert "servidor" not in json.loads((clon / config.ARCHIVO_CONFIG).read_text(encoding="utf-8"))
    assert cli.main(["--repo", str(clon), "instalar", "--arnes", "claude-code"]) == 0
    assert llamadas == []


def test_reinstalar_conserva_el_servidor_de_la_configuracion(clon, monkeypatch):
    _servidor(monkeypatch, [VINCULO])
    assert cli.main(["--repo", str(clon), "instalar"]) == 0
    assert cli.main(["--repo", str(clon), "instalar", "--nivel", "abierto"]) == 0
    repo = config.leer_config_repositorio(clon)
    assert repo.servidor == URL and repo.nivel_codigo.value == "abierto"


# --- el servidor de la configuración del repositorio -------------------------------------------


def test_sin_railspec_url_el_proxy_usa_el_servidor_del_repositorio_pero_nunca_railspec_token(clon):
    config.escribir_config_repositorio(
        clon, config.ConfigRepositorio(org="a", workspace="w", repositorio="r", servidor=URL)
    )
    solo_sesion = config.cargar(clon, {config.ENV_TOKEN: "ghu_ajeno"})
    assert solo_sesion.url == URL and solo_sesion.token is None
    explicita = config.cargar(clon, {config.ENV_URL: "https://otro.example/mcp", config.ENV_TOKEN: "ghu_mio"})
    assert explicita.url == "https://otro.example/mcp" and explicita.token == "ghu_mio"


def test_el_servidor_de_la_configuracion_debe_ser_https():
    for malo in ("http://railspec.example/mcp", "railspec.example", "https://con espacio"):
        with pytest.raises(ValueError):
            config.ConfigRepositorio(org="a", workspace="w", repositorio="r", servidor=malo)


def test_login_toma_el_servidor_del_repositorio_donde_se_ejecuta_y_lo_avisa(
    clon, monkeypatch, capsys, tmp_path
):
    config.escribir_config_repositorio(
        clon, config.ConfigRepositorio(org="a", workspace="w", repositorio="r", servidor=URL)
    )
    monkeypatch.delenv(config.ENV_URL)
    monkeypatch.chdir(clon)
    assert cli._url_servidor() == URL
    assert URL in capsys.readouterr().err
    monkeypatch.chdir(tmp_path)  # fuera de cualquier repositorio
    with pytest.raises(ConfigInvalida, match=config.ENV_URL):
        cli._url_servidor()
