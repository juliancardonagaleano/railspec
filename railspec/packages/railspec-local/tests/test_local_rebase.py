"""Rebase de una unidad sobre repositorios git temporales: limpio, sucio, conflicto y repetido."""

from __future__ import annotations

from pathlib import Path

import pytest
from local_fabricas import repo_git, sh
from railspec.local import git
from railspec.local.rebase import RebaseBloqueado, RebaseConflicto, rebasar_unidad

RAMA = "railspec/0001-sumar"
SUMA_MAS = "def suma(a, b):\n    return a + b\n"
SUMA_POR = "def suma(a, b):\n    return a * b\n"


def _commit(repo: Path, archivo: str, contenido: str, mensaje: str) -> str:
    ruta = repo / archivo
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(contenido, encoding="utf-8")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", mensaje)
    return sh(repo, "rev-parse", "HEAD").strip()


def escenario(tmp_path: Path) -> tuple[Path, Path, str]:
    """Clon principal con un commit y el worktree de la unidad parado en él."""

    repo = repo_git(tmp_path / "repo")
    base = sh(repo, "rev-parse", "HEAD").strip()
    worktree = tmp_path / "repo.railspec" / "0001-sumar"
    git.crear_worktree(repo, worktree, RAMA, base)
    return repo, worktree, base


def con_remoto(tmp_path: Path, repo: Path) -> Path:
    remoto = tmp_path / "remoto.git"
    sh(tmp_path, "init", "-q", "--bare", "-b", "main", str(remoto))
    sh(repo, "remote", "add", "origin", str(remoto))
    sh(repo, "push", "-q", "origin", "main")
    return remoto


# --- limpio ---------------------------------------------------------------------------------------


def test_sin_commits_propios_la_rama_se_mueve_a_la_base_nueva(tmp_path):
    repo, worktree, base = escenario(tmp_path)
    nueva = _commit(repo, "docs/notas.md", "nuevo\n", "avanza main")

    resultado = rebasar_unidad(worktree, RAMA, base, nueva)

    assert resultado.movido and resultado.reaplicados == 0 and not resultado.forzar_empuje
    assert (resultado.base_anterior, resultado.base_nueva, resultado.cabeza) == (base, nueva, nueva)
    assert git.head(worktree) == nueva and git.rama_actual(worktree) == RAMA
    assert (worktree / "docs" / "notas.md").read_text(encoding="utf-8") == "nuevo\n"
    assert git.cambios_sin_commit(worktree) == []


def test_los_commits_de_la_unidad_se_reaplican_sobre_la_base_nueva(tmp_path):
    repo, worktree, base = escenario(tmp_path)
    propio = _commit(worktree, "src/calc.py", SUMA_MAS, "corrige la suma")
    nueva = _commit(repo, "docs/notas.md", "nuevo\n", "avanza main")

    resultado = rebasar_unidad(worktree, RAMA, base, nueva)

    assert resultado.movido and resultado.reaplicados == 1
    assert resultado.cabeza != propio  # la historia se reescribió
    assert sh(worktree, "rev-parse", "HEAD~1").strip() == nueva
    assert sh(worktree, "log", "-1", "--format=%s").strip() == "corrige la suma"
    assert (worktree / "src" / "calc.py").read_text(encoding="utf-8") == SUMA_MAS
    assert (worktree / "docs" / "notas.md").is_file()
    assert git.rama_actual(repo) == "main" and git.head(repo) == nueva  # el clon principal ni se entera


def test_los_artefactos_sin_seguimiento_de_la_unidad_no_impiden_el_rebase(tmp_path):
    repo, worktree, base = escenario(tmp_path)
    spec = worktree / ".railspec" / "unidades" / "0001-sumar" / "spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# spec\n", encoding="utf-8")
    nueva = _commit(repo, "docs/notas.md", "nuevo\n", "avanza main")

    resultado = rebasar_unidad(worktree, RAMA, base, nueva)

    assert resultado.movido and git.head(worktree) == nueva
    assert spec.read_text(encoding="utf-8") == "# spec\n"


def test_el_commit_nuevo_que_solo_esta_en_el_remoto_se_trae(tmp_path):
    repo, worktree, base = escenario(tmp_path)
    remoto = con_remoto(tmp_path, repo)
    otro = tmp_path / "otro"
    sh(tmp_path, "clone", "-q", str(remoto), str(otro))
    sh(otro, "config", "user.email", "otro@example.com")
    sh(otro, "config", "user.name", "Otro")
    nueva = _commit(otro, "docs/notas.md", "del otro\n", "avanza en otra máquina")
    sh(otro, "push", "-q", "origin", "main")
    assert not git.existe_commit(repo, nueva)

    resultado = rebasar_unidad(worktree, RAMA, base, nueva)

    assert resultado.movido and git.head(worktree) == nueva


