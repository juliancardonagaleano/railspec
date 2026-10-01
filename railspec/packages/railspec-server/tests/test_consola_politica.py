"""Consola: login de GitHub, vínculos de repositorio, política de workspace y proveedores de contexto."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from apoyo_motor import JULIAN, ORG, REPO, WS, vinculo
from railspec.contracts.comun import NivelCodigo
from railspec.contracts.repositorio import Auditoria, Organizacion, Rol, VinculoRepositorio
from railspec.server.api.grafo import repositorio_de_url
from railspec.server.chat.codigo import ClonesGit
from railspec.server.consola.github import ClienteGithub
from test_consola import ANA_ID, CSRF, Montaje, asignar

AHORA = datetime(2026, 9, 30, 12, tzinfo=UTC)


def correr(coro):
    return asyncio.run(coro)


def _crear_org(m, github_org: str | None) -> None:
    previa = m.ctx.datos.organizacion(ORG)
    m.ctx.datos.guardar_organizacion(
        Organizacion(
            id=ORG,
            nombre="ACME",
            github_org=github_org,
            region_datos="eastus2",
            version=(previa.version + 1) if previa else 1,
            auditoria=Auditoria(
                creado_por=JULIAN, creado_en=AHORA, actualizado_por=JULIAN, actualizado_en=AHORA
            ),
        ),
        previa.version if previa else None,
    )


# --- B6: ``login`` de GitHub ---------------------------------------------------------------------------


def _github_espia():
    llamadas: list[str] = []

    def responder(peticion: httpx.Request) -> httpx.Response:
        llamadas.append(str(peticion.url))
        return httpx.Response(200, json={"id": 4242, "login": "octocat"})

    return llamadas, httpx.Client(transport=httpx.MockTransport(responder))


LOGINS_MALOS = ["../orgs/microsoft", "a/b", "-guion", "con espacio", "a" * 40, "x?y=1", "a%2e%2e", "a#b", "ñ"]


def test_id_de_login_rechaza_logins_que_no_son_de_github_sin_llamar():
    llamadas, http = _github_espia()
    cliente = ClienteGithub(None, http)
    for login in LOGINS_MALOS:
        assert cliente.id_de_login(login) is None, login
    assert llamadas == []
    assert cliente.id_de_login("octocat") == 4242 and llamadas == ["https://api.github.com/users/octocat"]
    assert cliente.id_de_login("a-b-1") == 4242


def test_asignar_rol_por_login_valida_el_formato_en_la_entrada():
    async def caso():
        llamadas, http = _github_espia()
        m = Montaje(github_http=http)
        asignar(m.almacen, Rol.org_admin, ANA_ID, workspace=None)
        async with m.cliente("tk-ana") as c:
            for login in LOGINS_MALOS:
                r = await c.post(
                    f"/consola/api/orgs/{ORG}/roles",
                    json={"workspace": WS, "rol": "lector", "sujeto": {"tipo": "usuario", "login": login}},
                    headers=CSRF,
                )
                assert r.status_code == 422, (login, r.text)
            assert llamadas == []
            r = await c.post(
                f"/consola/api/orgs/{ORG}/roles",
                json={"workspace": WS, "rol": "lector", "sujeto": {"tipo": "usuario", "login": "octocat"}},
                headers=CSRF,
            )
            assert r.status_code == 200, r.text
            assert r.json()["sujeto"] == {"tipo": "usuario", "github_id": 4242}
            assert llamadas == ["https://api.github.com/users/octocat"]

    correr(caso())


# --- M1: URL del vínculo de repositorio ----------------------------------------------------------------

BASE_VINCULOS = f"/consola/api/orgs/{ORG}/workspaces/{WS}/repositorios"
CUERPO_VINCULO = {"url": "https://github.com/acme/certificados-api", "rol": "primario"}

URLS_MALAS = [
    "https://evil.example/acme/certificados-api",
    "https://github.com.evil.example/acme/certificados-api",
    "https://user:pw@github.com/acme/certificados-api",
    "https://github.com@evil.example/acme/certificados-api",
    "https://github.com:8443/acme/certificados-api",
    "http://github.com/acme/certificados-api",
    "https://github.com/acme/certificados-api?x=1",
    "https://github.com/acme/certificados-api#frag",
    "https://github.com/acme/certificados-api/tree/main",
    "https://github.com/acme",
    "https://github.com/",
    "https://github.com/../..",
    "https://github.com/acme/..",
    "https://github.com/acme/.",
    "https://github.com/acme/.git",
    "https://github.com/acme/a b",
    "https://github.com/acme/a%2f..%2fb",
    "https://github.com/acme/api\n",
    "https://github.com\\@evil.example/acme/api",
    "https://github.com/-acme/api",
    "https://github.com/" + "a" * 40 + "/api",
]


@pytest.mark.parametrize(
    ("url", "esperado"),
    [
        ("https://github.com/acme/api", "acme/api"),
        ("https://github.com/acme/api.git", "acme/api"),
        ("https://github.com/acme/api/", "acme/api"),
        ("https://github.com/Acme-Corp/mi.repo_1", "Acme-Corp/mi.repo_1"),
        ("https://github.com/acme/.github", "acme/.github"),
    ],
)
def test_repositorio_de_url_acepta_solo_owner_y_repo_de_github(url, esperado):
    assert repositorio_de_url(url) == esperado


def test_repositorio_de_url_rechaza_todo_lo_demas():
    for url in URLS_MALAS:
        assert repositorio_de_url(url) is None, url


def _git(*args, cwd):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True
    )


def _repo_con(ruta: Path, archivo: str, texto: str) -> str:
    ruta.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", "-b", "main", cwd=ruta)
    (ruta / archivo).write_text(texto)
    _git("add", ".", cwd=ruta)
    _git("commit", "-q", "-m", "x", cwd=ruta)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ruta, check=True, capture_output=True, text=True
    ).stdout.strip()


def _vinculo(url: str) -> VinculoRepositorio:
    return vinculo(NivelCodigo.restringido).model_copy(update={"url": url})


def test_clones_git_no_sale_de_la_carpeta_de_clones(tmp_path):
    raiz = tmp_path / "a" / "b" / "clones"
    raiz.mkdir(parents=True)
    commit = _repo_con(tmp_path / "a", "secreto.txt", "de otro tenant")  # raiz/../.. es un repo
    clones = ClonesGit(raiz)
    # ``https://github.com/../..`` daba ``../..``: el clon de ``raiz/../..``.
    traversal = _vinculo("https://github.com/../..")
    assert clones.leer(traversal, commit, "secreto.txt") is None
    assert clones.commit_canonico(traversal) is None
    # Un enlace simbólico dentro de la raíz que apunta fuera tampoco vale.
    fuera = tmp_path / "fuera"
    commit2 = _repo_con(fuera / "api", "x.txt", "fuera")
    (raiz / "acme").mkdir()
    (raiz / "acme" / "api").symlink_to(fuera / "api", target_is_directory=True)
    assert clones.leer(_vinculo("https://github.com/acme/api"), commit2, "x.txt") is None


def test_clones_git_lee_el_clon_del_owner_y_repo(tmp_path):
    raiz = tmp_path / "clones"
    commit = _repo_con(raiz / "acme" / "api", "src.py", "print(1)\n")
    clones = ClonesGit(raiz)
    v = _vinculo("https://github.com/acme/api.git")
    assert clones.leer(v, commit, "src.py") == "print(1)\n"
    assert clones.commit_canonico(v) == commit


def test_vinculo_solo_acepta_github_del_owner_de_la_organizacion():
    async def caso():
        m = Montaje(nivel=None)
        _crear_org(m, "acme")
        asignar(m.almacen, Rol.workspace_admin, ANA_ID)
        async with m.cliente("tk-ana") as c:
            for url in URLS_MALAS:
                r = await c.put(f"{BASE_VINCULOS}/{REPO}", json=CUERPO_VINCULO | {"url": url}, headers=CSRF)
                assert r.status_code == 422, (url, r.text)
            # Repositorio de otro tenant en GitHub: el owner no es el de la organización.
            r = await c.put(
                f"{BASE_VINCULOS}/{REPO}",
                json=CUERPO_VINCULO | {"url": "https://github.com/victima/repo-privado"},
                headers=CSRF,
            )
            assert r.status_code == 422 and "acme" in r.json()["detalle"], r.text
            assert m.almacen.vinculos(ORG, WS) == []
            r = await c.put(
                f"{BASE_VINCULOS}/{REPO}",
                json=CUERPO_VINCULO | {"url": "https://github.com/ACME/certificados-api.git"},
                headers=CSRF,
            )
            assert r.status_code == 200, r.text

    correr(caso())


def test_vinculo_sin_github_org_falla_cerrado_salvo_allowlist_de_plataforma(monkeypatch):
    async def caso():
        m = Montaje(nivel=None)
        _crear_org(m, None)
        asignar(m.almacen, Rol.workspace_admin, ANA_ID)
        async with m.cliente("tk-ana") as c:
            r = await c.put(f"{BASE_VINCULOS}/{REPO}", json=CUERPO_VINCULO, headers=CSRF)
            assert r.status_code == 422 and "github_org" in r.json()["detalle"], r.text
            monkeypatch.setenv("RAILSPEC_VINCULOS_OWNERS", "otra-org, ACME")
            r = await c.put(f"{BASE_VINCULOS}/{REPO}", json=CUERPO_VINCULO, headers=CSRF)
            assert r.status_code == 200, r.text
            # Con ``github_org`` definido, la allowlist de plataforma no amplía lo que puede vincular.
            _crear_org(m, "acme")
            r = await c.put(
                f"{BASE_VINCULOS}/otro-repo",
                json=CUERPO_VINCULO | {"url": "https://github.com/otra-org/api"},
                headers=CSRF,
            )
            assert r.status_code == 422, r.text

    correr(caso())


def test_github_org_solo_lo_cambia_la_plataforma():
    async def caso():
        m = Montaje()
        _crear_org(m, None)
        asignar(m.almacen, Rol.org_admin, ANA_ID, workspace=None)
        edicion = {"nombre": "ACME", "region_datos": "eastus2", "github_org": "victima", "version": 1}
        async with m.cliente("tk-ana") as c:
            r = await c.put(f"/consola/api/orgs/{ORG}", json=edicion, headers=CSRF)
            assert r.status_code == 403 and "plataforma" in r.json()["detalle"], r.text
            r = await c.put(f"/consola/api/orgs/{ORG}", json=edicion | {"github_org": "../x"}, headers=CSRF)
            assert r.status_code == 422
            sin_tocar = edicion | {"github_org": None, "nombre": "ACME 2"}
            r = await c.put(f"/consola/api/orgs/{ORG}", json=sin_tocar, headers=CSRF)
            assert r.status_code == 200, r.text  # sin tocar github_org, edita lo demás
        async with m.cliente("tk-julian") as c:
            r = await c.put(f"/consola/api/orgs/{ORG}", json=edicion | {"version": 2}, headers=CSRF)
            assert r.status_code == 200 and r.json()["github_org"] == "victima", r.text

    correr(caso())


# --- B5: relajar la política exige org-admin, motivo y deja el diff en la auditoría ----------------------


async def _auditoria(c, ws: str = WS, **filtros) -> list[dict]:
    r = await c.get(f"/consola/api/orgs/{ORG}/workspaces/{ws}/auditoria", params=filtros)
    assert r.status_code == 200, r.text
    return r.json()["registros"]


def _politica(vinculo_json: dict, **cambios) -> dict:
    return {**vinculo_json["chat_contexto_codigo"], **cambios}


def test_relajar_la_politica_del_vinculo_exige_org_admin_y_motivo():
    async def caso():
        m = Montaje(nivel=None)
        _crear_org(m, "acme")
        asignar(m.almacen, Rol.workspace_admin, ANA_ID)
        url = f"{BASE_VINCULOS}/{REPO}"
        async with m.cliente("tk-ana") as c:
            r = await c.put(url, json=CUERPO_VINCULO, headers=CSRF)
            assert r.status_code == 200, r.text
            v = r.json()
            version = v["version"]

            async def put(cliente, politica=None, **extra):
                nonlocal version
                cuerpo = CUERPO_VINCULO | {"version": version} | extra
                if politica is not None:
                    cuerpo["chat_contexto_codigo"] = politica
                r = await cliente.put(url, json=cuerpo, headers=CSRF)
                if r.status_code == 200:
                    version = r.json()["version"]
                return r

            # Endurecer sigue en manos del workspace-admin, sin motivo, pero queda auditado con su diff.
            r = await put(c, _politica(v, huella_tokens_n=8, presupuesto_fuga_usuario_dia=3000))
            assert r.status_code == 200, r.text
            ultimo = (await _auditoria(c))[0]
            assert ultimo["evento"] == "cambio-configuracion" and "relaja" not in ultimo["detalle"]
            assert ultimo["detalle"]["cambio_chat_huella_tokens_n"] == "12 -> 8"
            assert ultimo["detalle"]["cambio_chat_presupuesto_fuga_usuario_dia"] == "6000 -> 3000"
            v = r.json()
            # Relajar sin cambiar el nivel: el workspace-admin no puede, ni con motivo.
            for cambio in (
                {"huella_tokens_n": 24},
                {"presupuesto_fuga_conversacion": 9999},
                {"presupuesto_fuga_usuario_dia": 6001},
            ):
                r = await put(c, _politica(v, **cambio), motivo="lo necesito")
                assert r.status_code == 403, (cambio, r.text)
            # Apagar y volver a encender el chat: encender es relajar.
            r = await put(c, _politica(v, permitido=False))
            assert r.status_code == 200, r.text
            v = r.json()
            r = await put(c, _politica(v, permitido=True), motivo="otra vez")
            assert r.status_code == 403
            # Modelos: restringir la lista endurece; ampliarla o vaciarla (= todos) relaja.
            r = await put(c, _politica(v, modelos_permitidos=["gpt-5"]))
            assert r.status_code == 200, r.text
            v = r.json()
            for modelos in (["gpt-5", "claude-sonnet-5-5"], []):
                r = await put(c, _politica(v, modelos_permitidos=modelos), motivo="x")
                assert r.status_code == 403, (modelos, r.text)
            # Subir el nivel de política es relajar: el workspace-admin no puede, con motivo o sin él.
            r = await put(c, nivel_codigo="abierto", motivo="repo público")
            assert r.status_code == 403, r.text
            assert m.almacen.vinculos(ORG, WS)[0].nivel_codigo == NivelCodigo.restringido
        async with m.cliente("tk-julian") as c:  # administra la plataforma: actúa como org-admin
            # Sin motivo, ni siquiera el org-admin relaja.
            r = await put(c, _politica(v, huella_tokens_n=24))
            assert r.status_code == 422 and "motivo" in r.json()["detalle"], r.text
            r = await put(
                c, _politica(v, huella_tokens_n=24, permitido=True), motivo="  revisado con seguridad "
            )
            assert r.status_code == 200, r.text
            v = r.json()
            ultimo = (await _auditoria(c))[0]
            assert ultimo["actor"]["login"] == "juliancardonagaleano"
            assert ultimo["detalle"]["cambio_chat_huella_tokens_n"] == "8 -> 24"
            assert ultimo["detalle"]["cambio_chat_permitido"] == "false -> true"
            assert ultimo["detalle"]["relaja"] == "chat_permitido,chat_huella_tokens_n"
            assert ultimo["detalle"]["motivo"] == "revisado con seguridad"
            # Bajar el nivel: evento cambio-nivel con de/a/motivo y el diff del resto de la política.
            r = await put(c, nivel_codigo="abierto", motivo="repo público")
            assert r.status_code == 200, r.text
            ultimo = (await _auditoria(c))[0]
            assert ultimo["evento"] == "cambio-nivel"
            d = ultimo["detalle"]
            assert (d["de"], d["a"], d["motivo"]) == ("restringido", "abierto", "repo público")
            assert d["cambio_chat_hosting"] == "azure-zona-datos -> cualquiera"
            assert d["cambio_chat_fragmentos_en_respuesta"] == "false -> true"
            assert "nivel_codigo" in d["relaja"] and "cambio_nivel_codigo" not in d

    correr(caso())


def test_crear_un_vinculo_menos_restrictivo_que_el_por_defecto_tambien_es_relajar():
    async def caso():
        m = Montaje(nivel=None)
        _crear_org(m, "acme")
        asignar(m.almacen, Rol.workspace_admin, ANA_ID)
        abierto = CUERPO_VINCULO | {"nivel_codigo": "abierto", "motivo": "repo público"}
        async with m.cliente("tk-ana") as c:
            # Si no, bastaría desvincular y volver a crear en ``abierto`` para esquivar el cambio de nivel.
            r = await c.put(f"{BASE_VINCULOS}/{REPO}", json=abierto, headers=CSRF)
            assert r.status_code == 403, r.text
            assert m.almacen.vinculos(ORG, WS) == []
            r = await c.put(f"{BASE_VINCULOS}/{REPO}", json=CUERPO_VINCULO, headers=CSRF)
            assert r.status_code == 200, r.text
            assert "cambio_nivel_codigo" not in (await _auditoria(c))[0]["detalle"]  # igual al por defecto
            assert (
                await c.delete(f"{BASE_VINCULOS}/{REPO}", params={"motivo": "x"}, headers=CSRF)
            ).status_code == 204
        async with m.cliente("tk-julian") as c:
            r = await c.put(f"{BASE_VINCULOS}/{REPO}", json=abierto | {"motivo": None}, headers=CSRF)
            assert r.status_code == 422
            r = await c.put(f"{BASE_VINCULOS}/{REPO}", json=abierto, headers=CSRF)
            assert r.status_code == 200, r.text
            d = (await _auditoria(c))[0]["detalle"]
            assert d["accion"] == "crear" and d["cambio_nivel_codigo"] == "restringido -> abierto"
            assert d["motivo"] == "repo público" and "nivel_codigo" in d["relaja"]

    correr(caso())


def test_zona_de_datos_del_workspace_solo_se_amplia_con_org_admin_y_motivo():
    async def caso():
        m = Montaje()
        _crear_org(m, "acme")
        asignar(m.almacen, Rol.workspace_admin, ANA_ID)
        base = f"/consola/api/orgs/{ORG}/workspaces/{WS}"
        async with m.cliente("tk-julian") as c:
            r = await c.post(
                f"/consola/api/orgs/{ORG}/workspaces",
                json={"workspace": WS, "nombre": "Certificados", "zona_datos_azure": "us"},
                headers=CSRF,
            )
            assert r.status_code == 200, r.text
            assert (await _auditoria(c))[0]["detalle"]["zona_datos_azure"] == "us"
        edicion = {"nombre": "Certificados", "perfil_por_defecto": "estandar", "version": 1}
        async with m.cliente("tk-ana") as c:
            # Quitar la zona o cambiarla a otra no se puede probar que endurezca: relaja.
            for zona in (None, "eu", "global"):
                r = await c.put(base, json=edicion | {"zona_datos_azure": zona, "motivo": "x"}, headers=CSRF)
                assert r.status_code == 403, (zona, r.text)
            assert m.ctx.datos.workspace(ORG, WS).zona_datos_azure == "us"
            # Lo demás del workspace sigue siendo del workspace-admin (la zona igual, sin motivo).
            r = await c.put(base, json=edicion | {"zona_datos_azure": "us", "nombre": "Certs"}, headers=CSRF)
            assert r.status_code == 200, r.text
            ultimo = (await _auditoria(c))[0]
            assert (
                ultimo["detalle"]["accion"] == "editar" and "cambio_zona_datos_azure" not in ultimo["detalle"]
            )
        async with m.cliente("tk-julian") as c:
            e2 = edicion | {"nombre": "Certs", "version": 2}
            r = await c.put(base, json=e2 | {"zona_datos_azure": "eu"}, headers=CSRF)
            assert r.status_code == 422 and "motivo" in r.json()["detalle"], r.text
            r = await c.put(
                base, json=e2 | {"zona_datos_azure": "eu", "motivo": "migración a EU"}, headers=CSRF
            )
            assert r.status_code == 200, r.text
            d = (await _auditoria(c))[0]["detalle"]
            assert d["cambio_zona_datos_azure"] == "us -> eu" and d["relaja"] == "zona_datos_azure"
            assert d["motivo"] == "migración a EU"
            # Quitar la restricción de zona es ampliarla: motivo y org-admin.
            r = await c.put(
                base, json=e2 | {"zona_datos_azure": None, "version": 3, "motivo": "sin zona"}, headers=CSRF
            )
            assert r.status_code == 200, r.text
        async with m.cliente("tk-ana") as c:
            # Fijar una zona donde no había restringe: lo hace el workspace-admin, sin motivo.
            r = await c.put(base, json=e2 | {"zona_datos_azure": "eu", "version": 4}, headers=CSRF)
            assert r.status_code == 200, r.text
            d = (await _auditoria(c))[0]["detalle"]
            assert d["cambio_zona_datos_azure"] == "- -> eu" and "relaja" not in d

    correr(caso())
