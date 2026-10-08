"""Adaptadores de Claude Code y OpenCode, reglas de rutas y detección de secretos."""

from __future__ import annotations

import json
import time

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
        [
            ".claude/commands/railspec.md",
            ".claude/skills/railspec-bucle/SKILL.md",
            ".mcp.json",
            ".claude/settings.json",
            ".claude/settings.local.json",
            "CLAUDE.md",
        ]
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
    assert (tmp_path / ".opencode" / "skills" / "railspec-bucle" / "SKILL.md").is_file()
    assert "argument-hint" not in (tmp_path / ".opencode" / "commands" / "railspec.md").read_text()
    assert datos["permission"] == {f"railspec_{t}": "ask" for t in adaptadores.TOOLS_HUMANAS}
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


#: Líneas de unos 64 000 caracteres que hacían cuadráticas a ``cadena-conexion`` y a ``jwt``:
#: antes de acotar los patrones, de 1,4 a 8,7 s cada una; ahora, de 20 a 110 ms.
_LINEAS_PATOLOGICAS = {
    "a.": "a." * 32_000,
    "esquema-largo": "a.a.a.a.a.a.a.a.a.a.a.a.a.a.a.a.://b:" * 1_700,
    "usuario-sin-clave": "a://b:" * 10_600,
    "cabecera-jwt": "eyJ-" * 16_000,
}


@pytest.mark.parametrize("nombre", sorted(_LINEAS_PATOLOGICAS))
def test_una_linea_hecha_para_estancar_las_expresiones_regulares_no_estanca_al_proxy(nombre):
    texto = _LINEAS_PATOLOGICAS[nombre]
    inicio = time.perf_counter()
    assert secretos.escanear("x.py", texto.encode()) == []
    assert secretos.redactar(texto) == texto
    assert time.perf_counter() - inicio < 1.0


@pytest.mark.parametrize(
    ("linea", "clave"),
    [
        ("mongodb+srv://root:hunter2hunter2@cluster0.mongodb.net/app?retryWrites=true", "hunter2hunter2"),
        ("postgresql+asyncpg://app:p%40ssw0rd%21@db.internal:5432/app", "p%40ssw0rd%21"),
        ("git clone https://x-access-token:ghs_notarealtoken@github.com/org/repo.git", "ghs_notarealtoken"),
    ],
)
def test_las_cadenas_de_conexion_de_siempre_se_siguen_detectando_y_redactando(linea, clave):
    assert [h.tipo for h in secretos.escanear("x.py", linea.encode())] == ["cadena-conexion"]
    redactada = secretos.redactar(linea)
    assert "[secreto:cadena-conexion]" in redactada and clave not in redactada


# --- permisos, desinstalación y paso de instalación completo ------------------------------


def test_claude_code_permite_el_bucle_y_pregunta_lo_humano(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(npm test)"]}, "hooks": {"Stop": []}}), encoding="utf-8"
    )
    worktrees = tmp_path.parent / "repo.railspec"
    adaptadores.instalar(tmp_path, Arnes.claude_code, worktrees)

    ajustes = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert ajustes["hooks"] == {"Stop": [], "PreToolUse": [adaptadores.HOOK_CLAUDE_CODE]}
    assert ajustes["permissions"]["allow"][0] == "Bash(npm test)"
    assert "mcp__railspec__unit_advance" in ajustes["permissions"]["allow"]
    assert ajustes["permissions"]["ask"] == [
        "mcp__railspec__unit_approve",
        "mcp__railspec__unit_set_mode",
        "mcp__railspec__unit_integrate",
        "mcp__railspec__mandate_revoke",
    ]
    assert not set(ajustes["permissions"]["allow"]) & set(ajustes["permissions"]["ask"])
    local = json.loads((tmp_path / ".claude" / "settings.local.json").read_text())
    assert local == {
        "enabledMcpjsonServers": ["railspec"],
        "permissions": {"additionalDirectories": [str(worktrees)]},
    }
    assert adaptadores.verificar(tmp_path, Arnes.claude_code, worktrees) == []
    assert adaptadores.verificar(tmp_path, Arnes.claude_code, tmp_path / "otra") == [
        ".claude/settings.local.json"
    ]


def test_tools_de_los_permisos_son_las_del_proxy():
    import asyncio

    from mcp import Client
    from railspec.local.servidor_mcp import crear_servidor

    async def nombres():
        async with Client(crear_servidor(lambda: None)) as cliente:
            return {t.name for t in (await cliente.list_tools()).tools}

    permisos = adaptadores.TOOLS_AUTOMATICAS + adaptadores.TOOLS_HUMANAS
    assert len(set(permisos)) == len(permisos)
    assert set(permisos) == asyncio.run(nombres())