def test_una_rama_ya_publicada_pide_empujar_con_fuerza_tras_el_rebase(tmp_path):
    repo, worktree, base = escenario(tmp_path)
    con_remoto(tmp_path, repo)
    _commit(worktree, "src/calc.py", SUMA_MAS, "corrige la suma")
    sh(worktree, "push", "-q", "origin", f"HEAD:refs/heads/{RAMA}")
    nueva = _commit(repo, "docs/notas.md", "nuevo\n", "avanza main")

    assert rebasar_unidad(worktree, RAMA, base, nueva).forzar_empuje is True


def test_una_rama_sin_publicar_no_pide_fuerza(tmp_path):
    repo, worktree, base = escenario(tmp_path)
    con_remoto(tmp_path, repo)
    _commit(worktree, "src/calc.py", SUMA_MAS, "corrige la suma")
    nueva = _commit(repo, "docs/notas.md", "nuevo\n", "avanza main")

    assert rebasar_unidad(worktree, RAMA, base, nueva).forzar_empuje is False


# --- sucio ------------------------------------------------------------------------------------------


def test_cambios_sin_commit_bloquean_el_rebase_y_no_se_pierden(tmp_path):
    repo, worktree, base = escenario(tmp_path)
    nueva = _commit(repo, "docs/notas.md", "nuevo\n", "avanza main")
    (worktree / "src" / "calc.py").write_text(SUMA_MAS, encoding="utf-8")

    with pytest.raises(RebaseBloqueado, match=r"cambios sin commit en `src/calc.py`"):
        rebasar_unidad(worktree, RAMA, base, nueva)

    assert git.head(worktree) == base
    assert (worktree / "src" / "calc.py").read_text(encoding="utf-8") == SUMA_MAS


@pytest.mark.parametrize("como", ["en el índice", "sin seguimiento fuera de .railspec"])
def test_cambios_en_el_indice_o_archivos_nuevos_tambien_bloquean(tmp_path, como):
    repo, worktree, base = escenario(tmp_path)
    nueva = _commit(repo, "docs/notas.md", "nuevo\n", "avanza main")
    if como == "en el índice":
        (worktree / "src" / "calc.py").write_text(SUMA_MAS, encoding="utf-8")
        sh(worktree, "add", "src/calc.py")
        esperado = "src/calc.py"
    else:
        (worktree / "scratch.txt").write_text("borrador\n", encoding="utf-8")
        esperado = "scratch.txt"

    with pytest.raises(RebaseBloqueado, match=esperado):
        rebasar_unidad(worktree, RAMA, base, nueva)
    assert git.head(worktree) == base


def test_el_mensaje_de_cambios_sucios_resume_las_rutas_de_sobra(tmp_path):
    repo, worktree, base = escenario(tmp_path)
    nueva = _commit(repo, "docs/notas.md", "nuevo\n", "avanza main")
    for n in range(12):
        (worktree / f"borrador-{n:02}.txt").write_text("x\n", encoding="utf-8")

    with pytest.raises(RebaseBloqueado, match=r"y 4 más"):
        rebasar_unidad(worktree, RAMA, base, nueva)


# --- conflicto --------------------------------------------------------------------------------------


def _con_conflicto(tmp_path: Path) -> tuple[Path, Path, str, str, str]:
    repo, worktree, base = escenario(tmp_path)
    propio = _commit(worktree, "src/calc.py", SUMA_MAS, "la unidad suma")
    nueva = _commit(repo, "src/calc.py", SUMA_POR, "main multiplica")
    return repo, worktree, base, nueva, propio


def test_un_conflicto_aborta_el_rebase_y_deja_el_worktree_como_estaba(tmp_path):
    _, worktree, base, nueva, propio = _con_conflicto(tmp_path)

    with pytest.raises(RebaseConflicto) as exc:
        rebasar_unidad(worktree, RAMA, base, nueva)

    assert exc.value.archivos == ["src/calc.py"]
    assert exc.value.comando == f"git -C {worktree} rebase --onto {nueva} {base}"
    assert "`src/calc.py`" in str(exc.value) and exc.value.comando in str(exc.value)
    assert not git.rebase_en_curso(worktree)
    assert git.head(worktree) == propio and git.rama_actual(worktree) == RAMA
    assert git.cambios_sin_commit(worktree) == []
    assert (worktree / "src" / "calc.py").read_text(encoding="utf-8") == SUMA_MAS


