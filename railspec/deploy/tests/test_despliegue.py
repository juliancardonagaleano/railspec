"""Pruebas del renderizado de manifiestos y del reindexado desde CI."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from railspec.contracts.comun import AlcanceRepositorio
from railspec.contracts.snapshot import (
    Arista,
    DeltaIndice,
    MotorIndice,
    Relacion,
    Simbolo,
    TipoSimbolo,
    id_simbolo,
)

DEPLOY = Path(__file__).resolve().parents[1]


def _cargar(nombre: str, ruta: Path):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


renderizar = _cargar("railspec_deploy_renderizar", DEPLOY / "renderizar.py")
reindexar = _cargar("railspec_deploy_reindexar", DEPLOY / "ci" / "reindexar.py")

MINIMO = {
    "RAILSPEC_IMAGEN": "registro.azurecr.io/railspec-server@sha256:" + "a" * 64,
    "RAILSPEC_DOMINIO": "railspec.example.com",
    "RAILSPEC_TLS_SECRETO": "railspec-tls",
}

# --- renderizado ----------------------------------------------------------------------


def test_render_sustituye_todo_y_deriva_workload_identity():
    texto = renderizar.renderizar(MINIMO)
    assert "${" not in texto
    assert MINIMO["RAILSPEC_IMAGEN"] in texto
    assert 'azure.workload.identity/use: "false"' in texto
    # El hash de sesión MCP del ingress no es una variable del renderizador.
    assert "$http_mcp_session_id" in texto
    con_id = renderizar.renderizar({**MINIMO, "RAILSPEC_AZURE_CLIENT_ID": "0000-cliente"})
    assert 'azure.workload.identity/use: "true"' in con_id


def test_render_falla_sin_obligatorias():
    with pytest.raises(renderizar.ErrorRender, match="RAILSPEC_IMAGEN"):
        renderizar.renderizar({k: v for k, v in MINIMO.items() if k != "RAILSPEC_IMAGEN"})
    with pytest.raises(renderizar.ErrorRender, match="REPLICAS"):
        renderizar.renderizar({**MINIMO, "RAILSPEC_REPLICAS": "dos"})


def test_render_proveedores_en_configmap():
    yaml = pytest.importorskip("yaml")
    texto = renderizar.renderizar(
        {
            **MINIMO,
            "RAILSPEC_FOUNDRY_REGION": "eastus2",
            "RAILSPEC_FOUNDRY_ZONA_DATOS": "us",
            "RAILSPEC_FOUNDRY_DESPLIEGUES": "opus=claude-opus-5-5:DataZoneStandard,gpt=gpt-5:Standard",
        }
    )
    mapa = next(d for d in yaml.safe_load_all(texto) if d and d["kind"] == "ConfigMap")["data"]
    assert mapa["RAILSPEC_FOUNDRY_REGION"] == "eastus2"
    assert mapa["RAILSPEC_FOUNDRY_DESPLIEGUES"].startswith("opus=claude-opus-5-5:DataZoneStandard")
    assert mapa["RAILSPEC_FOUNDRY_PROYECTO"] == ""
    assert mapa["RAILSPEC_CATALOGO_TTL_S"] == "3600"
    assert mapa["RAILSPEC_CACHE_NODOS_S"] == "86400"
    assert mapa["RAILSPEC_CONTEXTO_CACHE_S"] == "900"


def test_render_rechaza_despliegues_json_y_ttl_no_numerico():
    with pytest.raises(renderizar.ErrorRender, match="forma despliegue=modelo"):
        renderizar.renderizar(
            {**MINIMO, "RAILSPEC_FOUNDRY_DESPLIEGUES": '[{"despliegue": "o", "modelo": "claude-opus-5-5"}]'}
        )
    with pytest.raises(renderizar.ErrorRender, match="CATALOGO_TTL_S"):
        renderizar.renderizar({**MINIMO, "RAILSPEC_CATALOGO_TTL_S": "una hora"})


def test_render_rechaza_variable_desconocida(tmp_path):
    (tmp_path / "x.yaml").write_text("a: ${RAILSPEC_NO_EXISTE}\n")
    with pytest.raises(renderizar.ErrorRender, match="desconocida"):
        renderizar.renderizar(MINIMO, tmp_path)


def test_render_produce_yaml_valido():
    yaml = pytest.importorskip("yaml")
    documentos = [d for d in yaml.safe_load_all(renderizar.renderizar(MINIMO)) if d]
    tipos = sorted(d["kind"] for d in documentos)
    assert tipos == sorted(
        [
            "Namespace",
            "ServiceAccount",
            "ConfigMap",
            "Deployment",
            "Service",
            "Ingress",
            "PodDisruptionBudget",
        ]
    )
    despliegue = next(d for d in documentos if d["kind"] == "Deployment")
    assert despliegue["spec"]["replicas"] == 2


# --- lotes ----------------------------------------------------------------------------

MOTOR = MotorIndice(version="0.11.0")


def _id(i: int) -> str:
    return id_simbolo("repo", "a.py", "funcion", f"f{i}")


def _simbolo(i: int) -> Simbolo:
    return Simbolo(
        id=_id(i),
        nombre=f"f{i}",
        tipo=TipoSimbolo.funcion,
        ruta="a.py",
        linea_inicio=1,
        linea_fin=2,
        sha256="0" * 64,
    )


def test_partir_reparte_por_lista_y_siempre_da_un_lote():
    assert len(reindexar.partir(DeltaIndice(motor=MOTOR), 10)) == 1
    delta = DeltaIndice(
        motor=MOTOR,
        simbolos_upsert=[_simbolo(i) for i in range(25)],
        simbolos_borrados=[_id(99)],
        aristas_agregadas=[Arista(origen=_id(0), destino=_id(1), relacion=Relacion.llama)],
    )
    lotes = reindexar.partir(delta, 10)
    assert [len(x.simbolos_upsert) for x in lotes] == [10, 10, 5]
    assert [len(x.simbolos_borrados) for x in lotes] == [1, 0, 0]
    assert sum(len(x.aristas_agregadas) for x in lotes) == 1


# --- git ------------------------------------------------------------------------------


def _repo(tmp_path: Path) -> tuple[Path, list[str]]:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], capture_output=True, check=True, text=True
        ).stdout.strip()

    git("init", "-q", "-b", "master")
    git("config", "user.email", "ci@example.com")
    git("config", "user.name", "ci")
    commits = []
    for i, archivo in enumerate(["a.py", "b.py", "c.py"]):
        (tmp_path / archivo).write_text(f"def f{i}():\n    return {i}\n")
        git("add", archivo)
        git("commit", "-q", "-m", archivo)
        commits.append(git("rev-parse", "HEAD"))
    return tmp_path, commits


def test_planificar_delta_y_completo(tmp_path):
    raiz, (c1, c2, c3) = _repo(tmp_path)
    delta = reindexar.planificar(raiz, c3, c1)
    assert delta.rutas == ["b.py", "c.py"] and delta.commit_anterior == c1 and delta.base == c1
    for anterior in (None, reindexar.CERO, "f" * 40, c3):
        completo = reindexar.planificar(raiz, c3, anterior)
        assert completo.completo and completo.rutas == ["a.py", "b.py", "c.py"]
    # anterior que no es ancestro (force-push): completo
    assert reindexar.planificar(raiz, c1, c3).completo


# --- flujo con dobles -----------------------------------------------------------------


class IndexadorDoble:
    def __init__(self) -> None:
        self.llamadas: list[tuple[str, list[str]]] = []

    def delta(self, worktree, repositorio, base_commit, rutas, excluir):
        self.llamadas.append((base_commit, rutas))
        return DeltaIndice(motor=MOTOR, simbolos_upsert=[_simbolo(i) for i in range(len(rutas))])


class ServidorDoble:
    def __init__(self, respuestas: list[tuple[int, dict]] | None = None) -> None:
        self.cuerpos: list[dict] = []
        self.respuestas = list(respuestas or [])

    def __call__(self, url, cuerpo, token):
        assert url == "https://railspec.example.com/v1/tools/graph.index"
        assert token == "jwt"
        self.cuerpos.append(cuerpo)
        if self.respuestas:
            return self.respuestas.pop(0)
        return 200, {
            "commit": cuerpo["commit"],
            "lotes_recibidos": cuerpo["lote"],
            "aplicado": cuerpo["lote"] == cuerpo["lotes"],
        }


ALCANCE = AlcanceRepositorio(org="acme", workspace="ws", repositorio="repo")


def _correr(raiz, commit, anterior, servidor, indexador=None):
    return reindexar.reindexar(
        raiz,
        ALCANCE,
        "master",
        commit,
        anterior,
        "https://railspec.example.com/",
        lambda: "jwt",
        indexador or IndexadorDoble(),
        [],
        tamano=1,
        transporte=servidor,
        espera_s=0,
    )


def test_reindexar_delta_por_lotes(tmp_path):
    raiz, (c1, _, c3) = _repo(tmp_path)
    servidor = ServidorDoble()
    salida = _correr(raiz, c3, c1, servidor)
    assert salida.aplicado and salida.commit == c3
    assert [(c["lote"], c["lotes"], c["commit_anterior"]) for c in servidor.cuerpos] == [
        (1, 2, c1),
        (2, 2, c1),
    ]
    assert servidor.cuerpos[0]["alcance"] == {"org": "acme", "workspace": "ws", "repositorio": "repo"}


def test_reindexar_reintenta_completo_si_el_canonico_esta_desfasado(tmp_path):
    raiz, (c1, _, c3) = _repo(tmp_path)
    servidor = ServidorDoble(
        [(409, {"codigo": "base-commit-distinto", "detalle": "canónico en otro commit"})]
    )
    indexador = IndexadorDoble()
    salida = _correr(raiz, c3, c1, servidor, indexador)
    assert salida.aplicado
    assert len(indexador.llamadas) == 2 and indexador.llamadas[1][1] == ["a.py", "b.py", "c.py"]
    assert "commit_anterior" not in servidor.cuerpos[-1]


def test_reindexar_reintenta_5xx_y_no_4xx(tmp_path):
    raiz, (_, _, c3) = _repo(tmp_path)
    servidor = ServidorDoble([(503, {}), (503, {})])
    assert _correr(raiz, c3, None, servidor).aplicado
    rechazo = ServidorDoble([(403, {"codigo": "fuera-de-alcance", "detalle": "no"})])
    with pytest.raises(reindexar.RechazoServidor, match="403"):
        _correr(raiz, c3, None, rechazo)
    assert len(rechazo.cuerpos) == 1


def test_token_oidc_exige_permiso():
    with pytest.raises(reindexar.ErrorReindexado, match="id-token"):
        reindexar.TokenOidc("railspec", {})


def test_render_politica_de_contexto_y_vinculos_en_configmap():
    yaml = pytest.importorskip("yaml")
    por_defecto = renderizar.renderizar(MINIMO)
    mapa = next(d for d in yaml.safe_load_all(por_defecto) if d and d["kind"] == "ConfigMap")["data"]
    assert mapa["RAILSPEC_VINCULOS_OWNERS"] == ""  # sin allowlist, una organización sin github_org no vincula
    texto = renderizar.renderizar({**MINIMO, "RAILSPEC_VINCULOS_OWNERS": "acme, acme-labs"})
    mapa = next(d for d in yaml.safe_load_all(texto) if d and d["kind"] == "ConfigMap")["data"]
    assert mapa["RAILSPEC_VINCULOS_OWNERS"] == "acme, acme-labs"
    with pytest.raises(renderizar.ErrorRender, match="VINCULOS_OWNERS"):
        renderizar.renderizar({**MINIMO, "RAILSPEC_VINCULOS_OWNERS": 'acme"\n  X: "y'})
