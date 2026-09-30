"""Recorridos completos del DAG con el arnés simulado."""

from __future__ import annotations

import asyncio

from apoyo_motor import (
    JULIAN,
    JULIAN_CONSOLA,
    aprobar,
    avanzar,
    construir,
    entrada_start,
    reporte,
)
from railspec.contracts.comun import EstadoFase, Fase, GateFase, Veredicto
from railspec.contracts.estado import TipoCheckpoint
from railspec.contracts.eventos import Direccion


def correr(coro):
    return asyncio.run(coro)


async def _hasta_checkpoint_o_orden(motor, alcance):
    return await avanzar(motor, alcance)


def test_recorrido_interactivo_completo():
    async def caso():
        motor, proveedor = construir()
        salida = await motor.start(entrada_start(), JULIAN)
        alcance = salida.estado.unidad
        assert alcance.unidad == "0001-emitir-pdf-firmado"
        assert salida.estado.fase == Fase.spec and salida.version_contrato_negociada == "1.0"

        vistos = []
        for _ in range(20):
            av = await avanzar(motor, alcance)
            vistos.append(av.tipo if av.tipo != "orden" else f"orden:{av.orden.tipo}:{av.orden.fase.value}")
            if av.tipo == "cerrada":
                break
            if av.tipo == "orden":
                await motor.report(reporte(av.orden), JULIAN_CONSOLA)
            elif av.tipo == "checkpoint":
                await aprobar(motor, alcance, av.checkpoint.id, actor=JULIAN_CONSOLA)
        assert vistos == [
            "orden:redactar:spec",
            "checkpoint",
            "orden:redactar:plan",
            "checkpoint",
            "orden:redactar:tasks",
            "orden:implementar:implement",
            "orden:validar:implement",
            "cerrada",
        ]
        estado = motor.n.almacen.obtener_estado(alcance)
        assert estado.fase == Fase.done and estado.estado == EstadoFase.completado
        assert set(estado.gates) == {GateFase.spec, GateFase.plan, GateFase.tasks, GateFase.codigo}
        assert all(g.veredicto == Veredicto.aprobado for g in estado.gates.values())
        assert [r.decision.value for r in estado.resoluciones] == ["aprobado", "aprobado"]
        # Un crítico por gate en riesgo medio con perfil estándar = 2 críticos.
        assert len(proveedor.peticiones) == 8
        assert estado.consumo.tokens == 8 * 1200
        eventos = motor.n.almacen.eventos_desde(alcance, Direccion.remoto_a_local, 0)
        assert [e.secuencia for e in eventos] == list(range(1, len(eventos) + 1))
        tipos = {e.carga.tipo for e in eventos}
        assert {"orden.emitida", "veredicto.emitido", "checkpoint.solicitado", "estado.actualizado"} <= tipos

    correr(caso())


def test_checkpoints_por_modo_semi_autonomo():
    async def caso():
        motor, _ = construir()
        salida = await motor.start(entrada_start(), JULIAN_CONSOLA)
        alcance = salida.estado.unidad
        # Conversión de modo registrada (la hace el servidor; la tool llegará con el contrato).
        from railspec.contracts.comun import Modo
        from railspec.contracts.estado import ConversionModo

        motor.n.escribir(
            alcance,
            lambda e: {
                "modo": Modo.semi_autonomo,
                "modo_conversion": [
                    ConversionModo(
                        de=Modo.interactivo,
                        a=Modo.semi_autonomo,
                        actor=JULIAN_CONSOLA,
                        en=e.actualizado_en,
                        motivo="triaje",
                    )
                ],
            },
        )
        tipos = []
        for _ in range(20):
            av = await avanzar(motor, alcance)
            if av.tipo == "cerrada":
                break
            if av.tipo == "orden":
                await motor.report(reporte(av.orden), JULIAN_CONSOLA)
            else:
                tipos.append(av.checkpoint.tipo)
                assert motor.n.almacen.obtener_estado(alcance).fase == Fase.aprobacion
                await aprobar(motor, alcance, av.checkpoint.id, actor=JULIAN_CONSOLA)
        assert tipos == [TipoCheckpoint.paquete_aprobacion]

    correr(caso())


def test_artefactos_de_la_unidad_no_cuentan_como_fuera_del_plan():
    """El arnés escribe spec, plan y tasks en el worktree: el snapshot de implementación
    los lleva y ni el alcance de la orden ni el gate de código deben rechazarlos."""

    async def caso():
        motor, _ = construir()
        alcance = (await motor.start(entrada_start(), JULIAN)).estado.unidad
        artefactos = f".railspec/unidades/{alcance.unidad}"
        for _ in range(20):
            av = await avanzar(motor, alcance)
            if av.tipo == "cerrada":
                break
            if av.tipo == "checkpoint":
                await aprobar(motor, alcance, av.checkpoint.id, actor=JULIAN_CONSOLA)
                continue
            rutas = ("src/pdf.py",)
            if av.orden.tipo == "implementar":
                assert f"{artefactos}/*" in av.orden.alcance.permitidos
                rutas = ("src/pdf.py", f"{artefactos}/spec.md", f"{artefactos}/plan.md")
            await motor.report(reporte(av.orden, rutas=rutas), JULIAN_CONSOLA)
        estado = motor.n.almacen.obtener_estado(alcance)
        assert estado.fase == Fase.done
        gate = estado.gates[GateFase.codigo]
        assert gate.veredicto == Veredicto.aprobado
        assert not any("fuera del plan" in h.titulo for h in gate.hallazgos)

    correr(caso())
