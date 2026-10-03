"""Despliegue en Render: Blueprint, workflow y script manual (railspec/docs/despliegue-render.md)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[1]
RAIZ = DEPLOY.parent
REPO = RAIZ.parent
BLUEPRINT = REPO / "render.yaml"
BLUEPRINT_GHCR = DEPLOY / "render" / "render-ghcr.yaml"
DOCKERFILE = DEPLOY / "servidor" / "Dockerfile"
AMBOS = pytest.mark.parametrize("blueprint", [BLUEPRINT, BLUEPRINT_GHCR], ids=["docker", "ghcr"])
IMAGEN_WORKFLOW = REPO / ".github" / "workflows" / "railspec-imagen.yml"
SCRIPT = DEPLOY / "render" / "desplegar.sh"
GUIA = RAIZ / "docs" / "despliegue-render.md"

#: Variables que el servidor lee, más ``FORWARDED_ALLOW_IPS`` (uvicorn) y ``RENDER_EXTERNAL_URL`` (Render).
SECRETAS = {
    "RAILSPEC_POSTGRES_URL",
    "RAILSPEC_FOUNDRY_API_KEY",
    "RAILSPEC_GITHUB_APP_CLIENT_ID",
    "RAILSPEC_GITHUB_APP_CLIENT_SECRET",
    "RAILSPEC_CONSOLA_SECRETO",
}


def _servicio(blueprint: Path = BLUEPRINT) -> dict:
    datos = yaml.safe_load(blueprint.read_text(encoding="utf-8"))
    (servicio,) = datos["services"]
    return servicio


def _variables_del_servidor() -> set[str]:
    patron = re.compile(r"\bRAILSPEC_[A-Z0-9]+(?:_[A-Z0-9]+)*\b")
    fuentes = [
        f
        for paquete in ("railspec-server", "railspec-graph")
        for f in (RAIZ / "packages" / paquete / "src").rglob("*.py")
    ]
    assert fuentes
    return {nombre for f in fuentes for nombre in patron.findall(f.read_text(encoding="utf-8"))}


def test_el_blueprint_por_defecto_construye_el_dockerfile_del_repositorio_en_el_plan_gratuito():
    s = _servicio()
    assert s["type"] == "web" and s["runtime"] == "docker" and s["plan"] == "free"
    assert s["healthCheckPath"] == "/healthz"
    assert (REPO / s["dockerfilePath"]).resolve() == DOCKERFILE.resolve()
    assert s["dockerContext"] == "." and s["branch"] == "master"
    assert s["autoDeployTrigger"] == "commit"
    # El filtro de construcción cubre lo que entra en la imagen; cada ruta existe.
    rutas = s["buildFilter"]["paths"]
    assert rutas and all(list(REPO.glob(r)) for r in rutas), rutas
    for paquete in ("railspec-contracts", "railspec-graph", "railspec-server", "railspec-console"):
        assert f"railspec/packages/{paquete}/**" in rutas, paquete


def test_la_variante_ghcr_es_un_servicio_web_gratuito_con_imagen_de_ghcr_y_sonda_de_salud():
    s = _servicio(BLUEPRINT_GHCR)
    assert s["type"] == "web" and s["runtime"] == "image" and s["plan"] == "free"
    assert s["healthCheckPath"] == "/healthz"
    # El hook de CI fija el digest; la etiqueta móvil solo es el valor inicial (mismo repositorio en ambos).
    assert s["image"]["url"] == "ghcr.io/juliancardonagaleano/railspec-server:master"
    assert s["image"]["creds"]["fromRegistryCreds"]["name"] == "ghcr-railspec"
    assert s["autoDeployTrigger"] == "off"  # sin comillas, YAML 1.1 lo leería como False


def test_el_workflow_publica_en_ghcr_con_la_misma_ruta_que_el_blueprint():
    wf = IMAGEN_WORKFLOW.read_text(encoding="utf-8")
    assert "ghcr.io/${GITHUB_REPOSITORY_OWNER,,}/railspec-server" in wf
    assert "type=raw,value=master" in wf  # la etiqueta del Blueprint existe


@AMBOS
def test_cada_variable_del_blueprint_la_lee_el_servidor_y_los_secretos_no_llevan_valor(blueprint):
    claves = {}
    for v in _servicio(blueprint)["envVars"]:
        clave = v["key"]
        claves[clave] = v
        assert clave in _variables_del_servidor() or clave == "FORWARDED_ALLOW_IPS", clave
    for clave in SECRETAS & claves.keys():
        v = claves[clave]
        # Un secreto se pide en el panel (sync: false) o lo genera Render; nunca va escrito en el repositorio.
        assert "value" not in v and (v.get("sync") is False or v.get("generateValue") is True), clave
    # Lo mínimo para que el servidor arranque sobre Postgres y la consola funcione con GitHub App.
    assert {
        "RAILSPEC_POSTGRES_URL",
        "RAILSPEC_CONSOLA_SECRETO",
        "RAILSPEC_GITHUB_APP_CLIENT_ID",
    } <= claves.keys()
    # Sin Mongo: el servidor no arranca con las dos bases a la vez.
    assert "RAILSPEC_MONGO_URI" not in claves


@AMBOS
def test_el_puerto_del_blueprint_es_el_que_escucha_el_servidor(blueprint):
    puerto = next(v["value"] for v in _servicio(blueprint)["envVars"] if v["key"] == "RAILSPEC_PUERTO")
    assert puerto.isdigit() and 1024 <= int(puerto) <= 65535


def test_el_job_render_corre_solo_en_master_con_ghcr_y_sin_imprimir_el_hook():
    wf = yaml.safe_load(IMAGEN_WORKFLOW.read_text(encoding="utf-8"))
    job = wf["jobs"]["render"]
    assert job["needs"] == "imagen"
    condicion = " ".join(job["if"].split())
    for parte in (
        "github.event_name != 'pull_request'",
        "github.ref == 'refs/heads/master'",
        "vars.RAILSPEC_RENDER_URL != ''",
        "vars.RAILSPEC_ACR_NOMBRE == ''",
    ):
        assert parte in condicion, parte
    assert job["concurrency"]["cancel-in-progress"] is False
    assert wf["jobs"]["imagen"]["outputs"]["referencia"]
    texto = yaml.safe_dump(job)
    assert "secrets.RENDER_DEPLOY_HOOK_URL" in texto and "needs.imagen.outputs.referencia" in texto
    assert "desplegar.sh" in texto
    # El hook es un secreto: ni echo ni set -x.
    pasos = "\n".join(p.get("run", "") for p in job["steps"])
    assert 'echo "$RENDER_DEPLOY_HOOK_URL"' not in pasos and "set -x" not in pasos


def test_el_script_manual_rechaza_referencias_ajenas_y_pasa_el_digest_codificado(tmp_path):
    digest = "a" * 64
    curl = tmp_path / "curl"
    curl.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {tmp_path}/args\n', encoding="utf-8")
    curl.chmod(0o755)
    entorno = {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "RENDER_DEPLOY_HOOK_URL": "https://api.render.com/deploy/srv-x?key=k",
    }
    base = "ghcr.io/juliancardonagaleano/railspec-server"
    ok = subprocess.run(
        ["sh", str(SCRIPT), f"{base}@sha256:{digest}"], env=entorno, capture_output=True, text=True
    )
    assert ok.returncode == 0, ok.stderr
    args = (tmp_path / "args").read_text(encoding="utf-8").split()
    assert (
        "-G" in args
        and f"imgURL={base}@sha256:{digest}" in args
        and entorno["RENDER_DEPLOY_HOOK_URL"] in args
    )
    for mala in (f"ghcr.io/otro/imagen@sha256:{digest}", f"{base}:master", f"{base}@sha256:{'b' * 63}"):
        r = subprocess.run(["sh", str(SCRIPT), mala], env=entorno, capture_output=True, text=True)
        assert r.returncode != 0 and "referencia inválida" in r.stderr, mala
    sin_hook = subprocess.run(
        ["sh", str(SCRIPT), "x"], env={"PATH": entorno["PATH"]}, capture_output=True, text=True
    )
    assert sin_hook.returncode != 0 and "RENDER_DEPLOY_HOOK_URL" in sin_hook.stderr


def test_la_guia_documenta_las_variables_y_los_secretos_de_ambos_caminos():
    guia = GUIA.read_text(encoding="utf-8")
    for nombre in (
        "RENDER_DEPLOY_HOOK_URL",
        "RAILSPEC_RENDER_URL",
        "ghcr-railspec",
        "desplegar.sh",
        "render-ghcr.yaml",
    ):
        assert nombre in guia, nombre
    for blueprint in (BLUEPRINT, BLUEPRINT_GHCR):
        for v in _servicio(blueprint)["envVars"]:
            assert f"`{v['key']}`" in guia, v["key"]


@AMBOS
def test_el_blueprint_no_trae_secretos_en_claro(blueprint):
    texto = blueprint.read_text(encoding="utf-8")
    assert not re.search(r"(postgres(ql)?://\S+:\S+@|ghp_|github_pat_|sk-[A-Za-z0-9]{16,})", texto)
