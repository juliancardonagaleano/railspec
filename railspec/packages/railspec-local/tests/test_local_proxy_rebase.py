"""``unit_advance`` rebasa la unidad cuando la orden parte de otro commit base (fase B del rebase).

Contra repositorios git temporales (limpio, sucio, conflicto, con remoto) y el servidor doble: el doble
rechaza un reporte cuyo ``base_commit`` no es el de la orden, así que un reporte aceptado prueba que el
proxy guardó la base nueva.
"""

from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path

import pytest
from local_fabricas import ServidorDoble, crear_proxy, orden_implementar, sh
from railspec.local import git
from railspec.local.almacen import Almacen
from railspec.local.rebase import RebaseBloqueado, RebaseConflicto

RAMA = "railspec/0001-sumar"
SUMA_MAS = "def suma(a, b):\n    return a + b\n"
SUMA_POR = "def suma(a, b):\n    return a * b\n"


def correr(coro):
    return asyncio.run(coro)


def _commit(repo: Path, archivo: str, contenido: str, mensaje: str) -> str:
    ruta = repo / archivo
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(contenido, encoding="utf-8")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", mensaje)
    return sh(repo, "rev-parse", "HEAD").strip()


def escenario(tmp_path: Path, avanzar: bool = True, base_orden: str | None = None):
    """Unidad en marcha; con ``avanzar``, un commit nuevo en el clon principal del que parte la orden.

    Devuelve (proxy, servidor, worktree, base original, base nueva o ``None``)."""

    servidor = ServidorDoble()
    proxy = crear_proxy(tmp_path, servidor)
    worktree = Path(correr(proxy.iniciar("Corregir suma", "La suma resta."))["worktree"])
    base = git.head(worktree)
    nueva = base_orden or (
        _commit(proxy.raiz, "docs/notas.md", "nuevo\n", "avanza main") if avanzar else None
    )
    if nueva:
        servidor.ordenes.append(partial(_orden_en, nueva))
    return proxy, servidor, worktree, base, nueva


def _orden_en(base_commit: str, estado, secuencia, orden_id):
    return orden_implementar(estado, secuencia, orden_id, base_commit=base_commit)


# --- orden con otra base ------------------------------------------------------------


def test_orden_con_otra_base_rebasa_y_guarda_la_base_nueva(tmp_path):
    proxy, servidor, worktree, base, nueva = escenario(tmp_path)
    propio = _commit(worktree, "src/calc.py", SUMA_MAS, "corrige la suma")

    respuesta = correr(proxy.avanzar())

    assert respuesta["tipo"] == "orden" and respuesta["orden"]["base_commit"] == nueva
    assert respuesta["rebase"] == {
        "base_anterior": base,
        "base_nueva": nueva,
        "reaplicados": 1,
        "movido": True,
    }
    assert "avisos" not in respuesta  # la rama nunca se empujó: no hay nada que forzar
    # El worktree partió de la base nueva con los commits de la unidad encima.
    assert sh(worktree, "rev-parse", "HEAD~1").strip() == nueva
    assert git.head(worktree) != propio and git.rama_actual(worktree) == RAMA
    assert (worktree / "docs" / "notas.md").is_file()
    # La base nueva está en disco junto con la orden (EstadoLocal exige que coincidan).
    estado = Almacen(worktree).leer()
    assert estado.base_commit == nueva
    assert estado.orden_en_curso is not None and estado.orden_en_curso.base_commit == nueva


def test_tras_rebasar_el_reporte_lleva_la_base_nueva_y_el_servidor_lo_acepta(tmp_path):
    proxy, servidor, worktree, _, nueva = escenario(tmp_path)
    correr(proxy.avanzar())
    (worktree / "src" / "calc.py").write_text(SUMA_MAS, encoding="utf-8")

    resultado = correr(proxy.reportar())

    assert resultado["aceptado"] is True
    assert servidor.reportes[0].base_commit == nueva


def test_sin_commits_propios_la_rama_solo_se_mueve(tmp_path):
    proxy, _, worktree, _, nueva = escenario(tmp_path)

    respuesta = correr(proxy.avanzar())

    assert respuesta["rebase"]["reaplicados"] == 0 and respuesta["rebase"]["movido"] is True
    assert git.head(worktree) == nueva


def test_rama_ya_empujada_avisa_de_force_with_lease(tmp_path):
    proxy, _, worktree, base, nueva = escenario(tmp_path)
    _commit(worktree, "src/calc.py", SUMA_MAS, "corrige la suma")
    remoto = tmp_path / "remoto.git"
    sh(tmp_path, "init", "-q", "--bare", "-b", "main", str(remoto))
    sh(proxy.raiz, "remote", "add", "origin", str(remoto))
    sh(worktree, "push", "-q", "origin", RAMA)

    respuesta = correr(proxy.avanzar())

    assert respuesta["rebase"]["movido"] is True
    (aviso,) = respuesta["avisos"]
    assert "--force-with-lease" in aviso and RAMA in aviso and str(worktree) in aviso
    assert "nunca `--force`" in aviso
    # La referencia remota quedó atrás: es lo que hace que el push normal falle.
    assert not git.es_ancestro(worktree, sh(worktree, "rev-parse", f"origin/{RAMA}").strip(), "HEAD")


