"""Rebase de una unidad: lleva su rama y su worktree a otro ``base_commit``.

El servidor puede emitir una orden cuyo ``base_commit`` no es el del worktree (la
base de la unidad avanzó). El worktree tiene que partir del commit de la orden: el
reporte lleva ese commit y el servidor rechaza uno distinto. Aquí vive la
operación en git; escribir el ``base_commit`` nuevo en el estado local es del
proxy, que lo hace solo tras un ``rebasar_unidad`` que no lanzó.

Reglas:

- Solo con el worktree limpio: un rebase reescribe la rama y no debe llevarse por
  delante trabajo sin commit. Los artefactos sin seguimiento de ``.railspec/`` no
  cuentan; el rebase no los toca.
- Ante un conflicto no se deja nada a medias: el rebase se aborta, el worktree
  queda como estaba y el error dice qué archivos chocan y cómo rebasar a mano.
- Es repetible: si la rama ya contiene la base nueva (porque el desarrollador la
  rebasó a mano tras un conflicto), no se toca nada y se da por alineada.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import git
from .errores import ErrorRailspec

#: Sin seguimiento y bajo ``.railspec/`` (artefactos de la unidad): no impiden el rebase.
SIN_SEGUIMIENTO_PERMITIDO = (".railspec/",)
MAX_RUTAS_EN_MENSAJE = 8


class RebaseBloqueado(ErrorRailspec):
    """No se cumple una condición del rebase; no se tocó nada."""


class RebaseConflicto(ErrorRailspec):
    """Reaplicar los commits de la unidad choca con la base nueva; el worktree quedó como estaba."""

    def __init__(self, archivos: list[str], comando: str) -> None:
        super().__init__(
            "No se pudo rebasar la unidad: sus commits chocan con la base nueva en "
            f"{_lista(archivos)}. El proxy abortó el rebase y el worktree quedó como estaba. "
            f"Rebasa a mano con `{comando}`, resuelve los conflictos, `git add` y `git rebase --continue`, "
            "y vuelve a llamar unit_advance: detecta que la rama ya parte de la base nueva y sigue."
        )
        self.archivos = archivos
        self.comando = comando


@dataclass(frozen=True)
class ResultadoRebase:
    base_anterior: str
    base_nueva: str
    cabeza: str
    #: Commits de la unidad que quedaron sobre la base nueva.
    reaplicados: int
    #: ``False`` si la rama ya partía de la base nueva y no hubo que tocar git.
    movido: bool
    #: La rama ya estaba en el remoto y el rebase la reescribió: el próximo push exige ``--force-with-lease``.
    forzar_empuje: bool


def rebasar_unidad(
    worktree: Path, rama: str, base_anterior: str, base_nueva: str, remoto: str = "origin"
) -> ResultadoRebase:
    """Reaplica los commits de ``rama`` sobre ``base_nueva``; ``base_anterior`` es de donde partía.

    ``RebaseBloqueado`` si no se puede intentar (rebase a medias, otra rama, commit que no se
    consigue, cambios sin commit, historia ajena); ``RebaseConflicto`` si git choca."""

    if git.rebase_en_curso(worktree):
        raise RebaseBloqueado(
            f"Hay un rebase a medias en {worktree}: termínalo (`git rebase --continue`) o cancélalo "
            "(`git rebase --abort`) y vuelve a llamar unit_advance."
        )
    actual = git.rama_actual(worktree)
    if actual != rama:
        raise RebaseBloqueado(
            f"El worktree está en {actual or 'HEAD suelto'} y la unidad vive en la rama {rama}: "
            f"vuelve a ella (`git -C {worktree} switch {rama}`) y llama unit_advance de nuevo."
        )
    if not git.traer_commit(worktree, base_nueva, remoto):
        raise RebaseBloqueado(
            f"La orden parte del commit {base_nueva[:12]} y no está en el repositorio ni en el remoto "
            f"{remoto}. Trae sus ramas (`git -C {worktree} fetch {remoto}`) y vuelve a llamar unit_advance; "
            "si el commit nunca se publicó, la orden es la equivocada."
        )

    if git.es_ancestro(worktree, base_nueva, "HEAD"):
        return _resultado(worktree, rama, base_anterior, base_nueva, movido=False)
    if not git.es_ancestro(worktree, base_anterior, "HEAD"):
        raise RebaseBloqueado(
            f"La rama {rama} ya no parte de {base_anterior[:12]} ni de {base_nueva[:12]}: el proxy no "
            "adivina qué commits reaplicar. Llévala a una de las dos bases (`git reset`, `git rebase`) y "
            "vuelve a llamar unit_advance."
        )
    sucios = git.cambios_sin_commit(worktree, SIN_SEGUIMIENTO_PERMITIDO)
    if sucios:
        raise RebaseBloqueado(
            f"El worktree tiene cambios sin commit en {_lista(sucios)} y un rebase podría perderlos. "
            f"Haz commit de ellos, o guárdalos con `git -C {worktree} stash` y recupéralos con "
            "`git stash pop` después, y vuelve a llamar unit_advance."
        )

    try:
        git.rebasar(worktree, base_nueva, base_anterior)
    except git.ErrorGit:
        if not git.rebase_en_curso(worktree):
            raise  # git no llegó a empezar (p. ej. un archivo sin seguimiento estorba): su mensaje basta
        archivos = git.archivos_en_conflicto(worktree)
        git.abortar_rebase(worktree)
        raise RebaseConflicto(
            archivos, f"git -C {worktree} rebase --onto {base_nueva} {base_anterior}"
        ) from None
    return _resultado(worktree, rama, base_anterior, base_nueva, movido=True)


def _resultado(
    worktree: Path, rama: str, base_anterior: str, base_nueva: str, *, movido: bool
) -> ResultadoRebase:
    cabeza = git.head(worktree)
    publicado = git.commit_empujado(worktree, rama) if movido else None
    return ResultadoRebase(
        base_anterior=base_anterior,
        base_nueva=base_nueva,
        cabeza=cabeza,
        reaplicados=git.contar_commits(worktree, base_nueva, cabeza),
        movido=movido,
        forzar_empuje=publicado is not None and not git.es_ancestro(worktree, publicado, cabeza),
    )


def _lista(rutas: list[str]) -> str:
    visibles = ", ".join(f"`{r}`" for r in rutas[:MAX_RUTAS_EN_MENSAJE])
    resto = len(rutas) - MAX_RUTAS_EN_MENSAJE
    return f"{visibles} y {resto} más" if resto > 0 else visibles
