"""Gate del protocolo: capa determinista, panel de críticos, refutador y convergencia.

El gate decide en código, no en prosa: los críticos devuelven hallazgos con
esquema, el refutador puede descartar los de severidad alta y la regla de
convergencia (``decidir``) es una función pura. Reglas del contrato que aquí
se cumplen por construcción:

- nunca aprueba por agotamiento: con hallazgos alta/media sin refutar, escala;
- nunca critica de memoria: sin gobernanza completa escala ``sin-gobernanza``
  antes de llamar a un modelo;
- un escalado siempre lleva causa.

Lo que no cabe en el enum ``CausaEscalado`` v1 (``sin-convergencia`` del kit)
se reporta como ``hallazgos-sin-resolver``.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, Field, ValidationError
from railspec.contracts.comun import CausaEscalado, Criterio, GateFase, NivelCodigo, Severidad
from railspec.contracts.hallazgos import Cita, Hallazgo, bloqueantes
from railspec.contracts.orden import ItemGobernanza
from railspec.contracts.repositorio import PerfilConfig, TopeGate

from ..proveedores.base import ErrorProveedor, PeticionModelo, RespuestaModelo
from ..proveedores.seleccion import PerfilInsatisfacible, Proveedores
from .perfiles import requisito

# --- Lentes ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Lente:
    id: str
    rol: str
    rubrica: str


LENTES: dict[GateFase, tuple[Lente, ...]] = {
    GateFase.spec: (
        Lente(
            "testeabilidad",
            "critico-profundo",
            "Cada CA-NN describe un comportamiento observable con resultado verificable; sin "
            "adjetivos vagos ni criterios que solo se comprueban leyendo código.",
        ),
        Lente(
            "ambiguedad",
            "critico-profundo",
            "Omisiones y ambigüedades: casos límite, errores, actores y datos que el spec da por "
            "supuestos; términos con dos lecturas razonables.",
        ),
        Lente(
            "alcance",
            "critico-profundo",
            "El alcance es coherente con el problema, el 'fuera de alcance' es explícito y el spec "
            "no prescribe solución técnica.",
        ),
        Lente(
            "gobernanza",
            "critico-profundo",
            "El spec respeta los ADR, policies y principios aplicables listados en el contexto; "
            "señala cualquiera que contradiga u omita.",
        ),
    ),
    GateFase.plan: (
        Lente(
            "reutilizacion",
            "critico-profundo",
            "El plan reutiliza lo que el repositorio ya tiene en vez de reinventarlo.",
        ),
        Lente(
            "simplicidad",
            "critico-profundo",
            "Sin abstracciones ni generalidad que ningún CA-NN pide (YAGNI).",
        ),
        Lente(
            "riesgos",
            "critico-profundo",
            "Riesgos reales identificados con mitigación; grupos ejecutables y con archivos acotados; "
            "comando de validación que prueba los CA-NN.",
        ),
        Lente(
            "gobernanza-coherencia",
            "critico-profundo",
            "Coherencia con el spec (todo CA-NN tiene camino en el plan) y con la gobernanza aplicable.",
        ),
    ),
    GateFase.tasks: (
        Lente(
            "atomicidad",
            "critico-estructural",
            "Cada tarea es atómica, verificable y del tamaño de un cambio revisable.",
        ),
        Lente(
            "orden",
            "critico-estructural",
            "El orden y las dependencias entre tareas y grupos son ejecutables tal como están.",
        ),
        Lente(
            "fidelidad",
            "critico-estructural",
            "Las tareas implementan exactamente el plan: nada que el plan no diga, nada del plan sin tarea.",
        ),
    ),
    GateFase.codigo: (
        Lente(
            "cumplimiento",
            "critico-cumplimiento",
            "Cada CA-NN queda satisfecho por el cambio y por la validación reportada; señala el CA que no.",
        ),
        Lente(
            "correctitud",
            "critico-profundo",
            "Errores lógicos, casos límite y regresiones visibles en el cambio.",
        ),
        Lente(
            "encaje",
            "critico-cumplimiento",
            "El cambio encaja con las convenciones del repositorio y con la gobernanza aplicable, "
            "y no toca archivos fuera del alcance de la orden.",
        ),
    ),
}

_SEVERIDADES = (
    "alta = el artefacto es inservible o incumple gobernanza; media = degrada calidad o deja "
    "ambigüedad real; baja = mejora opcional. No inventes hallazgos para justificar la corrida: "
    "si no encuentras nada, devuelve la lista vacía."
)


def repartir(lentes: tuple[Lente, ...], criticos: int) -> list[list[Lente]]:
    """Cada lente lo cubre exactamente un crítico; nunca se omite un lente."""

    n = max(1, min(criticos, len(lentes)))
    grupos: list[list[Lente]] = [[] for _ in range(n)]
    for i, lente in enumerate(lentes):
        grupos[i % n].append(lente)
    return grupos


# --- Esquemas de salida de los modelos ------------------------------------------------


class HallazgoPropuesto(BaseModel):
    lente: str = Field(description="Id del lente que lo produjo.")
    severidad: Severidad
    criterio: str | None = Field(default=None, description="CA-NN afectado, si aplica.")
    titulo: str
    seccion: str | None = Field(default=None, description="Sección del artefacto.")
    ruta: str | None = Field(default=None, description="Ruta del archivo, en el gate de código.")
    linea_inicio: int | None = None
    linea_fin: int | None = None
    evidencia: str
    propuesta: str | None = None


class SalidaCritico(BaseModel):
    hallazgos: list[HallazgoPropuesto]


class SalidaRefutador(BaseModel):
    refutado: bool = Field(description="True si el hallazgo es falso o irrelevante.")
    motivo: str


# --- Evaluación -----------------------------------------------------------------------


@dataclass
class EntradaGate:
    fase: GateFase
    material: str
    criterios: list[Criterio]
    gobernanza: list[ItemGobernanza]
    perfil: PerfilConfig
    tope: TopeGate
    nivel: NivelCodigo
    hallazgos_previos: list[Hallazgo] = field(default_factory=list)


@dataclass
class Llamada:
    """Una llamada a modelo, para telemetría, auditoría y consumo."""

    nodo: str
    rol: str
    respuesta: RespuestaModelo
    sha256_enviado: str
    effort: str | None


@dataclass
class EvaluacionPanel:
    hallazgos: list[Hallazgo]
    llamadas: list[Llamada]
    criticos: list[str]
    refutador: bool
    error: str | None = None


def _sistema(fase: GateFase, lentes: list[Lente], gobernanza: list[ItemGobernanza]) -> str:
    """Prefijo estable: rúbrica y gobernanza primero; el material va en el contenido."""

    partes = [
        f"Eres un crítico del gate '{fase.value}' de un protocolo de desarrollo guiado por especificación.",
        "Evalúa SOLO estos lentes y etiqueta cada hallazgo con el id del lente:",
        *[f"- {lente.id}: {lente.rubrica}" for lente in lentes],
        _SEVERIDADES,
        "Cita la sección (o ruta y líneas en código) y da evidencia concreta. Propón la corrección.",
        "Gobernanza aplicable:",
        *([f"- [{g.tipo}] {g.id}: {g.titulo}. {g.resumen}" for g in gobernanza] or ["- (ninguna aplicable)"]),
    ]
    return "\n".join(partes)


def _contenido(entrada: EntradaGate) -> str:
    partes = []
    if entrada.criterios:
        partes.append(
            "Criterios de aceptación:\n" + "\n".join(f"- {c.id}: {c.texto}" for c in entrada.criterios)
        )
    if entrada.hallazgos_previos:
        partes.append(
            "Hallazgos de la iteración anterior (verifica si siguen abiertos):\n"
            + "\n".join(
                f"- {h.id} [{h.lente}/{h.severidad.value}] {h.titulo}" for h in entrada.hallazgos_previos
            )
        )
    partes.append("Material a evaluar:\n" + entrada.material)
    return "\n\n".join(partes)


def _sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _a_hallazgo(
    p: HallazgoPropuesto, fase: GateFase, n: int, lentes: set[str], criterios: set[str]
) -> Hallazgo:
    lente = p.lente if p.lente in lentes else sorted(lentes)[0]
    try:
        cita = Cita(ruta=p.ruta, linea_inicio=p.linea_inicio, linea_fin=p.linea_fin, seccion=p.seccion)
    except ValidationError:
        cita = Cita(seccion=(p.seccion or p.ruta or "(sin ubicación)")[:200])
    return Hallazgo(
        id=f"H-{n}",
        gate=fase,
        lente=lente[:80],
        severidad=p.severidad,
        criterio=p.criterio if p.criterio in criterios else None,
        titulo=(p.titulo or "(sin título)")[:200],
        cita=cita,
        evidencia=(p.evidencia or "(sin evidencia)")[:4000],
        propuesta=p.propuesta[:4000] if p.propuesta else None,
    )


async def evaluar_panel(
    entrada: EntradaGate, proveedores: Proveedores, primer_id: int = 1
) -> EvaluacionPanel:
    """Corre el panel en paralelo y, si el tope lo pide, el refutador sobre los hallazgos alta."""

    lentes = LENTES[entrada.fase]
    grupos = repartir(lentes, entrada.tope.criticos)
    contenido = _contenido(entrada)
    llamadas: list[Llamada] = []

    async def critico(
        i: int, grupo: list[Lente]
    ) -> tuple[list[Lente], RespuestaModelo[SalidaCritico], Llamada]:
        rol = grupo[0].rol
        req = requisito(entrada.perfil, rol)
        eleccion = proveedores.elegir(rol, req, entrada.nivel)
        sistema = _sistema(entrada.fase, grupo, entrada.gobernanza)
        r = await eleccion.proveedor.completar(
            PeticionModelo(
                rol=rol,
                modelo=eleccion.modelo,
                sistema=sistema,
                contenido=contenido,
                esquema=SalidaCritico,
                effort=req.effort,
                etiqueta=f"critico-{i + 1}",
            )
        )
        nodo = f"gate-{entrada.fase.value}:" + "+".join(lente.id for lente in grupo)
        return grupo, r, Llamada(nodo, rol, r, _sha(sistema + "\n" + contenido), req.effort)

    try:
        resultados = await asyncio.gather(*(critico(i, g) for i, g in enumerate(grupos)))
    except (ErrorProveedor, PerfilInsatisfacible) as exc:
        return EvaluacionPanel([], llamadas, [], False, error=str(exc))

    criterios = {c.id for c in entrada.criterios}
    hallazgos: list[Hallazgo] = []
    for grupo, r, llamada in resultados:
        llamadas.append(llamada)
        ids = {lente.id for lente in grupo}
        for p in r.valor.hallazgos:
            hallazgos.append(_a_hallazgo(p, entrada.fase, primer_id + len(hallazgos), ids, criterios))

    refutador = False
    if entrada.tope.adversarial:
        altas = [h for h in hallazgos if h.severidad == Severidad.alta]
        if altas:
            refutador = True
            try:
                veredictos = await asyncio.gather(*(_refutar(h, entrada, proveedores) for h in altas))
            except (ErrorProveedor, PerfilInsatisfacible) as exc:
                return EvaluacionPanel(hallazgos, llamadas, _nombres(grupos), True, error=str(exc))
            refutados = set()
            for h, (salida, llamada) in zip(altas, veredictos, strict=True):
                llamadas.append(llamada)
                if salida.refutado:
                    refutados.add(h.id)
            hallazgos = [h.model_copy(update={"refutado": h.id in refutados}) for h in hallazgos]
    return EvaluacionPanel(hallazgos, llamadas, _nombres(grupos), refutador)


def _nombres(grupos: list[list[Lente]]) -> list[str]:
    return [lente.id for g in grupos for lente in g]


async def _refutar(
    h: Hallazgo, entrada: EntradaGate, proveedores: Proveedores
) -> tuple[SalidaRefutador, Llamada]:
    req = requisito(entrada.perfil, "refutador")
    eleccion = proveedores.elegir("refutador", req, entrada.nivel)
    sistema = (
        f"Eres el verificador adversarial del gate '{entrada.fase.value}'. Intenta demostrar que el "
        "hallazgo es falso o irrelevante para el material. Ante la duda, refuta."
    )
    contenido = (
        f"Hallazgo {h.id} [{h.lente}/{h.severidad.value}]: {h.titulo}\nEvidencia: {h.evidencia}\n\n"
        + _contenido(entrada)
    )
    r = await eleccion.proveedor.completar(
        PeticionModelo(
            rol="refutador",
            modelo=eleccion.modelo,
            sistema=sistema,
            contenido=contenido,
            esquema=SalidaRefutador,
            effort=req.effort,
            etiqueta=f"refutar-{h.id}",
        )
    )
    return r.valor, Llamada(
        f"gate-{entrada.fase.value}:refutador", "refutador", r, _sha(sistema + contenido), req.effort
    )


# --- Convergencia ---------------------------------------------------------------------


class Accion(StrEnum):
    aprobar = "aprobar"
    refinar = "refinar"
    escalar = "escalar"


@dataclass(frozen=True)
class Decision:
    accion: Accion
    causa: CausaEscalado | None = None
    motivo: str = ""


def _misma_ubicacion(a: Hallazgo, b: Hallazgo) -> bool:
    return a.lente == b.lente and (
        (a.cita.ruta, a.cita.linea_inicio, a.cita.seccion)
        == (b.cita.ruta, b.cita.linea_inicio, b.cita.seccion)
        or (a.criterio is not None and a.criterio == b.criterio)
    )


def decidir(iteracion: int, tope: TopeGate, actuales: list[Hallazgo], previos: list[Hallazgo]) -> Decision:
    """Regla de convergencia del kit, en este orden, tras cada tanda del panel."""

    abiertos = bloqueantes(actuales)
    if not abiertos:
        return Decision(Accion.aprobar)
    previos_bloq = bloqueantes(previos)
    if previos_bloq:
        reaparecen = [h for h in abiertos if any(_misma_ubicacion(h, p) for p in previos_bloq)]
        if reaparecen:
            return Decision(
                Accion.escalar,
                CausaEscalado.sin_convergencia,
                f"sin convergencia: reaparece {reaparecen[0].titulo}",
            )
        if len(abiertos) >= len(previos_bloq):
            return Decision(
                Accion.escalar,
                CausaEscalado.sin_convergencia,
                "sin convergencia: los hallazgos alta/media no bajan",
            )
    if iteracion >= tope.iteraciones:
        return Decision(
            Accion.escalar,
            CausaEscalado.hallazgos_sin_resolver,
            f"tope de {tope.iteraciones} iteraciones con hallazgos abiertos",
        )
    return Decision(Accion.refinar)
