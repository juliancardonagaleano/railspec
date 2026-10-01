"""El DAG del protocolo sobre Microsoft Agent Framework.

Un workflow por unidad (``nombre_workflow``), con estado persistido en
checkpoints. Los nodos son de tres clases:

- deterministas: ``triaje``, ``avance``, ``cierre`` y la capa determinista del gate;
- de modelo: el panel de críticos y el refutador dentro de ``gate``;
- de espera externa: ``redaccion`` e ``implementacion`` emiten órdenes al arnés
  y ``decision`` abre checkpoints humanos. Cada espera es un ``request_info``
  cuyo ``request_id`` es el id de la orden o del checkpoint, así que la tool
  que recibe la respuesta sabe exactamente a qué petición entregarla.

Los cuatro modos son configuraciones del mismo grafo: cambia qué checkpoints
abre ``decision``.

::

    triaje → redaccion ⇄ gate → decision → avance → redaccion (plan, tasks)
                                   ↑   ↓                ↓
                          implementacion ⇄ gate      cierre
"""

from __future__ import annotations

import fnmatch
import hashlib
import inspect
import logging
import sys
from typing import Any, Never

from agent_framework import (
    Executor,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    handler,
    register_checkpoint_type,
    response_handler,
)
from pydantic import BaseModel, Field
from railspec.contracts.comun import (
    AlcanceUnidad,
    CausaEscalado,
    EstadoFase,
    Fase,
    GateFase,
    GobernanzaConsultada,
    Modo,
    NivelCodigo,
    Severidad,
    Veredicto,
)
from railspec.contracts.estado import (
    Checkpoint,
    Decision,
    Rehabilitacion,
    ResolucionCheckpoint,
    ResultadoGate,
    TipoCheckpoint,
)
from railspec.contracts.eventos import CheckpointSolicitado, OrdenEmitida, VeredictoEmitido
from railspec.contracts.hallazgos import Cita, Hallazgo, bloqueantes
from railspec.contracts.orden import (
    Artefacto,
    ContextoArmado,
    OrdenImplementar,
    OrdenRedactar,
    OrdenRefinar,
    OrdenValidar,
)
from railspec.contracts.reporte import ReporteOrden, ResultadoOrden, ResultadoValidacion
from railspec.contracts.snapshot import Snapshot

from ..estado.checkpoints import nombre_workflow
from . import ordenes
from .artefactos import Extraido, GrupoPlan, hallazgos_estructura, validar
from .gate import Accion, EntradaGate, decidir, evaluar_panel
from .nucleo import Nucleo, presupuesto_agotado
from .perfiles import ACTOR_SERVIDOR, tope_gate

log = logging.getLogger("railspec.motor")

# --- Mensajes entre nodos ------------------------------------------------------------


class Arranque(BaseModel):
    pedido: str


class IniciarRedaccion(BaseModel):
    artefacto: Artefacto
    nota: str | None = None


class RefinarArtefacto(BaseModel):
    artefacto: Artefacto
    hallazgos: list[Hallazgo]


class ArtefactoListo(BaseModel):
    artefacto: Artefacto


class CodigoListo(BaseModel):
    pass


class RefinarCodigo(BaseModel):
    hallazgos: list[Hallazgo]


class GateSuperado(BaseModel):
    fase: GateFase


class GateEscalado(BaseModel):
    fase: GateFase
    motivo: str


class Avanzar(BaseModel):
    desde: GateFase


class IniciarImplementacion(BaseModel):
    pass


class Cerrar(BaseModel):
    pass


class Rechazada(BaseModel):
    motivo: str


class DatosUnidad(BaseModel):
    """Estado de trabajo del DAG (vive en el checkpoint, no en ``EstadoUnidad``)."""

    pedido: str = ""
    extraido: Extraido = Field(default_factory=Extraido)
    artefactos: dict[str, str] = Field(default_factory=dict)
    iteraciones: dict[str, int] = Field(default_factory=dict)
    previos: dict[str, list[Hallazgo]] = Field(default_factory=dict)
    siguiente_hallazgo: int = 1
    grupo: int = 0
    correccion: bool = False
    snapshots: list[str] = Field(default_factory=list)
    tareas_completadas: list[str] = Field(default_factory=list)
    validacion: ResultadoValidacion | None = None
    ultima_orden: dict[str, Any] | None = None
    checkpoint_gate: GateFase | None = None


