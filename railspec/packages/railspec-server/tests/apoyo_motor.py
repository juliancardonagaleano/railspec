"""Dobles y arnés simulado para las pruebas del motor."""

from __future__ import annotations

import hashlib
import itertools
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from railspec.contracts.comun import (
    Actor,
    AlcanceRepositorio,
    AlcanceWorkspace,
    Canal,
    Modo,
    NivelCodigo,
    Presupuesto,
    Proveedor,
    TipoActor,
)
from railspec.contracts.estado import Decision
from railspec.contracts.mandato import Delegacion, LimitesMandato, MandatoContenido, TipoDelegacion
from railspec.contracts.orden import Artefacto
from railspec.contracts.reporte import ArtefactoRedactado, ReporteOrden, ResultadoOrden, ResultadoValidacion
from railspec.contracts.repositorio import (
    Auditoria,
    HostingChat,
    PoliticaChat,
    VinculoRepositorio,
    politica_chat_por_defecto,
)
from railspec.contracts.snapshot import CambioArchivo, EscaneoSecretos, EstadoArchivo, ModoDelta, Snapshot
from railspec.contracts.tools import (
    MandateApproveEntrada,
    MandateProposeEntrada,
    RepositorioInicio,
    UnitAdvanceEntrada,
    UnitApproveEntrada,
    UnitStartEntrada,
)
from railspec.server.estado import CheckpointsMongo, almacen_en_memoria
from railspec.server.motor import Motor, Nucleo
from railspec.server.motor.gate import SalidaCritico
from railspec.server.motor.gobernanza import GobernanzaFija
from railspec.server.proveedores import Proveedores
from railspec.server.proveedores.falso import ProveedorGuionado

ORG, WS, REPO = "acme", "certificados", "certificados-api"
BASE = "4063ae9" + "0" * 33
WS_ALCANCE = AlcanceWorkspace(org=ORG, workspace=WS)

JULIAN = Actor(tipo=TipoActor.humano, canal=Canal.arnes, github_id=83125327, login="juliancardonagaleano")
JULIAN_CONSOLA = JULIAN.model_copy(update={"canal": Canal.consola})

SPEC = """# Emitir PDF firmado

## Problema
Los certificados salen sin firma.

## Alcance
Firmar el PDF al emitir.

## Fuera de alcance
Revocación.

## Criterios de aceptación
- CA-01: El PDF emitido lleva firma verificable.
- CA-02: Un PDF sin firma se rechaza al emitir.
"""

PLAN = """# Plan

## Enfoque
Reutilizar el firmador existente.

## Grupos
### G1 — Firma
Archivos: `src/pdf.py`, `tests/test_pdf.py`
Firmar al emitir.

## Riesgos
- Certificado caducado: validar vigencia.

## Validación
Comando: `pytest tests/test_pdf.py`
"""

TASKS = """# Tareas

## G1 — Firma
- [ ] T-01: Firmar el PDF al emitir (CA-01)
- [ ] T-02: Rechazar PDF sin firma (CA-02)
"""

TEXTOS = {Artefacto.spec: SPEC, Artefacto.plan: PLAN, Artefacto.tasks: TASKS}


