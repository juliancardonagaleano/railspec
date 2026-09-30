"""Adaptadores de Claude Code y OpenCode, reglas de rutas y detección de secretos."""

from __future__ import annotations

import json

import pytest
from railspec.contracts.comun import Arnes
from railspec.local import adaptadores, cli, config, secretos
from railspec.local.rutas import coincide


def test_instalar_claude_code_preserva_lo_ajeno_y_es_idempotente(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"otro": {"type": "stdio", "command": "x"}}}), encoding="utf-8"
    )
    (tmp_path / "CLAUDE.md").write_text("# Proyecto\n\nNotas propias.\n", encoding="utf-8")

    cambios = adaptadores.instalar(tmp_path, Arnes.claude_code)
    assert sorted(cambios) == sorted(
        [".claude/commands/railspec.md", ".claude/skills/railspec-bucle/SKILL.md", ".mcp.json", "CLAUDE.md"]
    )
    mcp = json.loads((tmp_path / ".mcp.json").read_text())
    assert mcp["mcpServers"]["otro"] == {"type": "stdio", "command": "x"}
    assert mcp["mcpServers"]["railspec"] == {"type": "stdio", "command": "railspec", "args": ["mcp"]}
    claude = (tmp_path / "CLAUDE.md").read_text()
    assert claude.startswith("# Proyecto\n\nNotas propias.\n")
    assert claude.count(adaptadores.INICIO_BLOQUE) == 1

    assert adaptadores.instalar(tmp_path, Arnes.claude_code) == []
    assert adaptadores.verificar(tmp_path, Arnes.claude_code) == []


def test_bloque_de_reglas_se_reemplaza_en_su_sitio(tmp_path):
    ruta = tmp_path / "CLAUDE.md"
    ruta.write_text(
        f"antes\n{adaptadores.INICIO_BLOQUE}\nviejo\n{adaptadores.FIN_BLOQUE}\ndespués\n", encoding="utf-8"
    )
    adaptadores.instalar(tmp_path, Arnes.claude_code)
    texto = ruta.read_text()
    assert texto.startswith("antes\n") and texto.endswith("después\n") and "viejo" not in texto
    assert "Railspec: reglas de conducta" in texto


def test_instalar_opencode_lee_jsonc_con_comentarios(tmp_path):
    (tmp_path / "opencode.jsonc").write_text(
        '{\n  // comentario\n  "$schema": "https://opencode.ai/config.json",\n'
        '  "mcp": { "pce": { "type": "local", "command": ["sh", "x // no es comentario"] }, },\n'
        '  /* bloque */ "agent": {}\n}\n',
        encoding="utf-8",
    )
    adaptadores.instalar(tmp_path, Arnes.opencode)
    datos = adaptadores.leer_jsonc((tmp_path / "opencode.jsonc").read_text())
    assert datos["mcp"]["pce"]["command"] == ["sh", "x // no es comentario"]
    assert datos["mcp"]["railspec"] == {"type": "local", "command": ["railspec", "mcp"], "enabled": True}
    assert (tmp_path / ".opencode" / "skill" / "railspec-bucle" / "SKILL.md").is_file()
    assert adaptadores.INICIO_BLOQUE in (tmp_path / "AGENTS.md").read_text()
    assert adaptadores.verificar(tmp_path, Arnes.opencode) == []


def test_verificar_detecta_deriva(tmp_path):
    adaptadores.instalar(tmp_path, Arnes.claude_code)
    (tmp_path / ".claude" / "skills" / "railspec-bucle" / "SKILL.md").write_text("editado\n")
    assert adaptadores.verificar(tmp_path, Arnes.claude_code) == [".claude/skills/railspec-bucle/SKILL.md"]


def test_json_invalido_no_se_pisa(tmp_path):
    (tmp_path / ".mcp.json").write_text("{ roto", encoding="utf-8")
    with pytest.raises(Exception, match="no es JSON válido"):
        adaptadores.instalar(tmp_path, Arnes.claude_code)
    assert (tmp_path / ".mcp.json").read_text() == "{ roto"


def test_cli_instalar_escribe_config_y_adaptadores(tmp_path, capsys):
    from local_fabricas import repo_git

    raiz = repo_git(tmp_path / "repo")
    codigo = cli.main(
        [
            "--repo",
            str(raiz),
            "instalar",
            "--org",
            "acme",
            "--workspace",
            "certificados",
            "--repositorio",
            "certificados-api",
            "--arnes",
            "claude-code",
            "--arnes",
            "opencode",
        ]
    )
    assert codigo == 0
    salida = json.loads(capsys.readouterr().out)
    assert salida["nivel_codigo"] == "restringido"
    repo = config.leer_config_repositorio(raiz)
    assert (repo.org, repo.workspace, repo.repositorio, repo.arnes) == (
        "acme",
        "certificados",
        "certificados-api",
        Arnes.claude_code,
    )
    assert cli.main(["--repo", str(raiz), "instalar", "--verificar", "--arnes", "opencode"]) == 0


def test_cli_sin_url_explica_que_falta(tmp_path, capsys, monkeypatch):
    from local_fabricas import repo_git

    raiz = repo_git(tmp_path / "repo")
    config.escribir_config_repositorio(
        raiz, config.ConfigRepositorio(org="acme", workspace="w", repositorio="r")
    )
    monkeypatch.delenv(config.ENV_URL, raising=False)
    assert cli.main(["--repo", str(raiz), "estado"]) == 1
    assert "RAILSPEC_URL" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("ruta", "patrones", "esperado"),
    [
        ("src/pdf.py", ["src/**"], True),
        ("src/a/b/pdf.py", ["src/**"], True),
        ("tests/test_pdf.py", ["src/**"], False),
        ("src/pdf.py", ["src/pdf.py"], True),
        ("otro/src/pdf.py", ["src/pdf.py"], False),
        ("a/b/.env", [".env"], True),
        ("node_modules/x/y.js", ["node_modules/"], True),
        ("app/node_modules/x.js", ["node_modules/"], True),
        ("src/x.pem", ["*.pem"], True),
        ("src/pdf.py", ["*.md"], False),
        ("docs/a/b.md", ["docs/**/*.md"], True),
        ("docs/b.md", ["docs/**/*.md"], True),
    ],
)
def test_coincidencia_de_rutas(ruta, patrones, esperado):
    assert coincide(ruta, patrones) is esperado


@pytest.mark.parametrize(
    ("linea", "tipo"),
    [
        ("-----BEGIN RSA PRIVATE KEY-----", "clave-privada"),
        ("token = 'ghp_" + "a" * 36 + "'", "github-token"),
        ("url = 'mongodb://admin:s3cr3tpass@db:27017'", "cadena-conexion"),
        ('password = "correcthorsebattery"', "asignacion-secreto"),
        ("DefaultEndpointsProtocol=https;AccountKey=" + "A" * 44, "azure-storage"),
    ],
)
def test_detecta_secretos(linea, tipo):
    assert [h.tipo for h in secretos.escanear("x.py", linea.encode())] == [tipo]


@pytest.mark.parametrize(
    "linea",
    [
        'password = os.environ["DB_PASSWORD"]',
        'password = "${DB_PASSWORD}"',
        "def suma(a, b): return a + b",
        "url = 'https://example.com/path'",
    ],
)
def test_no_marca_codigo_normal(linea):
    assert secretos.escanear("x.py", linea.encode()) == []


def test_redacta_secretos_en_salida_libre():
    texto = secretos.redactar("fallo con AKIAABCDEFGHIJKLMNOP en el log")
    assert "AKIA" not in texto and "[secreto:aws-access-key]" in texto