SalidaGate = RefinarArtefacto | RefinarCodigo | GateSuperado | GateEscalado
CLAVE_DATOS = "railspec.datos"
GATE_DE_ARTEFACTO = {
    Artefacto.spec: GateFase.spec,
    Artefacto.plan: GateFase.plan,
    Artefacto.tasks: GateFase.tasks,
}
ARTEFACTO_DE_GATE = {v: k for k, v in GATE_DE_ARTEFACTO.items()}
FASE_DE_GATE = {
    GateFase.spec: Fase.spec,
    GateFase.plan: Fase.plan,
    GateFase.tasks: Fase.tasks,
    GateFase.codigo: Fase.implement,
}
CLASES_ORDEN = {
    "redactar": OrdenRedactar,
    "refinar": OrdenRefinar,
    "implementar": OrdenImplementar,
    "validar": OrdenValidar,
}


# --- Base de los nodos -------------------------------------------------------------------


class Nodo(Executor):
    def __init__(self, id: str, nucleo: Nucleo, alcance: AlcanceUnidad) -> None:
        super().__init__(id=id)
        self.n = nucleo
        self.alcance = alcance

    def datos(self, ctx: WorkflowContext) -> DatosUnidad:
        crudo = ctx.get_state(CLAVE_DATOS)
        return DatosUnidad.model_validate(crudo) if crudo else DatosUnidad()

    def guardar(self, ctx: WorkflowContext, datos: DatosUnidad) -> None:
        ctx.set_state(CLAVE_DATOS, datos.model_dump(mode="json"))

    async def contexto(self, fase: GateFase, datos: DatosUnidad) -> ContextoArmado:
        estado = self.n.leer(self.alcance)
        r = await self.n.gobernanza.consultar(self.alcance, fase, _objeto(estado.titulo, datos))
        insumos = self.n.insumos.resolver(estado) if self.n.insumos is not None and estado.insumos else []
        return ContextoArmado(gobernanza=r.items, gobernanza_consultada=r.consultada, insumos=insumos)

    async def emitir_orden(self, ctx: WorkflowContext, orden: Any) -> None:
        self.n.almacen.guardar_orden(orden)
        self.n.escribir(
            self.alcance,
            lambda e: {
                "orden_vigente": orden.id,
                "secuencia_ordenes": orden.secuencia,
                "fase": orden.fase,
                "estado": EstadoFase.en_progreso,
            },
        )
        self.n.emitir(
            self.alcance, OrdenEmitida(orden_id=orden.id, secuencia_orden=orden.secuencia), ACTOR_SERVIDOR
        )
        datos = self.datos(ctx)
        datos.ultima_orden = orden.model_dump(mode="json")
        self.guardar(ctx, datos)
        await ctx.request_info(orden, ReporteOrden, request_id=str(orden.id))

    async def abrir_checkpoint(
        self,
        ctx: WorkflowContext,
        tipo: TipoCheckpoint,
        fase: Fase,
        pregunta: str,
        bloquea: bool,
        sha: str | None = None,
    ) -> None:
        cp = Checkpoint(
            id=self.n.nuevo_id(),
            tipo=tipo,
            fase=fase,
            pregunta=pregunta[:2000],
            artefacto_sha256=sha,
            abierto_en=self.n.reloj(),
        )
        self.n.escribir(
            self.alcance,
            lambda e: {
                "checkpoint_pendiente": cp,
                "fase": fase,
                "estado": EstadoFase.bloqueado if bloquea else EstadoFase.en_progreso,
            },
        )
        self.n.emitir(self.alcance, CheckpointSolicitado(checkpoint_id=cp.id), ACTOR_SERVIDOR)
        await ctx.request_info(cp, ResolucionCheckpoint, request_id=str(cp.id))