class Relojito:
    def __init__(self) -> None:
        self.t = datetime(2026, 9, 30, 19, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.t += timedelta(milliseconds=10)
        return self.t


def ids() -> Callable[[], uuid.UUID]:
    contador = itertools.count(1)
    return lambda: uuid.UUID(int=next(contador), version=4)


def critico_sin_hallazgos(peticion):
    if peticion.esquema is SalidaCritico:
        return SalidaCritico(hallazgos=[])
    raise AssertionError(f"petición inesperada: {peticion.esquema}")


def construir(
    guion=critico_sin_hallazgos,
    gobernanza=None,
    nivel: NivelCodigo | None = None,
    en_linea: bool = True,
):
    almacen = almacen_en_memoria()
    proveedor = ProveedorGuionado(guion)
    reloj = Relojito()
    nucleo = Nucleo(
        almacen=almacen,
        proveedores=Proveedores({Proveedor.foundry: proveedor}),
        gobernanza=gobernanza or GobernanzaFija(),
        reloj=reloj,
        nuevo_id=ids(),
    )
    if nivel is not None:
        almacen.guardar_configuracion([vinculo(nivel)])
    motor = Motor(nucleo, CheckpointsMongo(almacen.db), en_linea=en_linea)
    return motor, proveedor


def vinculo(nivel: NivelCodigo) -> VinculoRepositorio:
    ahora = datetime(2026, 9, 30, tzinfo=UTC)
    politica = politica_chat_por_defecto(nivel)
    return VinculoRepositorio(
        version=1,
        auditoria=Auditoria(creado_por=JULIAN, creado_en=ahora, actualizado_por=JULIAN, actualizado_en=ahora),
        alcance=AlcanceRepositorio(org=ORG, workspace=WS, repositorio=REPO),
        url="https://github.com/acme/certificados-api",
        rol="primario",
        nivel_codigo=nivel,
        chat_contexto_codigo=politica,
    )


def entrada_start(**extra) -> UnitStartEntrada:
    datos = dict(
        alcance=WS_ALCANCE,
        repositorios=[RepositorioInicio(repositorio=REPO, rama="main", base_commit=BASE)],
        titulo="Emitir PDF firmado",
        pedido="Los certificados deben salir firmados.",
        version_contrato_cliente="1.0",
    )
    datos.update(extra)
    return UnitStartEntrada(**datos)


def snapshot(orden, nivel: NivelCodigo = NivelCodigo.restringido, rutas=("src/pdf.py",)) -> Snapshot:
    return Snapshot(
        id=uuid.uuid4(),
        unidad=orden.unidad,
        repositorio=orden.repositorio,
        base_commit=orden.base_commit,
        hash_arbol="a" * 40,
        creado_en=datetime(2026, 9, 30, 20, tzinfo=UTC),
        nivel_codigo=nivel,
        modo_delta=ModoDelta.solo_hashes,
        archivos=[
            CambioArchivo(
                ruta=r, estado=EstadoArchivo.agregado, sha256_despues=hashlib.sha256(r.encode()).hexdigest()
            )
            for r in rutas
        ],
        escaneo_secretos=EscaneoSecretos(herramienta="gitleaks", version="8.21", hallazgos=0),
    )


def reporte(
    orden,
    *,
    resultado=ResultadoOrden.completado,
    texto: str | None = None,
    codigo_salida=0,
    rutas=("src/pdf.py",),
    motivo=None,
) -> ReporteOrden:
    base = dict(
        orden_id=orden.id,
        secuencia=orden.secuencia,
        unidad=orden.unidad,
        base_commit=orden.base_commit,
        resultado=resultado,
        reportado_en=datetime(2026, 9, 30, 20, tzinfo=UTC),
        motivo=motivo or (None if resultado == ResultadoOrden.completado else "no pude"),
    )
    if resultado == ResultadoOrden.completado:
        if orden.tipo in ("redactar", "refinar"):
            contenido = texto if texto is not None else TEXTOS[orden.artefacto]
            base["artefacto"] = ArtefactoRedactado(
                tipo=orden.artefacto,
                contenido=contenido,
                sha256=hashlib.sha256(contenido.encode()).hexdigest(),
            )
        elif orden.tipo == "implementar":
            base["snapshot"] = snapshot(orden, rutas=rutas)
            base["tareas_completadas"] = [t.id for t in orden.tareas]
        elif orden.tipo == "validar":
            base["validacion"] = ResultadoValidacion(
                comando=orden.comando_validacion,
                codigo_salida=codigo_salida,
                duracion_ms=1200,
                salida="2 passed" if codigo_salida == 0 else "1 failed",
            )
    return ReporteOrden(**base)


async def avanzar(motor: Motor, alcance, actor=JULIAN):
    estado = motor.n.almacen.obtener_estado(alcance)
    return (
        await motor.advance(UnitAdvanceEntrada(unidad=alcance, version_vista=estado.version), actor)
    ).avance


async def aprobar(
    motor: Motor, alcance, checkpoint_id, decision=Decision.aprobado, comentario=None, actor=JULIAN
):
    return await motor.approve(
        UnitApproveEntrada(
            unidad=alcance, checkpoint=checkpoint_id, decision=decision, comentario=comentario
        ),
        actor,
    )


# --- mandato (1.11) -----------------------------------------------------------------------------------

PLAN_PDF = "pdf-a"
DELEGACIONES = [
    Delegacion(id="D-1", tipo=TipoDelegacion.pre_decidida, texto="Usar el firmador existente."),
    Delegacion(id="D-2", tipo=TipoDelegacion.con_criterio, texto="Nombres de pruebas y de helpers."),
    Delegacion(id="D-3", tipo=TipoDelegacion.reservada, texto="Cambios de esquema de base de datos."),
]


def contenido_mandato(modo: Modo = Modo.supervisado, **limites) -> MandatoContenido:
    """Un mandato válido; el desatendido trae un tope de tokens porque el contrato lo exige."""

    datos = dict(repositorios=[REPO])
    if modo == Modo.desatendido:
        datos["presupuesto"] = Presupuesto(tokens_max=10_000_000)
    datos.update(limites)
    return MandatoContenido(
        titulo="Firmar los PDF",
        objetivo="Los certificados emitidos salen firmados.",
        modo=modo,
        limites=LimitesMandato(**datos),
        delegaciones=list(DELEGACIONES),
    )


async def proponer_mandato(motor: Motor, id_: str = PLAN_PDF, contenido=None, actor=JULIAN_CONSOLA, **kw):
    return await motor.n.mandatos.propose(
        MandateProposeEntrada(alcance=WS_ALCANCE, id=id_, contenido=contenido or contenido_mandato(**kw)),
        actor,
    )


async def aprobar_mandato(motor: Motor, id_: str = PLAN_PDF, actor=JULIAN_CONSOLA, comentario=None):
    m = motor.n.almacen.obtener_mandato(WS_ALCANCE, id_)
    return await motor.n.mandatos.approve(
        MandateApproveEntrada(
            alcance=WS_ALCANCE,
            id=id_,
            version_vista=m.version,
            huella=m.contenido.huella(),
            comentario=comentario,
        ),
        actor,
    )


async def mandato_aprobado(motor: Motor, id_: str = PLAN_PDF, **kw):
    """Redacta y aprueba un mandato (lo que hace una persona en la consola) y lo devuelve."""

    await proponer_mandato(motor, id_, **kw)
    return (await aprobar_mandato(motor, id_)).mandato


def entrada_mandato(modo: Modo = Modo.supervisado, plan: str = PLAN_PDF, **extra) -> UnitStartEntrada:
    return entrada_start(modo=modo, plan=plan, **extra)


__all__ = ["HostingChat", "PoliticaChat"]
