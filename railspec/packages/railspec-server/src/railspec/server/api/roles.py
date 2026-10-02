"""Rol efectivo (R3): una sola definición para MCP, ``/v1``, el chat y la consola.

El rol de una persona en un workspace es el mayor entre sus asignaciones y las de sus equipos de GitHub, a
nivel organización (``workspace`` nulo) o de ese workspace. Quien llama desde el arnés (MCP) y quien lo hace
desde la consola pasan por aquí, así que no pueden discrepar en qué asignaciones cuentan ni en cuál gana.

Lo único que la consola añade es administrar la plataforma (``RAILSPEC_CONSOLA_ADMINS``): quien lo hace actúa
como ``org-admin`` en toda organización, pero solo en la consola. Por el arnés no gana nada: para trabajar ahí
necesita un rol asignado como cualquiera (ver ``railspec/docs/consola.md``, «Autorización»).
"""

from __future__ import annotations

from collections.abc import Iterable

from railspec.contracts.repositorio import AsignacionRol, Rol

JERARQUIA = [Rol.lector, Rol.desarrollador, Rol.workspace_admin, Rol.org_admin]


def mayor(roles: Iterable[Rol]) -> Rol | None:
    return max(roles, key=JERARQUIA.index, default=None)


def alcanza(rol: Rol | None, minimo: Rol) -> bool:
    return rol is not None and JERARQUIA.index(rol) >= JERARQUIA.index(minimo)


def rol_efectivo(
    asignaciones: Iterable[AsignacionRol], workspace: str | None, *, abierto: bool = False
) -> Rol | None:
    """El mayor rol de ``asignaciones`` que vale en ``workspace``: las de la organización y las suyas.

    ``abierto`` (solo desarrollo, sin Mongo): quien no tiene ninguna es ``desarrollador``.
    """

    rol = mayor(a.rol for a in asignaciones if a.workspace is None or a.workspace == workspace)
    if rol is None and abierto:
        return Rol.desarrollador
    return rol