class ConParadas(Nodo):
    """Nodos que emiten órdenes: un reporte fallido o bloqueado abre una parada."""

    async def parada(self, ctx: WorkflowContext, orden: Any, reporte: ReporteOrden) -> None:
        await self.abrir_checkpoint(
            ctx,
            TipoCheckpoint.parada,
            orden.fase,
            f"El arnés reportó '{reporte.resultado.value}' en la orden {orden.tipo} #{orden.secuencia}: "
            f"{reporte.motivo}. ¿Reintentar?",
            bloquea=True,
        )

    @response_handler
    async def resolver_parada(
        self, cp: Checkpoint, resolucion: ResolucionCheckpoint, ctx: WorkflowContext[Rechazada]
    ) -> None:
        if resolucion.decision == Decision.rechazado:
            await ctx.send_message(Rechazada(motivo=resolucion.comentario or "rechazada"), target_id="cierre")
            return
        datos = self.datos(ctx)
        previa = datos.ultima_orden or {}
        estado = self.n.leer(self.alcance)
        instrucciones = previa.get("instrucciones", "")
        if resolucion.decision == Decision.cambios_solicitados:
            instrucciones = f"{instrucciones}\n\nIndicación humana tras la parada:\n{resolucion.comentario}"
        nueva = CLASES_ORDEN[previa["tipo"]].model_validate(
            {
                **previa,
                "id": str(self.n.nuevo_id()),
                "secuencia": estado.secuencia_ordenes + 1,
                "emitida_en": self.n.reloj().isoformat(),
                "expira_en": None,
                "instrucciones": instrucciones[:20_000],
            }
        )
        await self.emitir_orden(ctx, nueva)


def _objeto(titulo: str, datos: DatosUnidad) -> str:
    criterios = "; ".join(c.texto for c in datos.extraido.criterios[:5])
    return f"{titulo}. {criterios or datos.pedido[:400]}"


