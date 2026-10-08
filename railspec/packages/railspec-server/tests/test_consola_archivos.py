"""Consola: editar ``contexto.yaml`` y ``.railspecignore`` proponiendo un PR (nunca commit directo)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json

import httpx
import jwt
import pytest
from apoyo_motor import ORG, REPO, WS, vinculo
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from railspec.contracts.comun import NivelCodigo
from railspec.contracts.repositorio import Rol
from railspec.server.consola import ConfigConsola, ConfigGithubApp
from railspec.server.consola.archivos import diff_unificado, validar_contexto, validar_ignore
from railspec.server.consola.repo_github import RepoGithub
from test_consola import ANA_ID, CSRF, LUIS_ID, Montaje, asignar

RUTA = f"/consola/api/orgs/{ORG}/workspaces/{WS}/repositorios/{REPO}/archivos"
NOMBRE = "acme/certificados-api"
CLAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = CLAVE.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
APP = ConfigGithubApp("Iv1.x", "secreto", "4242", PEM)
CONTEXTO_VALIDO = """\
version: 1
proveedores:
  - nombre: docs
    rol: documentacion
    url: https://docs.acme.example/mcp
    politica_fallo: blanda
    fases: [spec, plan]
"""
PROTEGIDA = "main"


def correr(coro):
    return asyncio.run(coro)


def _sha(texto: str) -> str:
    return hashlib.sha1(texto.encode()).hexdigest()


class GithubFalso:
    """GitHub mínimo para el flujo de la App: instalación, token, lectura, rama, commit y PR."""

    def __init__(self, permisos=None, instalada=True) -> None:
        self.permisos = {"contents": "write", "pull_requests": "write"} if permisos is None else permisos
        self.instalada = instalada
        self.archivos: dict[str, str] = {}  # ruta → contenido en la rama base
        self.ramas: dict[str, dict[str, str]] = {}
        self.llamadas: list[tuple[str, str, dict]] = []
        self.prs: list[dict] = []
        self.falla_pr = False
        self.tokens_pedidos: list[dict] = []

    def cliente(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.responder))

    def responder(self, p: httpx.Request) -> httpx.Response:
        ruta = p.url.path
        cuerpo = json.loads(p.content) if p.content else {}
        self.llamadas.append((p.method, ruta, cuerpo))
        auth = p.headers.get("authorization", "")
        base = f"/repos/{NOMBRE}"
        if ruta == f"{base}/installation":
            claims = jwt.decode(auth.removeprefix("Bearer "), CLAVE.public_key(), algorithms=["RS256"])
            assert claims["iss"] == "4242"
            if not self.instalada:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"id": 99, "permissions": self.permisos})
        if ruta == "/app/installations/99/access_tokens":
            self.tokens_pedidos.append(cuerpo)
            pedido = cuerpo["permissions"]
            if any(self.permisos.get(k) not in (v, "write") for k, v in pedido.items()):
                return httpx.Response(422, json={"message": "permisos no concedidos"})
            return httpx.Response(201, json={"token": "ghs_instalacion"})
        assert auth == "Bearer ghs_instalacion", f"{p.method} {ruta} sin token de instalación"
        if p.method == "GET" and ruta.startswith(f"{base}/contents/"):
            nombre = ruta.removeprefix(f"{base}/contents/")
            if p.url.params["ref"] != PROTEGIDA or nombre not in self.archivos:
                return httpx.Response(404, json={"message": "Not Found"})
            texto = self.archivos[nombre]
            return httpx.Response(
                200,
                json={
                    "type": "file",
                    "encoding": "base64",
                    "content": base64.encodebytes(texto.encode()).decode(),
                    "sha": _sha(texto),
                },
            )
        if p.method == "GET" and ruta == f"{base}/git/ref/heads/{PROTEGIDA}":
            return httpx.Response(200, json={"object": {"sha": "c" * 40}})
        if p.method == "POST" and ruta == f"{base}/git/refs":
            assert cuerpo["ref"] != f"refs/heads/{PROTEGIDA}"
            if cuerpo["ref"].removeprefix("refs/heads/") in self.ramas:
                return httpx.Response(422, json={"message": "Reference already exists"})
            self.ramas[cuerpo["ref"].removeprefix("refs/heads/")] = {}
            return httpx.Response(201, json={})
        if p.method == "PUT" and ruta.startswith(f"{base}/contents/"):
            assert cuerpo["branch"] != PROTEGIDA, "la consola no hace commit en la rama principal"
            self.ramas[cuerpo["branch"]][ruta.removeprefix(f"{base}/contents/")] = base64.b64decode(
                cuerpo["content"]
            ).decode()
            return httpx.Response(201, json={})
        if p.method == "POST" and ruta == f"{base}/pulls":
            if self.falla_pr:
                return httpx.Response(500, json={"message": "boom"})
            self.prs.append(cuerpo)
            return httpx.Response(201, json={"html_url": f"https://github.com/{NOMBRE}/pull/7", "number": 7})
        if p.method == "DELETE" and ruta.startswith(f"{base}/git/refs/heads/"):
            self.ramas.pop(ruta.removeprefix(f"{base}/git/refs/heads/"), None)
            return httpx.Response(204)
        return httpx.Response(404, json={"message": f"no simulado: {p.method} {ruta}"})


def _montaje(gh: GithubFalso | None = None, rol: Rol = Rol.workspace_admin, **extra) -> Montaje:
    config_app = APP
    repo = RepoGithub(config_app, gh.cliente()) if gh is not None else None
    m = Montaje(nivel=None, repo_github=repo, **extra)
    asignar(m.almacen, rol, ANA_ID)
    m.almacen.guardar_configuracion([vinculo(NivelCodigo.restringido)])
    return m


def _auditoria(m: Montaje) -> list[dict]:
    registros, _ = m.ctx.datos.auditoria(ORG, WS, evento="cambio-configuracion")
    return [r.model_dump(mode="json") for r in registros]


# --- validación ------------------------------------------------------------------------------------------


def test_contexto_valido_y_vacio():
    assert validar_contexto(CONTEXTO_VALIDO, ORG).ok
    assert validar_contexto("version: 1\n", ORG).ok
    v = validar_contexto("   \n", ORG)
    assert not v.ok and "vacío" in v.errores[0].mensaje


@pytest.mark.parametrize(
    ("texto", "linea", "fragmento"),
    [
        ("version: [\n", 2, "YAML inválido"),
        ("version: 1\nversion: 2\n", 2, "clave repetida"),
        ("a: &x 1\nb: *x\n", 1, "anclas"),
        ("- 1\n", 1, "mapa"),
        ("version: 2\n", 1, "version"),
        ("version: 1\nextra: 1\n", None, "extra"),
        ("version: 1\nproveedores:\n  - nombre: a\n    rol: docs\n", 3, "rol"),
        (CONTEXTO_VALIDO.replace("https://", "http://"), 5, "url"),
    ],
)
def test_contexto_invalido_dice_la_linea(texto, linea, fragmento):
    v = validar_contexto(texto, ORG)
    assert not v.ok
    assert any(fragmento in h.mensaje for h in v.errores)
    if linea is not None:
        assert linea in [h.linea for h in v.errores]


def test_contexto_gobernanza_blanda_y_repetidos():
    bloque = "  - nombre: g\n    rol: gobernanza\n    url: https://g.example/mcp\n    politica_fallo: %s\n"
    assert not validar_contexto("version: 1\nproveedores:\n" + bloque % "blanda", ORG).ok
    doble = "version: 1\nproveedores:\n" + bloque % "estricta" + bloque % "estricta"
    assert any("repetido" in h.mensaje for h in validar_contexto(doble, ORG).errores)


def test_contexto_avisa_host_y_namespace_sin_bloquear():
    texto = CONTEXTO_VALIDO + "    credencial_ref: secret://otra-org--x/clave\n"
    v = validar_contexto(texto, ORG)
    assert v.ok
    mensajes = " ".join(h.mensaje for h in v.avisos)
    assert "RAILSPEC_PROVEEDORES_HOSTS" in mensajes and "namespace" in mensajes


def test_contexto_no_explota_con_alias_anidados():
    bomba = "a: &a [x, x, x, x]\n" + "".join(
        f"{c}: &{c} [*{p}, *{p}, *{p}, *{p}]\n" for c, p in zip("bcdefghi", "abcdefgh", strict=True)
    )
    assert not validar_contexto(bomba, ORG).ok


def test_ignore():
    assert validar_ignore("# nota\n\nsecretos/\n**/*.pem\n").ok
    v = validar_ignore("a/\n!b\n../c\n**\n[x]\n")
    assert [h.linea for h in v.errores] == [2, 3]
    assert {h.linea for h in v.avisos} == {4, 5}
    assert not validar_ignore("x" * 300).ok
    assert not validar_ignore("\n".join(f"p{i}" for i in range(1001))).ok
    assert not validar_ignore("a" * (64 * 1024 + 1)).ok


def test_diff_unificado():
    d = diff_unificado("a\nb\n", "a\nc\n", "contexto.yaml")
    assert d.startswith("--- a/contexto.yaml\n+++ b/contexto.yaml\n") and "-b\n+c\n" in d
    assert diff_unificado(None, "x\n", "f").startswith("--- /dev/null\n+++ b/f\n")


# --- configuración -----------------------------------------------------------------------------------------


def test_config_de_la_app_como_instalacion():
    base = {
        "RAILSPEC_GITHUB_APP_CLIENT_ID": "i",
        "RAILSPEC_GITHUB_APP_CLIENT_SECRET": "s",
        "RAILSPEC_CONSOLA_URL": "http://localhost",
        "RAILSPEC_CONSOLA_SECRETO": "x" * 40,
    }
    assert not ConfigConsola.desde_entorno(base).github_app.puede_instalar
    completa = base | {
        "RAILSPEC_GITHUB_APP_ID": "123",
        "RAILSPEC_GITHUB_APP_CLAVE_PRIVADA": "-----BEGIN-----\\nabc\\n-----END-----",
    }
    app = ConfigConsola.desde_entorno(completa).github_app
    assert app.puede_instalar and "\n" in app.clave_privada and "abc" not in repr(app)
    with pytest.raises(ValueError, match="van juntas"):
        ConfigConsola.desde_entorno(base | {"RAILSPEC_GITHUB_APP_ID": "123"})
    with pytest.raises(ValueError, match="numérico"):
        ConfigConsola.desde_entorno(completa | {"RAILSPEC_GITHUB_APP_ID": "abc"})
    with pytest.raises(ValueError, match="CLIENT_ID"):
        ConfigConsola.desde_entorno(
            {"RAILSPEC_GITHUB_APP_ID": "123", "RAILSPEC_GITHUB_APP_CLAVE_PRIVADA": "k"}
        )


# --- lectura -----------------------------------------------------------------------------------------------


def test_leer_archivo_existente_y_ausente():
    async def caso():
        gh = GithubFalso()
        gh.archivos["contexto.yaml"] = CONTEXTO_VALIDO
        m = _montaje(gh)
        async with m.cliente("tk-ana") as c:
            r = await c.get(f"{RUTA}/contexto")
            assert r.status_code == 200, r.text
            d = r.json()
            assert d["existe"] and d["contenido"] == CONTEXTO_VALIDO and d["sha"] == _sha(CONTEXTO_VALIDO)
            assert d["modo"] == "pr" and d["motivo_manual"] is None
            assert d["repositorio"] == NOMBRE and d["rama"] == "main" and d["ruta"] == "contexto.yaml"
            # Un token de solo lectura: la lectura no pide permisos de escritura.
            assert gh.tokens_pedidos[-1]["permissions"] == {"contents": "read"}
            assert gh.tokens_pedidos[-1]["repositories"] == ["certificados-api"]

            r = await c.get(f"{RUTA}/ignore")
            assert (
                r.json()["existe"] is False
                and r.json()["contenido"] == ""
                and "gitignore" in r.json()["plantilla"]
            )

    correr(caso())


def test_sin_credenciales_instalada_o_sin_escritura_la_edicion_es_manual():
    async def caso():
        async def modo(m: Montaje) -> dict:
            async with m.cliente("tk-ana") as c:
                return (await c.get(f"{RUTA}/contexto")).json()

        sin = await modo(_montaje(None))
        assert sin["modo"] == "manual" and sin["existe"] is None and "credenciales" in sin["motivo_manual"]

        no_instalada = await modo(_montaje(GithubFalso(instalada=False)))
        assert no_instalada["modo"] == "manual" and "no está instalada" in no_instalada["motivo_manual"]

        solo_lectura = GithubFalso(permisos={"contents": "read"})
        solo_lectura.archivos["contexto.yaml"] = CONTEXTO_VALIDO
        lectura = await modo(_montaje(solo_lectura))
        assert lectura["modo"] == "manual" and lectura["existe"] and "escritura" in lectura["motivo_manual"]

        m = _montaje(None)
        m.ctx.repo_github = RepoGithub(ConfigGithubApp("Iv1.x", "secreto"), httpx.Client())  # App sin clave
        assert (await modo(m))["modo"] == "manual"

    correr(caso())


def test_error_de_github_en_la_lectura_es_502_sin_detalles():
    async def caso():
        gh = GithubFalso()
        gh.archivos["contexto.yaml"] = "\xff"
        m = _montaje(gh)
        gh.cliente = lambda: httpx.Client(
            transport=httpx.MockTransport(lambda p: httpx.Response(500, json={"message": "secreto interno"}))
        )
        m.ctx.repo_github = RepoGithub(APP, gh.cliente())
        async with m.cliente("tk-ana") as c:
            r = await c.get(f"{RUTA}/contexto")
        assert r.status_code == 502 and "secreto interno" not in r.text

    correr(caso())


def test_permisos_y_rutas():
    async def caso():
        gh = GithubFalso()
        for rol in (Rol.desarrollador, Rol.lector):
            m = _montaje(gh, rol=rol)
            async with m.cliente("tk-ana") as c:
                assert (await c.get(f"{RUTA}/contexto")).status_code == 403
                r = await c.post(
                    f"{RUTA}/contexto/proponer", json={"contenido": "version: 1\n"}, headers=CSRF
                )
                assert r.status_code == 403
        m = _montaje(gh)
        async with m.cliente("tk-luis") as c:  # sin rol alguno
            assert (await c.get(f"{RUTA}/contexto")).status_code == 403
        async with m.cliente("tk-ana") as c:
            assert (await c.get(f"{RUTA}/otro")).status_code == 404  # lista cerrada de archivos
            assert (await c.get(f"{RUTA.replace(REPO, 'no-existe')}/contexto")).status_code == 404
            sin_csrf = await c.post(f"{RUTA}/contexto/validar", json={"contenido": "version: 1\n"})
            assert sin_csrf.status_code == 403
        assert LUIS_ID != ANA_ID
        assert not gh.prs

    correr(caso())


def test_validar_por_ruta():
    async def caso():
        m = _montaje(None)
        async with m.cliente("tk-ana") as c:
            r = await c.post(
                f"{RUTA}/contexto/validar", json={"contenido": "version: 1\nversion: 2"}, headers=CSRF
            )
            assert r.status_code == 200 and r.json()["ok"] is False and r.json()["errores"][0]["linea"] == 2
            r = await c.post(f"{RUTA}/ignore/validar", json={"contenido": "a/\n"}, headers=CSRF)
            assert r.json() == {"ok": True, "errores": [], "avisos": []}

    correr(caso())


# --- propuesta ---------------------------------------------------------------------------------------------


def test_proponer_abre_un_pr_en_una_rama_nueva_y_audita_sin_el_contenido():
    async def caso():
        gh = GithubFalso()
        gh.archivos["contexto.yaml"] = "version: 1\n"
        m = _montaje(gh)
        async with m.cliente("tk-ana") as c:
            r = await c.post(
                f"{RUTA}/contexto/proponer",
                json={
                    "contenido": CONTEXTO_VALIDO.replace("\n", "\r\n"),
                    "sha_base": _sha("version: 1\n"),
                    "motivo": "docs internas",
                },
                headers=CSRF,
            )
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["modo"] == "pr" and d["pr_url"] == f"https://github.com/{NOMBRE}/pull/7" and d["numero"] == 7
        assert d["rama"].startswith("railspec/contexto-yaml-") and "+  - nombre: docs" in d["diff"]
        # Un solo commit en la rama nueva, con finales de línea normalizados; la principal queda intacta.
        assert gh.ramas == {d["rama"]: {"contexto.yaml": CONTEXTO_VALIDO}}
        assert gh.archivos == {"contexto.yaml": "version: 1\n"}
        (pr,) = gh.prs
        assert (
            pr["base"] == "main"
            and pr["head"] == d["rama"]
            and "ana" in pr["body"]
            and "docs internas" in pr["body"]
        )
        assert gh.tokens_pedidos[-1]["permissions"] == {"contents": "write", "pull_requests": "write"}
        (evento,) = _auditoria(m)
        assert evento["repositorio"] == REPO and evento["actor"]["login"] == "ana"
        assert evento["detalle"] == {
            "entidad": "archivo-repositorio",
            "accion": "proponer",
            "archivo": "contexto.yaml",
            "sha256": hashlib.sha256(CONTEXTO_VALIDO.encode()).hexdigest()[:16],
            "motivo": "docs internas",
            "pr": f"https://github.com/{NOMBRE}/pull/7",
        }
        assert "nombre: docs" not in json.dumps(evento)

    correr(caso())


def test_proponer_archivo_nuevo_sin_sha():
    async def caso():
        gh = GithubFalso()
        m = _montaje(gh)
        async with m.cliente("tk-ana") as c:
            r = await c.post(f"{RUTA}/ignore/proponer", json={"contenido": "secretos/"}, headers=CSRF)
        assert r.status_code == 200, r.text
        assert r.json()["rama"].startswith("railspec/railspecignore-") and r.json()["diff"].startswith(
            "--- /dev/null"
        )
        (rama,) = gh.ramas.values()
        assert rama == {".railspecignore": "secretos/\n"}

    correr(caso())


def test_proponer_invalido_es_422_con_lineas_y_no_toca_github():
    async def caso():
        gh = GithubFalso()
        m = _montaje(gh)
        async with m.cliente("tk-ana") as c:
            r = await c.post(f"{RUTA}/ignore/proponer", json={"contenido": "a/\n!b\n"}, headers=CSRF)
        assert r.status_code == 422
        assert r.json()["errores"] == [{"ruta": "línea 2", "mensaje": r.json()["errores"][0]["mensaje"]}]
        assert gh.llamadas == [] and _auditoria(m) == []

    correr(caso())


def test_proponer_con_el_archivo_cambiado_o_sin_cambios():
    async def caso():
        gh = GithubFalso()
        gh.archivos["contexto.yaml"] = "version: 1\n"
        m = _montaje(gh)
        async with m.cliente("tk-ana") as c:
            r = await c.post(
                f"{RUTA}/contexto/proponer",
                json={"contenido": CONTEXTO_VALIDO, "sha_base": _sha("version: 0\n")},
                headers=CSRF,
            )
            assert r.status_code == 409 and "cambió" in r.json()["detalle"]
            r = await c.post(
                f"{RUTA}/contexto/proponer",
                json={"contenido": "version: 1", "sha_base": _sha("version: 1\n")},
                headers=CSRF,
            )
            assert r.status_code == 422 and "no hay cambios" in r.json()["detalle"]
        assert not gh.prs and not gh.ramas

    correr(caso())


def test_proponer_dos_veces_lo_mismo_no_duplica_la_rama():
    async def caso():
        gh = GithubFalso()
        m = _montaje(gh)
        async with m.cliente("tk-ana") as c:
            cuerpo = {"contenido": "version: 1\n"}
            assert (await c.post(f"{RUTA}/contexto/proponer", json=cuerpo, headers=CSRF)).status_code == 200
            gh.archivos.clear()  # la rama base sigue sin el archivo
            r = await c.post(f"{RUTA}/contexto/proponer", json=cuerpo, headers=CSRF)
        assert r.status_code == 409 and "propuesta" in r.json()["detalle"]
        assert len(gh.prs) == 1

    correr(caso())


def test_si_falla_el_pr_se_borra_la_rama_y_no_se_audita():
    async def caso():
        gh = GithubFalso()
        gh.falla_pr = True
        m = _montaje(gh)
        async with m.cliente("tk-ana") as c:
            r = await c.post(f"{RUTA}/contexto/proponer", json={"contenido": "version: 1\n"}, headers=CSRF)
        assert r.status_code == 502 and "boom" not in r.text
        assert gh.ramas == {} and _auditoria(m) == []

    correr(caso())


def test_modo_manual_devuelve_el_diff_y_no_audita():
    async def caso():
        gh = GithubFalso(permisos={"contents": "read"})
        gh.archivos["contexto.yaml"] = "version: 1\n"
        m = _montaje(gh)
        async with m.cliente("tk-ana") as c:
            r = await c.post(
                f"{RUTA}/contexto/proponer",
                json={"contenido": CONTEXTO_VALIDO, "sha_base": _sha("version: 1\n")},
                headers=CSRF,
            )
        assert r.status_code == 200, r.text
        d = r.json()
        assert (
            d["modo"] == "manual" and "escritura" in d["motivo_manual"] and d["contenido"] == CONTEXTO_VALIDO
        )
        assert "+  - nombre: docs" in d["diff"] and d["ruta"] == "contexto.yaml"
        assert not gh.prs and gh.ramas == {} and _auditoria(m) == []

        m2 = _montaje(None)  # sin credenciales: no hay con qué calcular el diff, pero sí el contenido
        async with m2.cliente("tk-ana") as c:
            r = await c.post(f"{RUTA}/contexto/proponer", json={"contenido": CONTEXTO_VALIDO}, headers=CSRF)
        assert r.json()["modo"] == "manual" and r.json()["diff"] is None

    correr(caso())