def test_desinstalar_claude_code_devuelve_los_archivos_a_como_estaban(tmp_path):
    originales = {
        ".mcp.json": json.dumps({"mcpServers": {"otro": {"type": "stdio", "command": "x"}}}, indent=2) + "\n",
        ".claude/settings.json": json.dumps({"permissions": {"allow": ["Bash(npm test)"]}}, indent=2) + "\n",
        ".claude/commands/propio.md": "mío\n",
        "CLAUDE.md": "# Proyecto\n\nNotas propias.\n",
    }
    for ruta, texto in originales.items():
        (tmp_path / ruta).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / ruta).write_text(texto, encoding="utf-8")
    worktrees = tmp_path / "wt"

    adaptadores.instalar(tmp_path, Arnes.claude_code, worktrees)
    assert adaptadores.instalados(tmp_path) == [Arnes.claude_code]
    cambios = adaptadores.desinstalar(tmp_path, Arnes.claude_code, worktrees)

    assert ".claude/settings.local.json" in cambios
    for ruta, texto in originales.items():
        assert (tmp_path / ruta).read_text() == texto, ruta
    assert not (tmp_path / ".claude" / "skills").exists()
    assert not (tmp_path / ".claude" / "settings.local.json").exists()
    assert adaptadores.instalados(tmp_path) == []
    assert adaptadores.desinstalar(tmp_path, Arnes.claude_code, worktrees) == []


def test_desinstalar_en_repositorio_limpio_no_deja_rastro(tmp_path):
    for arnes in (Arnes.claude_code, Arnes.opencode):
        adaptadores.instalar(tmp_path, arnes, tmp_path / "wt")
    for arnes in (Arnes.claude_code, Arnes.opencode):
        adaptadores.desinstalar(tmp_path, arnes, tmp_path / "wt")
    assert list(tmp_path.iterdir()) == []


def test_bloque_de_reglas_sale_de_en_medio_y_del_principio(tmp_path):
    reglas = tmp_path / "AGENTS.md"
    bloque = adaptadores.BloqueReglas("AGENTS.md", "reglas")
    reglas.write_text(f"antes\n{bloque.bloque}después\n", encoding="utf-8")
    assert bloque.desinstalar(tmp_path)
    assert reglas.read_text() == "antes\ndespués\n"
    reglas.write_text(f"{bloque.bloque}\n# Resto\n", encoding="utf-8")
    assert bloque.desinstalar(tmp_path)
    assert reglas.read_text() == "# Resto\n"


def test_opencode_fusiona_en_opencode_json_existente(tmp_path):
    (tmp_path / "opencode.json").write_text(
        json.dumps({"model": "azure/gpt-5", "permission": "allow"}), encoding="utf-8"
    )
    cambios = adaptadores.instalar(tmp_path, Arnes.opencode)
    assert "opencode.json" in cambios and not (tmp_path / "opencode.jsonc").exists()
    datos = json.loads((tmp_path / "opencode.json").read_text())
    # `"permission": "allow"` es la abreviatura de `{"*": "allow"}`; la última regla que coincide gana.
    assert datos["permission"] == {
        "*": "allow",
        **{f"railspec_{t}": "ask" for t in adaptadores.TOOLS_HUMANAS},
    }
    assert datos["model"] == "azure/gpt-5"

    adaptadores.desinstalar(tmp_path, Arnes.opencode)
    datos = json.loads((tmp_path / "opencode.json").read_text())
    assert datos == {
        "$schema": "https://opencode.ai/config.json",
        "model": "azure/gpt-5",
        "permission": {"*": "allow"},
    }


def test_opencode_respeta_permiso_editado_y_quita_rutas_antiguas(tmp_path):
    for antigua in (".opencode/command/railspec.md", ".opencode/skill/railspec-bucle/SKILL.md"):
        (tmp_path / antigua).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / antigua).write_text("versión anterior\n", encoding="utf-8")
    adaptadores.instalar(tmp_path, Arnes.opencode)
    assert not (tmp_path / ".opencode" / "command").exists()
    assert not (tmp_path / ".opencode" / "skill").exists()

    ruta = tmp_path / "opencode.jsonc"
    datos = adaptadores.leer_jsonc(ruta.read_text())
    datos["permission"]["railspec_unit_integrate"] = "deny"
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    assert adaptadores.verificar(tmp_path, Arnes.opencode) == ["opencode.jsonc"]

    adaptadores.desinstalar(tmp_path, Arnes.opencode)
    assert adaptadores.leer_jsonc(ruta.read_text()) == {
        "$schema": "https://opencode.ai/config.json",
        "permission": {"railspec_unit_integrate": "deny"},
    }


def test_cli_instalar_y_desinstalar_deja_el_arbol_limpio(tmp_path, capsys, monkeypatch):
    from local_fabricas import repo_git, sh

    raiz = repo_git(tmp_path / "repo")
    monkeypatch.delenv(config.ENV_WORKTREES, raising=False)
    base = [
        "--repo", str(raiz), "instalar", "--org", "acme", "--workspace", "w", "--repositorio", "r",
        "--arnes", "claude-code", "--arnes", "opencode",
    ]  # fmt: skip
    assert cli.main(base) == 0
    salida = json.loads(capsys.readouterr().out)
    assert "avisos" not in salida or not any("OpenCode" in a for a in salida["avisos"])
    local = json.loads((raiz / ".claude" / "settings.local.json").read_text())
    assert local["permissions"]["additionalDirectories"] == [str(tmp_path / "repo.railspec")]
    # La configuración por máquina no aparece como cambio a versionar.
    pendientes = sh(raiz, "status", "--porcelain", "--untracked-files=all")
    assert ".claude/settings.local.json" not in pendientes and ".claude/settings.json" in pendientes

    assert cli.main(["--repo", str(raiz), "desinstalar", "--config"]) == 0
    salida = json.loads(capsys.readouterr().out)
    assert set(salida["cambios"]) == {"claude-code", "opencode"}
    assert sh(raiz, "status", "--porcelain", "--untracked-files=all") == ""