def _sha(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


# --- Nodos -------------------------------------------------------------------------------


class Triaje(Nodo):
    """Punto de entrada: el riesgo y el perfil ya los fijó ``unit.start``; arranca el spec."""

    @handler
    async def arrancar(self, msg: Arranque, ctx: WorkflowContext[IniciarRedaccion]) -> None:
        self.guardar(ctx, DatosUnidad(pedido=msg.pedido))
        await ctx.send_message(IniciarRedaccion(artefacto=Artefacto.spec), target_id="redaccion")


class Redaccion(ConParadas):
    @handler
    async def iniciar(self, msg: IniciarRedaccion, ctx: WorkflowContext) -> None:
        datos = self.datos(ctx)
        estado = self.n.leer(self.alcance)
        contexto = await self.contexto(GATE_DE_ARTEFACTO[msg.artefacto], datos)
        orden = ordenes.redactar(
            estado,
            msg.artefacto,
            datos.pedido,
            contexto,
            datos.extraido.criterios,
            self.n.nuevo_id(),
            self.n.reloj(),
            msg.nota,
        )
        await self.emitir_orden(ctx, orden)

    @handler
    async def refinar(self, msg: RefinarArtefacto, ctx: WorkflowContext) -> None:
        datos = self.datos(ctx)
        estado = self.n.leer(self.alcance)
        contexto = await self.contexto(GATE_DE_ARTEFACTO[msg.artefacto], datos)
        actual = datos.artefactos.get(msg.artefacto.value, "")
        orden = ordenes.refinar(
            estado,
            msg.artefacto,
            _sha(actual),
            msg.hallazgos,
            contexto,
            datos.extraido.criterios,
            self.n.nuevo_id(),
            self.n.reloj(),
        )
        await self.emitir_orden(ctx, orden)

    @response_handler
    async def recibir(
        self, orden: OrdenRedactar | OrdenRefinar, reporte: ReporteOrden, ctx: WorkflowContext[ArtefactoListo]
    ) -> None:
        if reporte.resultado != ResultadoOrden.completado or reporte.artefacto is None:
            await self.parada(ctx, orden, reporte)
            return
        datos = self.datos(ctx)
        datos.artefactos[orden.artefacto.value] = reporte.artefacto.contenido
        self.guardar(ctx, datos)
        await ctx.send_message(ArtefactoListo(artefacto=orden.artefacto), target_id="gate")


class Gate(Nodo):
    @handler
    async def artefacto(self, msg: ArtefactoListo, ctx: WorkflowContext[SalidaGate]) -> None:
        await self.evaluar(ctx, GATE_DE_ARTEFACTO[msg.artefacto])

    @handler
    async def codigo(self, _: CodigoListo, ctx: WorkflowContext[SalidaGate]) -> None:
        await self.evaluar(ctx, GateFase.codigo)

    async def evaluar(self, ctx: WorkflowContext[SalidaGate], fase: GateFase) -> None:
        datos = self.datos(ctx)
        estado = self.n.leer(self.alcance)
        iteracion = datos.iteraciones.get(fase.value, 0) + 1
        previos = datos.previos.get(fase.value, [])
        perfil = self.n.perfil(estado)
        tope = tope_gate(perfil, estado.riesgo)
        nivel = self.n.nivel(estado)

        gob = await self.n.gobernanza.consultar(self.alcance, fase, _objeto(estado.titulo, datos))
        if gob.consultada != GobernanzaConsultada.si:
            await self.escalar(
                ctx,
                datos,
                fase,
                CausaEscalado.sin_gobernanza,
                iteracion - 1,
                [],
                gob.consultada,
                f"gobernanza {gob.consultada.value}: {gob.detalle}",
            )
            return
        agotado = presupuesto_agotado(estado)
        if agotado:
            await self.escalar(
                ctx,
                datos,
                fase,
                CausaEscalado.presupuesto_agotado,
                iteracion - 1,
                [],
                gob.consultada,
                f"presupuesto agotado ({agotado})",
            )
            return

        # Capa determinista: sin tokens.
        if fase == GateFase.codigo:
            material, deterministas = self.material_codigo(datos, nivel, estado)
            extraido = datos.extraido
        else:
            artefacto = ARTEFACTO_DE_GATE[fase]
            material = datos.artefactos.get(artefacto.value, "")
            v = validar(artefacto, material, datos.extraido)
            deterministas = hallazgos_estructura(fase, v, datos.siguiente_hallazgo)
            extraido = v.extraido
        criticos: list[str] = []
        refutador = False
        if deterministas:
            hallazgos = deterministas
        else:
            entrada = EntradaGate(
                fase=fase,
                material=material,
                criterios=extraido.criterios,
                gobernanza=gob.items,
                perfil=perfil,
                tope=tope,
                nivel=nivel,
                hallazgos_previos=previos,
            )
            panel = await evaluar_panel(entrada, self.n.proveedores, datos.siguiente_hallazgo)
            if panel.llamadas:
                self.n.escribir(
                    self.alcance, lambda e: self.n.registrar_llamadas(e, fase, panel.llamadas), anunciar=False
                )
            if panel.error:
                await self.escalar(
                    ctx,
                    datos,
                    fase,
                    CausaEscalado.error_proveedor,
                    iteracion,
                    [],
                    gob.consultada,
                    f"proveedor: {panel.error}",
                )
                return
            hallazgos, criticos, refutador = panel.hallazgos, panel.criticos, panel.refutador
        datos.siguiente_hallazgo += len(hallazgos)

        decision = decidir(iteracion, tope, hallazgos, previos)
        if decision.accion == Accion.aprobar:
            if fase != GateFase.codigo:
                datos.extraido = extraido
            datos.iteraciones.pop(fase.value, None)
            datos.previos.pop(fase.value, None)
            self.guardar(ctx, datos)
            resultado = ResultadoGate(
                veredicto=Veredicto.aprobado if iteracion == 1 else Veredicto.refinado,
                iteraciones=iteracion,
                hallazgos=[h for h in hallazgos if h not in bloqueantes(hallazgos)],
                gobernanza_consultada=gob.consultada,
                criticos=criticos,
                refutador=refutador,
                cerrado_en=self.n.reloj(),
            )
            self.cerrar_gate(fase, resultado)
            await ctx.send_message(GateSuperado(fase=fase), target_id="decision")
        elif decision.accion == Accion.refinar:
            datos.iteraciones[fase.value] = iteracion
            datos.previos[fase.value] = bloqueantes(hallazgos)
            self.guardar(ctx, datos)
            abiertos = [h for h in hallazgos if not h.refutado]
            if fase == GateFase.codigo:
                await ctx.send_message(RefinarCodigo(hallazgos=abiertos), target_id="implementacion")
            else:
                await ctx.send_message(
                    RefinarArtefacto(artefacto=ARTEFACTO_DE_GATE[fase], hallazgos=abiertos),
                    target_id="redaccion",
                )
        else:
            await self.escalar(
                ctx,
                datos,
                fase,
                decision.causa or CausaEscalado.hallazgos_sin_resolver,
                iteracion,
                bloqueantes(hallazgos),
                gob.consultada,
                decision.motivo,
                criticos,
                refutador,
            )

    def cerrar_gate(self, fase: GateFase, resultado: ResultadoGate) -> None:
        self.n.escribir(self.alcance, lambda e: {"gates": {**e.gates, fase: resultado}})
        self.n.emitir(
            self.alcance, VeredictoEmitido(gate=fase, veredicto=resultado.veredicto), ACTOR_SERVIDOR
        )

    async def escalar(
        self,
        ctx,
        datos,
        fase,
        causa,
        iteraciones,
        hallazgos,
        consultada,
        motivo,
        criticos=None,
        refutador=False,
    ) -> None:
        datos.iteraciones.pop(fase.value, None)
        datos.previos.pop(fase.value, None)
        self.guardar(ctx, datos)
        resultado = ResultadoGate(
            veredicto=Veredicto.escalado,
            causa=causa,
            iteraciones=max(iteraciones, 0),
            hallazgos=hallazgos,
            gobernanza_consultada=consultada,
            criticos=criticos or [],
            refutador=refutador,
            cerrado_en=self.n.reloj(),
        )
        self.cerrar_gate(fase, resultado)
        await ctx.send_message(GateEscalado(fase=fase, motivo=motivo[:1500]), target_id="decision")

    def impacto_grafo(self, estado) -> str:
        """Impacto aguas arriba de la superposición de la unidad (railspec-graph), si hay grafo.

        ``impacto_superposicion`` no es parte de ``GraphStore``: se usa si el
        almacén de grafo lo ofrece. Un fallo del grafo no bloquea el gate.
        """

        impacto = getattr(self.n.grafo, "impacto_superposicion", None)
        if impacto is None:
            return ""
        from railspec.contracts.comun import AlcanceRepositorio, AlcanceWorkspace
        from railspec.contracts.tools import ConsultaResolve, GraphQueryEntrada

        a = self.alcance
        visibles = [
            AlcanceRepositorio(org=a.org, workspace=a.workspace, repositorio=r.repositorio)
            for r in estado.repositorios
        ]
        consulta = GraphQueryEntrada(
            alcance=AlcanceWorkspace(org=a.org, workspace=a.workspace),
            unidad=a.unidad,
            consulta=ConsultaResolve(nombre="*"),
        )
        try:
            tocados, alcanzados = impacto(consulta, visibles)
        except Exception as exc:  # el grafo informa, no decide
            log.warning("impacto de grafo no disponible para %s: %s", a.unidad, exc)
            return ""
        if not tocados:
            return ""
        riesgo = alcanzados[0].riesgo if alcanzados else "bajo"
        lineas = [
            f"Impacto en el grafo (riesgo {riesgo}): {len(tocados)} símbolos tocados, "
            f"{len(alcanzados)} afectados aguas arriba."
        ]
        for r in alcanzados[:100]:
            ref = r.ref
            nombre = getattr(ref, "nombre", None) or getattr(ref, "ruta", None) or ref.tipo
            rel = r.relacion.value if r.relacion else "?"
            lineas.append(f"- {nombre} ({rel}, distancia {r.distancia})")
        return "\n".join(lineas)

    def material_codigo(self, datos: DatosUnidad, nivel: NivelCodigo, estado) -> tuple[str, list[Hallazgo]]:
        """Resumen del cambio para los críticos; en ``restringido`` no hay texto de código."""

        snapshots: list[Snapshot] = [
            s
            for s in (self.n.almacen.obtener_snapshot(self.alcance, i) for i in datos.snapshots)
            if s is not None
        ]
        permitidos = sorted({a for g in datos.extraido.grupos_plan for a in g.archivos})
        permitidos.append(ordenes.glob_artefactos(estado))
        archivos: dict[str, str] = {}
        simbolos: list[str] = []
        diffs: list[str] = []
        for s in snapshots:
            for a in s.archivos:
                archivos[a.ruta] = a.estado.value
            if s.delta_indice:
                simbolos += [
                    f"{x.tipo.value} {x.nombre} ({x.ruta}:{x.linea_inicio})"
                    for x in s.delta_indice.simbolos_upsert
                ]
            if s.diff and nivel != NivelCodigo.restringido:
                diffs.append(s.diff[:60_000])
        n = datos.siguiente_hallazgo
        deterministas: list[Hallazgo] = []
        fuera = [r for r in archivos if permitidos and not any(fnmatch.fnmatch(r, g) for g in permitidos)]
        for r in sorted(fuera):
            deterministas.append(
                _h(
                    GateFase.codigo,
                    n + len(deterministas),
                    "encaje",
                    f"archivo fuera del plan: {r}",
                    Cita(ruta=r),
                    "El snapshot toca un archivo que ningún grupo del plan permite.",
                )
            )
        v = datos.validacion
        if v is not None and v.codigo_salida != 0:
            deterministas.append(
                _h(
                    GateFase.codigo,
                    n + len(deterministas),
                    "validacion",
                    f"la validación falla (código {v.codigo_salida})",
                    Cita(seccion="validación"),
                    f"{v.comando} → {v.salida[-3000:]}",
                )
            )
        partes = [
            f"Nivel de código: {nivel.value}.",
            "Tareas completadas: " + (", ".join(datos.tareas_completadas) or "(ninguna)"),
            "Archivos del cambio:\n"
            + ("\n".join(f"- {e} {r}" for r, e in sorted(archivos.items())) or "- (ninguno)"),
        ]
        if simbolos:
            partes.append("Símbolos tocados:\n" + "\n".join(f"- {s}" for s in simbolos[:400]))
        impacto = self.impacto_grafo(estado)
        if impacto:
            partes.append(impacto)
        if diffs:
            partes.append("Diff:\n" + "\n".join(diffs))
        if v is not None:
            partes.append(f"Validación: {v.comando} → código {v.codigo_salida}\n{v.salida[-6000:]}")
        return "\n\n".join(partes), deterministas


def _h(fase: GateFase, n: int, lente: str, titulo: str, cita: Cita, evidencia: str) -> Hallazgo:
    return Hallazgo(
        id=f"H-{n}",
        gate=fase,
        lente=lente,
        severidad=Severidad.alta,
        titulo=titulo[:200],
        cita=cita,
        evidencia=evidencia[:4000],
    )


class DecisionHumana(Nodo):
    """Checkpoints por modo y resolución de gates escalados."""

    @handler
    async def superado(self, msg: GateSuperado, ctx: WorkflowContext[Avanzar]) -> None:
        estado = self.n.leer(self.alcance)
        datos = self.datos(ctx)
        tipo = None
        fase_cp = FASE_DE_GATE[msg.fase]
        if estado.modo == Modo.interactivo and msg.fase in (GateFase.spec, GateFase.plan):
            tipo = TipoCheckpoint.aprobar_spec if msg.fase == GateFase.spec else TipoCheckpoint.aprobar_plan
        elif estado.modo == Modo.semi_autonomo and msg.fase == GateFase.tasks:
            tipo, fase_cp = TipoCheckpoint.paquete_aprobacion, Fase.aprobacion
        if tipo is None:
            await ctx.send_message(Avanzar(desde=msg.fase), target_id="avance")
            return
        datos.checkpoint_gate = msg.fase
        self.guardar(ctx, datos)
        artefacto = ARTEFACTO_DE_GATE.get(msg.fase)
        sha = (
            _sha(datos.artefactos[artefacto.value])
            if artefacto and artefacto.value in datos.artefactos
            else None
        )
        pregunta = {
            TipoCheckpoint.aprobar_spec: "¿Apruebas el spec?",
            TipoCheckpoint.aprobar_plan: "¿Apruebas el plan técnico?",
            TipoCheckpoint.paquete_aprobacion: "¿Apruebas el paquete spec + plan + tareas para implementar?",
        }[tipo]
        await self.abrir_checkpoint(ctx, tipo, fase_cp, pregunta, bloquea=False, sha=sha)

    @handler
    async def escalado(self, msg: GateEscalado, ctx: WorkflowContext) -> None:
        datos = self.datos(ctx)
        datos.checkpoint_gate = msg.fase
        self.guardar(ctx, datos)
        await self.abrir_checkpoint(
            ctx,
            TipoCheckpoint.gate_escalado,
            FASE_DE_GATE[msg.fase],
            f"El gate {msg.fase.value} escaló: {msg.motivo}. "
            "Aprobar lo rehabilita; pedir cambios reabre el refinamiento.",
            bloquea=True,
        )

    @response_handler
    async def resolver(
        self,
        cp: Checkpoint,
        res: ResolucionCheckpoint,
        ctx: WorkflowContext[Avanzar | RefinarArtefacto | RefinarCodigo | Rechazada],
    ) -> None:
        datos = self.datos(ctx)
        fase = datos.checkpoint_gate or GateFase.spec
        if res.decision == Decision.rechazado:
            await ctx.send_message(Rechazada(motivo=res.comentario or "rechazada"), target_id="cierre")
            return
        if res.decision == Decision.aprobado:
            if cp.tipo == TipoCheckpoint.gate_escalado:
                reh = Rehabilitacion(
                    actor=res.actor, en=res.en, motivo=res.comentario or "rehabilitado por decisión humana"
                )
                self.n.escribir(
                    self.alcance,
                    lambda e: {
                        "gates": {**e.gates, fase: e.gates[fase].model_copy(update={"rehabilitado": reh})}
                    },
                    res.actor,
                )
            await ctx.send_message(Avanzar(desde=fase), target_id="avance")
            return
        # Cambios solicitados: el comentario humano entra como hallazgo y el gate empieza de cero.
        humano = Hallazgo(
            id=f"H-{datos.siguiente_hallazgo}",
            gate=fase,
            lente="humano",
            severidad=Severidad.alta,
            titulo="Cambios solicitados en revisión humana",
            cita=Cita(seccion="(revisión humana)"),
            evidencia=res.comentario or "(sin comentario)",
        )
        datos.siguiente_hallazgo += 1
        datos.iteraciones.pop(fase.value, None)
        datos.previos.pop(fase.value, None)
        self.guardar(ctx, datos)
        if fase == GateFase.codigo:
            await ctx.send_message(RefinarCodigo(hallazgos=[humano]), target_id="implementacion")
        else:
            await ctx.send_message(
                RefinarArtefacto(artefacto=ARTEFACTO_DE_GATE[fase], hallazgos=[humano]), target_id="redaccion"
            )


class Avance(Nodo):
    @handler
    async def avanzar(
        self, msg: Avanzar, ctx: WorkflowContext[Cerrar | IniciarRedaccion | IniciarImplementacion]
    ) -> None:
        if msg.desde == GateFase.codigo:
            await ctx.send_message(Cerrar(), target_id="cierre")
            return
        siguiente = {GateFase.spec: Artefacto.plan, GateFase.plan: Artefacto.tasks}.get(msg.desde)
        if siguiente is not None:
            await ctx.send_message(IniciarRedaccion(artefacto=siguiente), target_id="redaccion")
        else:
            await ctx.send_message(IniciarImplementacion(), target_id="implementacion")


class Implementacion(ConParadas):
    def _grupos(self, datos: DatosUnidad) -> list[tuple[GrupoPlan, Any]]:
        plan = {g.id: g for g in datos.extraido.grupos_plan}
        return [
            (plan.get(g.id) or GrupoPlan(id=g.id, nombre=g.nombre), g) for g in datos.extraido.grupos_tareas
        ]

    async def _emitir_grupo(self, ctx: WorkflowContext, datos: DatosUnidad, hallazgos=None) -> None:
        estado = self.n.leer(self.alcance)
        grupos = self._grupos(datos)
        contexto = await self.contexto(GateFase.codigo, datos)
        if hallazgos:
            gp, _ = grupos[-1]
            todas = type(grupos[-1][1])(
                id=gp.id, nombre=gp.nombre, tareas=[t for _, g in grupos for t in g.tareas]
            )
            permitidos = sorted({a for g, _ in grupos for a in g.archivos})
            orden = ordenes.implementar(
                estado,
                gp,
                todas,
                datos.extraido.criterios,
                datos.extraido.comando_validacion,
                contexto,
                self.n.nuevo_id(),
                self.n.reloj(),
                hallazgos,
                permitidos,
            )
        else:
            gp, gt = grupos[datos.grupo]
            orden = ordenes.implementar(
                estado,
                gp,
                gt,
                datos.extraido.criterios,
                datos.extraido.comando_validacion,
                contexto,
                self.n.nuevo_id(),
                self.n.reloj(),
            )
        await self.emitir_orden(ctx, orden)

    @handler
    async def iniciar(self, _: IniciarImplementacion, ctx: WorkflowContext) -> None:
        datos = self.datos(ctx)
        datos.grupo, datos.correccion = 0, False
        self.guardar(ctx, datos)
        await self._emitir_grupo(ctx, datos)

    @handler
    async def corregir(self, msg: RefinarCodigo, ctx: WorkflowContext) -> None:
        datos = self.datos(ctx)
        datos.correccion = True
        self.guardar(ctx, datos)
        await self._emitir_grupo(ctx, datos, msg.hallazgos)

    @response_handler
    async def implementado(
        self, orden: OrdenImplementar, reporte: ReporteOrden, ctx: WorkflowContext[CodigoListo]
    ) -> None:
        if reporte.resultado != ResultadoOrden.completado:
            await self.parada(ctx, orden, reporte)
            return
        datos = self.datos(ctx)
        if reporte.snapshot is not None:
            datos.snapshots.append(str(reporte.snapshot.id))
        datos.tareas_completadas = sorted(set(datos.tareas_completadas) | set(reporte.tareas_completadas))
        if not datos.correccion and datos.grupo + 1 < len(datos.extraido.grupos_tareas):
            datos.grupo += 1
            self.guardar(ctx, datos)
            await self._emitir_grupo(ctx, datos)
            return
        self.guardar(ctx, datos)
        await self._validar_o_cerrar(ctx, datos)

    async def _validar_o_cerrar(self, ctx: WorkflowContext, datos: DatosUnidad) -> None:
        comando = datos.extraido.comando_validacion
        if not comando:
            await ctx.send_message(CodigoListo(), target_id="gate")
            return
        estado = self.n.leer(self.alcance)
        contexto = await self.contexto(GateFase.codigo, datos)
        orden = ordenes.validar(
            estado, comando, contexto, datos.extraido.criterios, self.n.nuevo_id(), self.n.reloj()
        )
        await self.emitir_orden(ctx, orden)

    @response_handler
    async def validado(
        self, orden: OrdenValidar, reporte: ReporteOrden, ctx: WorkflowContext[CodigoListo]
    ) -> None:
        if reporte.resultado == ResultadoOrden.bloqueado or reporte.validacion is None:
            await self.parada(ctx, orden, reporte)
            return
        datos = self.datos(ctx)
        datos.validacion = reporte.validacion
        self.guardar(ctx, datos)
        await ctx.send_message(CodigoListo(), target_id="gate")


class Cierre(Nodo):
    @handler
    async def cerrar(self, _: Cerrar, ctx: WorkflowContext[Never, str]) -> None:
        self.n.escribir(self.alcance, lambda e: {"fase": Fase.done, "estado": EstadoFase.completado})
        await ctx.yield_output("cerrada")

    @handler
    async def rechazar(self, msg: Rechazada, ctx: WorkflowContext[Never, str]) -> None:
        self.n.escribir(self.alcance, lambda e: {"estado": EstadoFase.bloqueado})
        await ctx.yield_output(f"rechazada: {msg.motivo}")


# --- Construcción -------------------------------------------------------------------------


def construir(nucleo: Nucleo, alcance: AlcanceUnidad, almacen_checkpoints: Any) -> Workflow:
    t = Triaje("triaje", nucleo, alcance)
    r = Redaccion("redaccion", nucleo, alcance)
    g = Gate("gate", nucleo, alcance)
    d = DecisionHumana("decision", nucleo, alcance)
    a = Avance("avance", nucleo, alcance)
    i = Implementacion("implementacion", nucleo, alcance)
    c = Cierre("cierre", nucleo, alcance)
    b = WorkflowBuilder(
        name=nombre_workflow(alcance), start_executor=t, checkpoint_storage=almacen_checkpoints
    )
    for origen, destinos in (
        (t, (r,)),
        (r, (g, c)),
        (g, (r, i, d)),
        (d, (r, i, a, c)),
        (a, (r, i, c)),
        (i, (g, c)),
    ):
        for destino in destinos:
            b.add_edge(origen, destino)
    return b.build()


def _registrar_tipos() -> None:
    """Tipos que viajan dentro de los checkpoints (peticiones pendientes, mensajes)."""

    import pydantic_core
    import railspec.contracts as contratos

    modulos = [m for nombre, m in list(sys.modules.items()) if nombre.startswith(contratos.__name__ + ".")]
    modulos.append(sys.modules[__name__])
    for modulo in modulos:
        for _, obj in inspect.getmembers(modulo, inspect.isclass):
            if obj.__module__ == modulo.__name__:
                register_checkpoint_type(obj)
    register_checkpoint_type(pydantic_core.TzInfo)


_registrar_tipos()