def test_con_la_misma_base_no_se_toca_git(tmp_path):
    proxy, servidor, worktree, base, _ = escenario(tmp_path, avanzar=False)
    servidor.ordenes.append(orden_implementar)

    respuesta = correr(proxy.avanzar())

    assert respuesta["tipo"] == "orden" and "rebase" not in respuesta
    assert git.head(worktree) == base


def test_los_artefactos_de_la_unidad_sin_seguimiento_no_impiden_el_rebase(tmp_path):
    proxy, _, worktree, _, nueva = escenario(tmp_path)
    spec = worktree / ".railspec" / "unidades" / "0001-sumar" / "spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("# spec\n", encoding="utf-8")

    correr(proxy.avanzar())

    assert git.head(worktree) == nueva and spec.is_file()


# --- lo que lo impide: no se toca nada --------------------------------------------------


def test_worktree_sucio_no_se_rebasa_y_el_estado_queda_como_estaba(tmp_path):
    proxy, _, worktree, base, nueva = escenario(tmp_path)
    propio = _commit(worktree, "src/calc.py", SUMA_MAS, "corrige la suma")
    (worktree / "README.md").write_text("editado sin commit\n", encoding="utf-8")
    antes = Almacen(worktree).leer()

    with pytest.raises(RebaseBloqueado, match="cambios sin commit en `README.md`"):
        correr(proxy.avanzar())

    assert git.head(worktree) == propio  # ni un paso de rebase
    assert (worktree / "README.md").read_text(encoding="utf-8") == "editado sin commit\n"
    estado = Almacen(worktree).leer()
    assert estado.base_commit == base == antes.base_commit and estado.orden_en_curso is None


def test_se_puede_reintentar_tras_limpiar_el_worktree(tmp_path):
    proxy, _, worktree, _, nueva = escenario(tmp_path)
    (worktree / "README.md").write_text("editado sin commit\n", encoding="utf-8")
    with pytest.raises(RebaseBloqueado):
        correr(proxy.avanzar())
    sh(worktree, "checkout", "--", "README.md")

    respuesta = correr(proxy.avanzar())

    assert respuesta["rebase"]["base_nueva"] == nueva
    assert Almacen(worktree).leer().base_commit == nueva


def test_conflicto_aborta_deja_el_worktree_y_dice_como_rebasar_a_mano(tmp_path):
    proxy, servidor, worktree, base, nueva = escenario(tmp_path, avanzar=False)
    # Dos cambios incompatibles en la misma línea: uno en la unidad, otro en la rama principal.
    propio = _commit(worktree, "src/calc.py", SUMA_MAS, "suma con +")
    nueva = _commit(proxy.raiz, "src/calc.py", SUMA_POR, "suma con *")
    servidor.ordenes = [partial(_orden_en, nueva)]

    with pytest.raises(RebaseConflicto) as exc:
        correr(proxy.avanzar())

    assert exc.value.archivos == ["src/calc.py"]
    assert f"rebase --onto {nueva} {base}" in str(exc.value)
    assert git.head(worktree) == propio and not git.rebase_en_curso(worktree)
    assert git.cambios_sin_commit(worktree) == []
    estado = Almacen(worktree).leer()
    assert estado.base_commit == base and estado.orden_en_curso is None


def test_resuelto_a_mano_el_siguiente_unit_advance_sigue(tmp_path):
    proxy, servidor, worktree, base, _ = escenario(tmp_path, avanzar=False)
    _commit(worktree, "src/calc.py", SUMA_MAS, "suma con +")
    nueva = _commit(proxy.raiz, "src/calc.py", SUMA_POR, "suma con *")
    servidor.ordenes = [partial(_orden_en, nueva)]
    with pytest.raises(RebaseConflicto):
        correr(proxy.avanzar())

    # El desarrollador rebasa a mano, resuelve el choque y continúa.
    sh(worktree, "reset", "-q", "--hard", nueva)
    _commit(worktree, "src/calc.py", SUMA_MAS, "suma con + (resuelto)")

    respuesta = correr(proxy.avanzar())

    assert respuesta["tipo"] == "orden"
    assert respuesta["rebase"]["movido"] is False  # la rama ya parte de la base nueva
    assert Almacen(worktree).leer().base_commit == nueva


def test_commit_que_no_existe_no_se_inventa(tmp_path):
    proxy, _, worktree, base, _ = escenario(tmp_path, base_orden="a" * 40)

    with pytest.raises(RebaseBloqueado, match="no está en el repositorio ni en el remoto"):
        correr(proxy.avanzar())

    assert git.head(worktree) == base
    assert Almacen(worktree).leer().base_commit == base
