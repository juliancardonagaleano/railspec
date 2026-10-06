"""Tipos y vocabulario compartidos por todos los contratos.

El vocabulario (fases, modos, veredictos, severidades) viene del protocolo
SDD del kit (``.spec/README.md``) para que el motor hable el mismo idioma que
las unidades que ya existen, aunque no haya retrocompatibilidad de formato.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from ._base import Contrato

# --- Identificadores -------------------------------------------------------

Slug = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$"),
    Field(description="Identificador en minúsculas, dígitos y guiones (máx. 63)."),
]

UnidadId = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9]{4}-[a-z0-9][a-z0-9-]{0,62}$"),
    Field(description="Identificador de unidad SDD con forma NNNN-slug."),
]

CriterioId = Annotated[
    str,
    StringConstraints(pattern=r"^CA-[0-9]{2,3}$"),
    Field(description="Criterio de aceptación del spec, p. ej. CA-07."),
]

TareaId = Annotated[
    str,
    StringConstraints(pattern=r"^T-[0-9]{2,3}$"),
    Field(description="Tarea de tasks.md, p. ej. T-03."),
]

GrupoId = Annotated[
    str,
    StringConstraints(pattern=r"^G[0-9]{1,2}$"),
    Field(description="Grupo de tareas del plan, p. ej. G1."),
]

Commit = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{40}$"),
    Field(description="SHA-1 completo de un commit git."),
]

HashArbol = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{40}$"),
    Field(
        description=(
            "SHA-1 del árbol git del worktree incluyendo cambios sin commit "
            "(equivalente a `git add -A && git write-tree` en un índice temporal)."
        )
    ),
]

Sha256 = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
    Field(description="SHA-256 en hexadecimal."),
]

RutaRelativa = Annotated[
    str,
    StringConstraints(
        pattern=r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[^\x00]+$",
        max_length=1024,
    ),
    Field(description="Ruta relativa a la raíz del repositorio, sin '..' ni '/' inicial."),
]

Glob = Annotated[
    str,
    StringConstraints(pattern=r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[^\x00]+$", max_length=512),
    Field(description="Patrón glob relativo a la raíz del repositorio (sintaxis gitignore)."),
]

# --- Vocabulario del protocolo ----------------------------------------------


class Fase(StrEnum):
    research = "research"
    spec = "spec"
    plan = "plan"
    tasks = "tasks"
    aprobacion = "aprobacion"
    implement = "implement"
    done = "done"


class EstadoFase(StrEnum):
    en_progreso = "en-progreso"
    bloqueado = "bloqueado"
    completado = "completado"


class Modo(StrEnum):
    interactivo = "interactivo"
    semi_autonomo = "semi-autonomo"
    supervisado = "supervisado"
    desatendido = "desatendido"


class Riesgo(StrEnum):
    bajo = "bajo"
    medio = "medio"
    alto = "alto"


class Perfil(StrEnum):
    ligero = "ligero"
    estandar = "estandar"
    profundo = "profundo"


class GateFase(StrEnum):
    spec = "spec"
    plan = "plan"
    tasks = "tasks"
    codigo = "codigo"


class Veredicto(StrEnum):
    aprobado = "aprobado"
    refinado = "refinado"
    escalado = "escalado"


class CausaEscalado(StrEnum):
    hallazgos_sin_resolver = "hallazgos-sin-resolver"
    sin_gobernanza = "sin-gobernanza"
    presupuesto_agotado = "presupuesto-agotado"
    error_proveedor = "error-proveedor"
    sin_convergencia = "sin-convergencia"  # 1.2: el refinamiento oscila sin reducir hallazgos
    importado = "importado"  # desde 1.4: unidad cerrada importada; la rehabilita un humano


#: Modos que solo se admiten bajo un mandato (plan de trabajo).
MODOS_CON_MANDATO = frozenset({Modo.supervisado, Modo.desatendido})


class GobernanzaConsultada(StrEnum):
    si = "si"
    parcial = "parcial"
    no = "no"


class Severidad(StrEnum):
    alta = "alta"
    media = "media"
    baja = "baja"


class NivelCodigo(StrEnum):
    """Política de código propietario aprobada el 2026-09-30."""

    restringido = "restringido"
    interno = "interno"
    abierto = "abierto"


#: Cuanto menor, más restrictivo. Los niveles se combinan por el más restrictivo (``mas_restrictivo``).
ORDEN_RESTRICCION = {NivelCodigo.restringido: 0, NivelCodigo.interno: 1, NivelCodigo.abierto: 2}


def mas_restrictivo(niveles: Iterable[NivelCodigo]) -> NivelCodigo:
    """El nivel que rige cuando se combinan varios repositorios; ``ValueError`` si no hay ninguno."""

    return min(niveles, key=ORDEN_RESTRICCION.__getitem__)


class RolRepositorio(StrEnum):
    primario = "primario"
    transversal = "transversal"


class Proveedor(StrEnum):
    foundry = "foundry"
    anthropic = "anthropic"
    #: Desde 1.8: endpoint compatible con la API de Anthropic o de OpenAI (OpenCode Zen y Go, MiniMax...).
    #: Solo sirve a repositorios ``abierto``: su hosting no es Azure.
    compatible = "compatible"


class Arnes(StrEnum):
    claude_code = "claude-code"
    opencode = "opencode"
    codex = "codex"
    copilot = "copilot"


class TipoActor(StrEnum):
    humano = "humano"
    servicio = "servicio"
    agente = "agente"


class Canal(StrEnum):
    arnes = "arnes"
    consola = "consola"
    ci = "ci"
    servidor = "servidor"


class Effort(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    xhigh = "xhigh"
    max = "max"


# --- Espacios de nombres ----------------------------------------------------


class AlcanceWorkspace(Contrato):
    """Espacio de nombres mínimo: todo dato persistido lo lleva."""

    org: Slug
    workspace: Slug


class AlcanceRepositorio(AlcanceWorkspace):
    """Espacio de un repositorio vinculado a un workspace (grafo, vectores)."""

    repositorio: Slug


class AlcanceUnidad(AlcanceWorkspace):
    """Espacio de nombres de una unidad.

    La unidad cuelga del workspace, no de un repositorio, porque puede
    vincular N repositorios; cada snapshot dice a cuál pertenece. ``plan``
    agrupa unidades de un mismo mandato cuando lo hay.
    """

    unidad: UnidadId
    plan: Slug | None = None


class Presupuesto(Contrato):
    """Tope aplicado por el servidor; al agotarse escala al humano."""

    tokens_max: int | None = Field(default=None, ge=1)
    segundos_max: int | None = Field(default=None, ge=1)
    costo_usd_max: float | None = Field(default=None, gt=0)
    llamadas_max: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Desde 1.7: tope de llamadas al modelo. Cuenta las que salen hacia el proveedor (un "
            "acierto de caché no cuenta) y, como los demás topes, escala al humano al alcanzarse."
        ),
    )


class Criterio(Contrato):
    id: CriterioId
    texto: str = Field(min_length=1, max_length=2000)


def ids_unicos(valores: list[str], campo: str) -> None:
    repetidos = sorted({v for v in valores if valores.count(v) > 1})
    if repetidos:
        raise ValueError(f"{campo} repetidos: {', '.join(repetidos)}")


# --- Actor (R2) ----------------------------------------------------------------

LoginGithub = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$"),
    Field(description="Login de GitHub; informativo, la identidad es github_id."),
]


class OidcGithubActions(Contrato):
    """Identidad de servicio emitida por el token OIDC de GitHub Actions."""

    repositorio: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    workflow: str = Field(min_length=1, max_length=512, description="Claim workflow_ref.")
    run_id: int | None = Field(default=None, ge=1)


class Actor(Contrato):
    """Quién hizo algo, por qué canal. Va en todo lo que se escribe.

    La identidad estable es el id numérico de GitHub (el login puede cambiar).
    El proveedor de identidad queda detrás de ``proveedor_identidad`` para que
    otro (Azure DevOps, Entra ID) entre como valor nuevo sin romper el esquema.
    """

    tipo: TipoActor
    canal: Canal
    proveedor_identidad: str = Field(default="github", pattern=r"^[a-z][a-z0-9-]{1,31}$")
    github_id: int | None = Field(default=None, ge=1)
    login: LoginGithub | None = None
    oidc: OidcGithubActions | None = None
    agente: str | None = Field(
        default=None, max_length=120, description="Nodo o rol del agente, si tipo=agente."
    )
    en_nombre_de: int | None = Field(
        default=None, ge=1, description="github_id del humano por quien actúa un agente."
    )

    @model_validator(mode="after")
    def _coherente(self) -> Actor:
        if self.tipo == TipoActor.humano:
            if self.github_id is None or self.login is None:
                raise ValueError("un actor humano necesita github_id y login")
            if self.oidc is not None or self.agente is not None:
                raise ValueError("un actor humano no lleva oidc ni agente")
        elif self.tipo == TipoActor.servicio:
            if self.oidc is None:
                raise ValueError("un actor de servicio necesita su identidad OIDC")
        elif self.agente is None:
            raise ValueError("un actor agente necesita el campo agente")
        return self


# --- Texto que sale hacia personas (R6, R10) -------------------------------------

_BLOQUE_CODIGO = re.compile(r"```|~~~|^(?: {4}|\t)\S", re.MULTILINE)
_HTML = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_URL = re.compile(r"(?i)\b(?:https?|ftp|data|javascript):|\bwww\.")


def verificar_texto_plano(texto: str, campo: str) -> str:
    """Primera barrera del gate de salida, expresada en el contrato.

    No sustituye al gate (huellas, forma de código, secretos, presupuesto de
    fuga), pero hace que un mensaje con bloques de código, HTML o URL externas
    sea inválido por esquema y nunca llegue a renderizarse.
    """

    if _BLOQUE_CODIGO.search(texto):
        raise ValueError(f"{campo}: no admite bloques de código")
    if _HTML.search(texto):
        raise ValueError(f"{campo}: no admite HTML")
    if _URL.search(texto):
        raise ValueError(f"{campo}: no admite URL")
    return texto


#: Marca de esquema para campos que llevan texto de código (R1). El gate de
#: salida del chat calcula huellas de todo campo con esta clase.
CLASE_CODIGO_INTERNO = {"x-railspec-clase": "codigo_interno"}
