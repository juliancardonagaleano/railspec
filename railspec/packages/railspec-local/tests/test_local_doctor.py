"""``railspec doctor``: diagnóstico de solo lectura contra un servidor doble y repositorios temporales."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from local_fabricas import ServidorDoble, crear_proxy
from railspec.contracts.comun import Arnes
from railspec.contracts.tools import CodigoError, ErrorTool
from railspec.local import adaptadores, cli, config, doctor, indexador_cbm
from railspec.local.adaptadores import usuario
from railspec.local.almacen import ARCHIVO_ESTADO
from railspec.local.cliente import ClienteServidor
from railspec.local.credenciales import AlmacenCredenciales, Credencial
from railspec.local.errores import ErrorServidor, RespuestaInvalida, ServidorRechazo

URL = "https://railspec.test/mcp"
AHORA = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
NOMBRES_OK = [
    "repositorio",
    "comando",
    "servidor",
    "sesion",
    "contrato",
    "adaptadores",
    "indexador",
    "worktrees",
]


def correr(coro):
    return asyncio.run(coro)


@dataclass
class Mundo:
    tmp: Path
    proxy: object
    servidor: ServidorDoble
    home: Path
    env: dict[str, str]

    @property
    def raiz(self) -> Path:
        return self.proxy.raiz

    def cliente(self, url, fuente):
        return ClienteServidor(self.servidor)

    def doctor(self, **kw) -> dict[str, doctor.Comprobacion]:
        resultado = correr(
            doctor.diagnosticar(
                self.raiz, self.cliente, entorno=self.env, home=self.home, ahora=lambda: AHORA, **kw
            )
        )
        return {c.nombre: c for c in resultado}

    def iniciar_unidad(self) -> Path:
        return Path(correr(self.proxy.iniciar("Corregir suma", "La suma resta."))["worktree"])

    def instalar(self, arnes: Arnes) -> None:
        adaptadores.instalar(self.raiz, arnes, config.dir_worktrees_por_defecto(self.raiz))


@pytest.fixture
def mundo(tmp_path, monkeypatch):
    servidor = ServidorDoble()
    proxy = crear_proxy(tmp_path, servidor)
    home = tmp_path / "home"
    home.mkdir()
    for variable in ("XDG_CONFIG_HOME", "CODEX_HOME", config.ENV_WORKTREES, config.ENV_CREDENCIALES):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(doctor, "shutil", SimpleNamespace(which=lambda c: f"/usr/local/bin/{c}"))
    monkeypatch.setattr(
        indexador_cbm,
        "diagnosticar",
        lambda binario=None: indexador_cbm.DiagnosticoBinario("/usr/bin/cbm", indexador_cbm.VERSION_FIJA),
    )
    env = {
        config.ENV_URL: URL,
        config.ENV_TOKEN: "ghu_secreto",
        config.ENV_CREDENCIALES: str(home / "creds.json"),
    }
    return Mundo(tmp_path, proxy, servidor, home, env)


def estados(resultado: dict[str, doctor.Comprobacion]) -> dict[str, str]:
    return {nombre: c.estado for nombre, c in resultado.items()}


def foto(carpeta: Path) -> dict[str, tuple]:
    """Archivo → (tamaño, mtime, modo): lo que cambiaría si algo escribiera."""

    cambios = {}
    for ruta in sorted(carpeta.rglob("*")):
        info = ruta.lstat()
        cambios[str(ruta.relative_to(carpeta))] = (info.st_size, info.st_mtime_ns, stat.S_IMODE(info.st_mode))
    return cambios


# --- todo bien -------------------------------------------------------------------------------------


def test_todo_en_orden(mundo):
    mundo.instalar(Arnes.claude_code)

    resultado = mundo.doctor()

    assert list(resultado) == NOMBRES_OK
    assert estados(resultado) == dict.fromkeys(NOMBRES_OK, "ok"), {n: c.detalle for n, c in resultado.items()}
    assert "RAILSPEC_TOKEN" in resultado["sesion"].detalle and "acepta" in resultado["sesion"].detalle
    assert "claude-code (repositorio)" in resultado["adaptadores"].detalle
    # Una sola llamada de negocio y de lectura: unit.list de ese repositorio, con límite 1.
    assert [t for t, _ in mundo.servidor.llamadas] == ["unit.list"]
    entrada = mundo.servidor.llamadas[0][1]
    assert entrada["repositorio"] == "certificados-api" and entrada["limite"] == 1
    assert not doctor.hay_fallos(list(resultado.values()))


def test_solo_lee_no_escribe_nada(mundo):
    mundo.instalar(Arnes.claude_code)
    mundo.instalar(Arnes.codex)
    worktree = mundo.iniciar_unidad()
    creds = mundo.home / "creds.json"
    AlmacenCredenciales(creds).guardar(
        URL,
        Credencial(access_token="ghu_x", client_id="Iv1", login="ana", obtenido_en=AHORA, expira_en=None),
    )
    creds.chmod(0o644)  # un archivo abierto: otros comandos lo cierran, doctor no
    mundo.env.pop(config.ENV_TOKEN)
    antes = foto(mundo.tmp)

    resultado = mundo.doctor()

    assert foto(mundo.tmp) == antes
    assert stat.S_IMODE(creds.stat().st_mode) == 0o644
    assert "lo pueden leer otros usuarios" in resultado["sesion"].detalle
    assert (worktree / ARCHIVO_ESTADO).is_file()


# --- repositorio y comando -------------------------------------------------------------------------


def test_fuera_de_un_repositorio_lo_dice_y_sigue_con_lo_demas(mundo, tmp_path_factory):
    nada = tmp_path_factory.mktemp("sin-repo")

    resultado = {
        c.nombre: c
        for c in correr(
            doctor.diagnosticar(nada, mundo.cliente, entorno=mundo.env, home=mundo.home, ahora=lambda: AHORA)
        )
    }

    assert (
        resultado["repositorio"].estado == "fallo"
        and "no está dentro de un repositorio" in resultado["repositorio"].detalle
    )
    assert resultado["servidor"].estado == "ok"
    assert (
        resultado["sesion"].estado == "aviso"
        and "sin la configuración del repositorio" in resultado["sesion"].detalle
    )
    assert "worktrees" not in resultado and [t for t, _ in mundo.servidor.llamadas] == []


def test_repositorio_sin_configuracion(mundo):
    (mundo.raiz / config.ARCHIVO_CONFIG).unlink()

    resultado = mundo.doctor()

    assert resultado["repositorio"].estado == "fallo"
    assert "railspec instalar" in resultado["repositorio"].remedio


def test_railspec_fuera_del_path(mundo, monkeypatch):
    monkeypatch.setattr(doctor, "shutil", SimpleNamespace(which=lambda c: None))

    resultado = mundo.doctor()

    assert resultado["comando"].estado == "fallo" and "PATH" in resultado["comando"].detalle


# --- servidor, sesión y contrato -------------------------------------------------------------------


def test_sin_url_no_hay_servidor_ni_llamadas(mundo):
    mundo.env.pop(config.ENV_URL)

    resultado = mundo.doctor()

    assert resultado["servidor"].estado == "fallo" and config.ENV_URL in resultado["servidor"].detalle
    assert resultado["sesion"].estado == "aviso"
    assert "contrato" not in resultado and mundo.servidor.llamadas == []


def test_servidor_caido(mundo):
    mundo.servidor.conectado = False

    resultado = mundo.doctor()

    assert resultado["servidor"].estado == "fallo" and "desconectado" in resultado["servidor"].detalle
    assert "contrato" not in resultado and mundo.servidor.llamadas == []


def test_servidor_que_no_responde_a_tiempo(mundo):
    mundo.servidor.demora_herramientas_s = 5

    resultado = mundo.doctor(tope_servidor_s=0.05)

    assert (
        resultado["servidor"].estado == "fallo" and "no respondió a tiempo" in resultado["servidor"].detalle
    )


def test_sin_sesion_no_se_prueba_nada_contra_el_servidor(mundo):
    mundo.env.pop(config.ENV_TOKEN)

    resultado = mundo.doctor()

    assert resultado["sesion"].estado == "fallo" and "no hay sesión iniciada" in resultado["sesion"].detalle
    assert resultado["sesion"].remedio == "`railspec login`"
    assert resultado["servidor"].estado == "ok"
    assert mundo.servidor.llamadas == []


def _guardar_sesion(mundo, expira_en):
    AlmacenCredenciales(Path(mundo.env[config.ENV_CREDENCIALES])).guardar(
        URL,
        Credencial(
            access_token="ghu_guardado", client_id="Iv1", login="ana", obtenido_en=AHORA, expira_en=expira_en
        ),
    )
    mundo.env.pop(config.ENV_TOKEN)


def test_sesion_guardada_vigente(mundo):
    _guardar_sesion(mundo, AHORA + timedelta(hours=6))

    sesion = mundo.doctor()["sesion"]

    assert sesion.estado == "ok" and "ana" in sesion.detalle and "vence 2026-10-02 18:00" in sesion.detalle


def test_sesion_que_vence_pronto_avisa(mundo):
    _guardar_sesion(mundo, AHORA + timedelta(minutes=20))

    sesion = mundo.doctor()["sesion"]

    assert sesion.estado == "aviso" and "vence el 2026-10-02 12:20" in sesion.detalle


def test_sesion_vencida_no_se_prueba(mundo):
    _guardar_sesion(mundo, AHORA - timedelta(minutes=5))

    resultado = mundo.doctor()

    assert (
        resultado["sesion"].estado == "fallo" and "venció el 2026-10-02 11:55" in resultado["sesion"].detalle
    )
    assert mundo.servidor.llamadas == []


def test_token_rechazado_por_el_servidor(mundo):
    mundo.servidor.fallos["unit.list"] = ServidorRechazo("unit.list", "el token no es de la GitHub App")

    sesion = mundo.doctor()["sesion"]

    assert sesion.estado == "fallo" and "el token no es de la GitHub App" in sesion.detalle


def test_rol_insuficiente(mundo):
    mundo.servidor.fallos["unit.list"] = ErrorServidor(
        ErrorTool(codigo=CodigoError.fuera_de_alcance, detalle="necesitas rol lector")
    )

    sesion = mundo.doctor()["sesion"]

    assert sesion.estado == "fallo" and "fuera-de-alcance: necesitas rol lector" in sesion.detalle


def test_servidor_al_que_le_falta_una_tool_del_contrato(mundo):
    publicadas = {t for t in correr(mundo.servidor.herramientas()) if t != "unit_import"}
    mundo.servidor.tools_publicadas = sorted(publicadas)

    contrato = mundo.doctor()["contrato"]

    assert contrato.estado == "fallo" and "no publica unit_import" in contrato.detalle


def test_servidor_con_tools_de_mas_avisa(mundo):
    mundo.servidor.tools_publicadas = [*correr(mundo.servidor.herramientas()), "unit_nueva"]

    contrato = mundo.doctor()["contrato"]

    assert contrato.estado == "aviso" and "unit_nueva" in contrato.detalle


def test_respuesta_fuera_de_contrato(mundo):
    mundo.servidor.fallos["unit.list"] = RespuestaInvalida("unit.list: no cumple el contrato")

    resultado = mundo.doctor()

    assert resultado["contrato"].estado == "fallo" and "no lo cumple" in resultado["contrato"].detalle
    assert "sesion" in resultado  # la sesión local sigue informada


def test_las_tools_esperadas_son_las_que_el_proxy_llama():
    """Si el proxy empieza a usar otra tool del contrato, el doctor tiene que esperarla."""

    fuentes = Path(doctor.__file__).parent
    usadas = set()
    for archivo in ("proxy.py", "portabilidad.py"):
        usadas |= set(re.findall(r'"((?:unit|sync|graph|insumo)\.[a-z_]+)"', (fuentes / archivo).read_text()))
    assert {t.replace(".", "_") for t in usadas} <= doctor.TOOLS_ESPERADAS


# --- adaptadores -----------------------------------------------------------------------------------


def test_sin_adaptadores_avisa(mundo):
    adaptador = mundo.doctor()["adaptadores"]

    assert adaptador.estado == "aviso" and "ningún arnés" in adaptador.detalle


def test_adaptador_con_deriva(mundo):
    mundo.instalar(Arnes.claude_code)
    (mundo.raiz / ".claude" / "commands" / "railspec.md").write_text("editado a mano\n", encoding="utf-8")

    adaptador = mundo.doctor()["adaptadores"]

    assert adaptador.estado == "fallo"
    assert adaptador.items == ("claude-code (repositorio): .claude/commands/railspec.md",)
    assert "railspec instalar --arnes" in adaptador.remedio


def test_adaptador_del_usuario_tambien_se_verifica(mundo):
    usuario.instalar(Arnes.opencode, home=mundo.home)
    assert mundo.doctor()["adaptadores"].estado == "ok"
    (mundo.home / ".config" / "opencode" / "commands" / "railspec.md").write_text("x\n", encoding="utf-8")

    adaptador = mundo.doctor()["adaptadores"]

    assert adaptador.estado == "fallo" and "opencode (usuario)" in adaptador.items[0]


# --- Codex -----------------------------------------------------------------------------------------


def _confiar(mundo, indice: int) -> None:
    clave = f"{mundo.raiz}/.codex/hooks.json:pre_tool_use:{indice}:0"
    codex = mundo.home / ".codex"
    codex.mkdir(exist_ok=True)
    (codex / "config.toml").write_text(
        f'model = "gpt-5.5"\n\n[hooks.state."{clave}"]\ntrusted_hash = "sha256:abc"\n', encoding="utf-8"
    )


def test_sin_codex_no_hay_comprobacion_de_codex(mundo):
    mundo.instalar(Arnes.claude_code)

    assert "codex" not in mundo.doctor()


def test_hook_de_codex_sin_confiar_es_un_fallo(mundo):
    mundo.instalar(Arnes.codex)

    codex = mundo.doctor()["codex"]

    assert codex.estado == "fallo" and "no está confiado" in codex.detalle and "/hooks" in codex.remedio


def test_hook_de_codex_confiado(mundo):
    mundo.instalar(Arnes.codex)
    _confiar(mundo, 0)

    codex = mundo.doctor()["codex"]

    assert codex.estado == "ok" and "No se puede comprobar" in codex.detalle


def test_la_confianza_es_del_indice_del_hook_en_hooks_json(mundo):
    mundo.instalar(Arnes.codex)
    ruta = mundo.raiz / ".codex" / "hooks.json"
    datos = json.loads(ruta.read_text())
    ajeno = {"matcher": "shell", "hooks": [{"type": "command", "command": "otro-hook"}]}
    datos["hooks"]["PreToolUse"].insert(0, ajeno)
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    _confiar(mundo, 0)  # confió el hook ajeno, no el de Railspec

    assert mundo.doctor()["codex"].estado == "fallo"

    _confiar(mundo, 1)
    assert mundo.doctor()["codex"].estado == "ok"


def test_codex_respeta_codex_home(mundo, tmp_path):
    mundo.instalar(Arnes.codex)
    otra = tmp_path / "codex-casa"
    otra.mkdir()
    clave = f"{mundo.raiz}/.codex/hooks.json:pre_tool_use:0:0"
    (otra / "config.toml").write_text(
        f'[hooks.state."{clave}"]\ntrusted_hash = "sha256:abc"\n', encoding="utf-8"
    )
    mundo.env["CODEX_HOME"] = str(otra)

    assert mundo.doctor()["codex"].estado == "ok"


# --- indexador -------------------------------------------------------------------------------------


def test_indexador_ausente_es_un_aviso(mundo, monkeypatch):
    monkeypatch.setattr(
        indexador_cbm, "diagnosticar", lambda binario=None: indexador_cbm.DiagnosticoBinario(None, None)
    )

    indexador = mundo.doctor()["indexador"]

    assert indexador.estado == "aviso" and f"=={indexador_cbm.VERSION_FIJA}" in indexador.remedio


def test_indexador_de_otra_version_es_un_fallo(mundo, monkeypatch):
    monkeypatch.setattr(
        indexador_cbm,
        "diagnosticar",
        lambda binario=None: indexador_cbm.DiagnosticoBinario("/usr/bin/cbm", "0.9.0"),
    )

    indexador = mundo.doctor()["indexador"]

    assert indexador.estado == "fallo"
    assert f"0.9.0 no es la versión fijada {indexador_cbm.VERSION_FIJA}" in indexador.detalle


# --- worktrees -------------------------------------------------------------------------------------


def test_worktrees_sanos(mundo):
    mundo.iniciar_unidad()

    assert mundo.doctor()["worktrees"].detalle == "1 unidad(es) local(es), sin huérfanos"


def test_worktree_cuya_carpeta_desaparecio(mundo):
    worktree = mundo.iniciar_unidad()
    shutil.rmtree(worktree)

    huerfanos = mundo.doctor()["worktrees"]

    assert huerfanos.estado == "aviso" and "git worktree prune" in huerfanos.items[0]
    assert "0001-sumar" in huerfanos.items[0]
    assert (mundo.raiz / ".git" / "worktrees").is_dir()  # doctor no la limpió


def test_worktree_sin_estado_local(mundo):
    worktree = mundo.iniciar_unidad()
    (worktree / ARCHIVO_ESTADO).unlink()

    huerfanos = mundo.doctor()["worktrees"]

    assert huerfanos.estado == "aviso" and f"git worktree remove {worktree}" in huerfanos.items[0]


def test_estado_local_roto_es_un_fallo(mundo):
    worktree = mundo.iniciar_unidad()
    (worktree / ARCHIVO_ESTADO).write_text("{ esto no es json", encoding="utf-8")

    roto = mundo.doctor()["worktrees"]

    assert roto.estado == "fallo" and "no se puede leer" in roto.items[0]


def test_carpeta_suelta_con_estado_de_este_repositorio(mundo):
    worktree = mundo.iniciar_unidad()
    suelta = worktree.parent / "0001-sumar-vieja"
    shutil.copytree(worktree, suelta, symlinks=True)
    ajena = worktree.parent / "0009-otro-repo"
    shutil.copytree(worktree, ajena, symlinks=True)
    estado = json.loads((ajena / ARCHIVO_ESTADO).read_text())
    estado["repositorio"] = "otro-repo"
    (ajena / ARCHIVO_ESTADO).write_text(json.dumps(estado), encoding="utf-8")

    huerfanos = mundo.doctor()["worktrees"]

    assert huerfanos.estado == "aviso"
    assert [i.split(":")[0] for i in huerfanos.items] == [
        "0001-sumar-vieja"
    ]  # la de otro repositorio no cuenta


# --- por la línea de comandos ----------------------------------------------------------------------


@pytest.fixture
def en_cli(mundo, monkeypatch):
    monkeypatch.setattr(cli, "crear_cliente", mundo.cliente)
    for nombre, valor in mundo.env.items():
        monkeypatch.setenv(nombre, valor)
    return mundo


def test_cli_doctor_sale_con_0_si_todo_esta_bien(en_cli, capsys):
    en_cli.instalar(Arnes.claude_code)

    codigo = cli.main(["--repo", str(en_cli.raiz), "doctor"])

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "✓ servidor" in salida and "✗" not in salida
    assert salida.rstrip().endswith("0 fallo(s), 0 aviso(s), 8 bien.")


def test_cli_doctor_sale_con_1_si_algo_falla_y_trae_el_remedio(en_cli, capsys, monkeypatch):
    monkeypatch.delenv(config.ENV_TOKEN)
    en_cli.instalar(Arnes.claude_code)

    codigo = cli.main(["--repo", str(en_cli.raiz), "doctor"])

    salida = capsys.readouterr().out
    assert codigo == 1
    assert "✗ sesion" in salida and "→ `railspec login`" in salida
    assert "1 fallo(s)" in salida


def test_cli_doctor_en_json(en_cli, capsys, monkeypatch):
    monkeypatch.delenv(config.ENV_URL)

    codigo = cli.main(["--repo", str(en_cli.raiz), "doctor", "--json"])

    datos = json.loads(capsys.readouterr().out)
    assert codigo == 1 and datos["ok"] is False
    assert datos["resumen"]["fallo"] == 1 and datos["resumen"]["aviso"] >= 1
    servidor = next(c for c in datos["comprobaciones"] if c["nombre"] == "servidor")
    assert servidor["estado"] == "fallo" and "remedio" in servidor


def test_cli_doctor_fuera_de_un_repositorio(en_cli, capsys, tmp_path_factory):
    nada = tmp_path_factory.mktemp("nada")

    codigo = cli.main(["--repo", str(nada), "doctor"])

    assert codigo == 1 and "✗ repositorio" in capsys.readouterr().out
