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