def test_tras_resolver_el_conflicto_a_mano_la_rama_se_da_por_alineada(tmp_path):
    _, worktree, base, nueva, _ = _con_conflicto(tmp_path)
    with pytest.raises(RebaseConflicto) as exc:
        rebasar_unidad(worktree, RAMA, base, nueva)

    # Lo que el mensaje pide al desarrollador: rebasar a mano, resolver y continuar.
    with pytest.raises(git.ErrorGit):
        git.rebasar(worktree, nueva, base)
    (worktree / "src" / "calc.py").write_text("def suma(a, b):\n    return a + b + 0\n", encoding="utf-8")
    sh(worktree, "add", "src/calc.py")
    sh(worktree, "-c", "core.editor=true", "rebase", "--continue")
    assert exc.value.comando.endswith(f"--onto {nueva} {base}")

    resultado = rebasar_unidad(worktree, RAMA, base, nueva)

    assert not resultado.movido and resultado.reaplicados == 1 and resultado.cabeza == git.head(worktree)
    assert (worktree / "src" / "calc.py").read_text(encoding="utf-8").endswith("a + b + 0\n")


def test_un_rebase_a_medias_bloquea_y_se_respeta(tmp_path):
    _, worktree, base, nueva, _ = _con_conflicto(tmp_path)
    with pytest.raises(git.ErrorGit):
        git.rebasar(worktree, nueva, base)
    assert git.rebase_en_curso(worktree)

    with pytest.raises(RebaseBloqueado, match="rebase a medias"):
        rebasar_unidad(worktree, RAMA, base, nueva)
    assert git.rebase_en_curso(worktree)  # no lo cancela por su cuenta


# --- otras condiciones ------------------------------------------------------------------------------


def test_otra_rama_en_el_worktree_bloquea(tmp_path):
    repo, worktree, base = escenario(tmp_path)
    nueva = _commit(repo, "docs/notas.md", "nuevo\n", "avanza main")
    sh(worktree, "switch", "-q", "-c", "otra")

    with pytest.raises(RebaseBloqueado, match=rf"está en otra .* {RAMA}"):
        rebasar_unidad(worktree, RAMA, base, nueva)


def test_un_commit_que_no_se_consigue_bloquea_sin_tocar_nada(tmp_path):
    _, worktree, base = escenario(tmp_path)

    with pytest.raises(RebaseBloqueado, match="no está en el repositorio ni en el remoto"):
        rebasar_unidad(worktree, RAMA, base, "f" * 40)
    assert git.head(worktree) == base


def test_una_rama_que_no_parte_de_la_base_anterior_no_se_adivina(tmp_path):
    repo, worktree, base = escenario(tmp_path)
    ajeno = _commit(repo, "docs/otro.md", "otro\n", "otra historia")
    nueva = _commit(repo, "docs/notas.md", "nuevo\n", "avanza main")

    with pytest.raises(RebaseBloqueado, match="ya no parte de"):
        rebasar_unidad(worktree, RAMA, ajeno, nueva)
    assert git.head(worktree) == base


# --- funciones de git.py ----------------------------------------------------------------------------


def test_cambios_sin_commit_ignora_solo_lo_pedido(tmp_path):
    _, worktree, _ = escenario(tmp_path)
    (worktree / ".railspec" / "unidades" / "u").mkdir(parents=True)
    (worktree / ".railspec" / "unidades" / "u" / "spec.md").write_text("x\n", encoding="utf-8")
    sh(worktree, "mv", "README.md", "LEEME.md")
    (worktree / "con espacios.txt").write_text("x\n", encoding="utf-8")

    # Un renombre cuenta una vez: su ruta de origen no se lee como entrada aparte.
    assert sorted(git.cambios_sin_commit(worktree)) == [
        ".railspec/unidades/u/spec.md",
        "LEEME.md",
        "con espacios.txt",
    ]
    assert sorted(git.cambios_sin_commit(worktree, (".railspec/",))) == ["LEEME.md", "con espacios.txt"]


def test_es_ancestro_distingue_no_ancestro_de_commit_inexistente(tmp_path):
    repo, worktree, base = escenario(tmp_path)
    nueva = _commit(repo, "docs/notas.md", "nuevo\n", "avanza main")

    assert git.es_ancestro(repo, base, nueva) and git.es_ancestro(repo, nueva, nueva)
    assert not git.es_ancestro(repo, nueva, base)
    with pytest.raises(git.ErrorGit):
        git.es_ancestro(repo, "f" * 40, base)
