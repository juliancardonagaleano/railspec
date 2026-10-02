"""Presupuestos del motor: por unidad, por fase y mensual del workspace, y el veredicto de la telemetría."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from apoyo_motor import JULIAN, ORG, WS, construir, entrada_start
from railspec.contracts.comun import CausaEscalado, Fase, GateFase, Presupuesto, Proveedor, Riesgo, Veredicto
from railspec.contracts.estado import Consumo, TipoCheckpoint
from railspec.contracts.repositorio import Auditoria, PresupuestoConfig, TelemetriaNodo
from railspec.server.motor import presupuesto
from railspec.server.motor.gate import Guardia, PresupuestoAgotado, SalidaCritico, _completar
from railspec.server.proveedores import ErrorProveedor, Proveedores
from railspec.server.proveedores.base import PeticionModelo, Uso
from railspec.server.proveedores.cache import CacheNodos
from railspec.server.proveedores.falso import ProveedorGuionado
from railspec.server.proveedores.seleccion import Eleccion
from test_motor_gate import es_checkpoint, hallazgo_alto, hasta

HOY = datetime(2026, 9, 30, 20, tzinfo=UTC)  # después de las unidades que crea el reloj de apoyo


def correr(coro):
    return asyncio.run(coro)


def config(workspace: str | None = None, **campos) -> PresupuestoConfig:
    auditoria = Auditoria(creado_por=JULIAN, creado_en=HOY, actualizado_por=JULIAN, actualizado_en=HOY)
    campos.setdefault("por_unidad", Presupuesto())
    return PresupuestoConfig(version=1, auditoria=auditoria, org=ORG, workspace=workspace, **campos)


def fila(unidad: str | None, fase: str, *, costo=0.0, tokens=0, en=HOY, ws=WS) -> TelemetriaNodo:
    return TelemetriaNodo(
        id=uuid.uuid4(),
        org=ORG,
        workspace=ws,
        unidad=unidad,
        conversacion=None if unidad else uuid.uuid4(),
        nodo="nodo",
        fase=fase,
        tokens_entrada=tokens,
        costo_usd=costo,
        duracion_ms=0,
        en=en,
    )


async def arrancar(motor, **extra):
    return (await motor.start(entrada_start(**extra), JULIAN)).estado.unidad


def alta_siempre(peticion):
    return SalidaCritico(hallazgos=[hallazgo_alto()])


# --- La función de comprobación -------------------------------------------------------------


def test_exceso_nombra_el_primer_tope_alcanzado():
    tope = Presupuesto(tokens_max=100, costo_usd_max=1.0, segundos_max=60)
    assert presupuesto.exceso(Consumo(tokens=99, costo_usd=0.99, segundos=59), tope) is None
    assert presupuesto.exceso(Consumo(tokens=100), tope) == "tokens 100/100"
    assert presupuesto.exceso(Consumo(costo_usd=1.0), tope) == "costo 1.00/1.00 USD"
    assert presupuesto.exceso(Consumo(segundos=61), tope) == "segundos 61/60"
    assert presupuesto.exceso(Consumo(tokens=10**9), Presupuesto()) is None


def test_por_fase_suma_la_telemetria_de_su_fase_y_su_unidad():
    async def caso():
        motor, _ = construir()
        almacen = motor.n.almacen
        alcance = await arrancar(motor)
        otra = await arrancar(motor)
        estado = almacen.obtener_estado(alcance)
        # El gate de código cuenta para la fase implement; otra unidad y otras fases no cuentan.
        almacen.registrar_telemetria(fila(alcance.unidad, "codigo", costo=0.30))
        almacen.registrar_telemetria(fila(alcance.unidad, "codigo", costo=0.25))
        almacen.registrar_telemetria(fila(alcance.unidad, "spec", costo=5.0))
        almacen.registrar_telemetria(fila(otra.unidad, "codigo", costo=5.0))
        almacen.guardar_configuracion([config(por_fase={Fase.implement: Presupuesto(costo_usd_max=0.55)})])

        def pregunta(gate, fase, gastado=presupuesto.SIN_GASTO):
            return presupuesto.agotado(almacen, HOY, estado, gate, fase, gastado)

        assert pregunta(GateFase.codigo, Fase.implement) == "por_fase implement: costo 0.55/0.55 USD"
        assert pregunta(GateFase.spec, Fase.spec) is None
        # Con lo que ya costó el panel en curso (aún sin registrar) el tope se alcanza antes.
        almacen.guardar_configuracion([config(por_fase={Fase.implement: Presupuesto(costo_usd_max=0.60)})])
        assert pregunta(GateFase.codigo, Fase.implement) is None
        assert pregunta(GateFase.codigo, Fase.implement, Uso(costo_usd=0.10)) == (
            "por_fase implement: costo 0.65/0.60 USD"
        )

    correr(caso())


def test_mensual_suma_el_mes_del_workspace_y_nada_mas():
    async def caso():
        motor, _ = construir()
        almacen = motor.n.almacen
        alcance = await arrancar(motor)
        estado = almacen.obtener_estado(alcance)
        almacen.registrar_telemetria(fila(alcance.unidad, "spec", costo=2.0))
        almacen.registrar_telemetria(fila(None, "chat", costo=1.0))  # el chat del workspace también gasta
        almacen.registrar_telemetria(fila(alcance.unidad, "plan", costo=9.0, en=HOY - timedelta(days=30)))
        almacen.registrar_telemetria(fila(alcance.unidad, "plan", costo=9.0, ws="otro-workspace"))
        almacen.guardar_configuracion([config(mensual_usd=3.5)])

        def pregunta(gastado=presupuesto.SIN_GASTO):
            return presupuesto.agotado(almacen, HOY, estado, GateFase.spec, Fase.spec, gastado)

        assert pregunta() is None
        assert pregunta(Uso(costo_usd=0.5)) == "mensual_usd 2026-09: costo 3.50/3.50 USD"
        # Sin configuración, o con una sin topes, no hay nada que agotar.
        almacen.guardar_configuracion([config()])
        assert pregunta(Uso(costo_usd=100.0)) is None

    correr(caso())


# --- El gate escala diciendo qué tope --------------------------------------------------------


def instalar_tope(alcance, motor, **topes):
    """``unidad`` va en el estado de la unidad; ``fase`` y ``mes``, en la configuración del workspace."""

    if "unidad" in topes:
        motor.n.escribir(alcance, lambda e: {"presupuesto": topes["unidad"]})
    if "fase" in topes:
        motor.n.almacen.guardar_configuracion([config(por_fase={Fase.spec: topes["fase"]})])
    if "mes" in topes:
        motor.n.almacen.guardar_configuracion([config(mensual_usd=topes["mes"])])


def test_por_fase_escala_al_empezar_la_siguiente_iteracion():
    async def caso():
        motor, proveedor = construir(alta_siempre)
        motor.n.almacen.guardar_configuracion([config(por_fase={Fase.spec: Presupuesto(tokens_max=2000)})])
        alcance = await arrancar(motor)
        av = await hasta(motor, alcance, es_checkpoint(TipoCheckpoint.gate_escalado))
        gate = motor.n.almacen.obtener_estado(alcance).gates[GateFase.spec]
        assert gate.causa == CausaEscalado.presupuesto_agotado
        assert "por_fase spec: tokens 2400/2000" in av.checkpoint.pregunta
        # Los dos críticos de la primera iteración; la segunda no llegó a llamar al modelo.
        assert len(proveedor.peticiones) == 2

    correr(caso())


def test_mensual_escala_y_dice_el_mes():
    async def caso():
        motor, proveedor = construir(alta_siempre)
        motor.n.almacen.guardar_configuracion([config(mensual_usd=0.02)])
        alcance = await arrancar(motor)
        av = await hasta(motor, alcance, es_checkpoint(TipoCheckpoint.gate_escalado))
        gate = motor.n.almacen.obtener_estado(alcance).gates[GateFase.spec]
        assert gate.causa == CausaEscalado.presupuesto_agotado
        assert "mensual_usd 2026-09: costo 0.02/0.02 USD" in av.checkpoint.pregunta
        assert len(proveedor.peticiones) == 2

    correr(caso())


def test_el_presupuesto_de_otra_fase_no_frena_este_gate():
    async def caso():
        motor, proveedor = construir()
        motor.n.almacen.guardar_configuracion([config(por_fase={Fase.plan: Presupuesto(tokens_max=1)})])
        alcance = await arrancar(motor)
        await hasta(motor, alcance, es_checkpoint(TipoCheckpoint.aprobar_spec))
        gate = motor.n.almacen.obtener_estado(alcance).gates[GateFase.spec]
        assert gate.veredicto == Veredicto.aprobado and proveedor.peticiones

    correr(caso())


@pytest.mark.parametrize(
    ("tope", "texto"),
    [
        ({"unidad": Presupuesto(tokens_max=3000)}, "por_unidad: tokens 3600/3000"),
        ({"fase": Presupuesto(tokens_max=3000)}, "por_fase spec: tokens 3600/3000"),
        ({"mes": 0.03}, "mensual_usd 2026-09: costo 0.03/0.03 USD"),
    ],
    ids=["unidad", "fase", "mes"],
)
def test_el_tope_frena_al_refutador_antes_de_su_llamada(tope, texto):
    """Riesgo alto: tres críticos y refutador. Tras los críticos el tope ya está alcanzado."""

    async def caso():
        motor, proveedor = construir(alta_siempre)
        alcance = await arrancar(motor, riesgo_sugerido=Riesgo.alto)
        instalar_tope(alcance, motor, **tope)
        av = await hasta(motor, alcance, es_checkpoint(TipoCheckpoint.gate_escalado))
        estado = motor.n.almacen.obtener_estado(alcance)
        gate = estado.gates[GateFase.spec]
        assert gate.causa == CausaEscalado.presupuesto_agotado and gate.iteraciones == 1
        assert f"presupuesto agotado ({texto})" in av.checkpoint.pregunta
        # Solo salieron los tres críticos, y lo que costaron quedó registrado y consumido.
        assert len(proveedor.peticiones) == 3
        assert motor.n.almacen.db.auditoria.count_documents({"evento": "llamada-modelo"}) == 3
        assert estado.consumo.tokens == 3600
        veredictos = {d["veredicto"] for d in motor.n.almacen.db.telemetria.find({})}
        assert veredictos == {"escalado"}

    correr(caso())


def test_la_guardia_frena_una_llamada_pero_no_una_respuesta_de_la_cache():
    async def caso():
        proveedor = ProveedorGuionado(lambda p: SalidaCritico(hallazgos=[]))
        eleccion = Eleccion(proveedor, "claude-opus-5-5")
        almacen = construir()[0].n.almacen
        proveedores = Proveedores({Proveedor.foundry: proveedor}, cache=CacheNodos(almacen))
        peticion = PeticionModelo(
            rol="critico-profundo",
            modelo="claude-opus-5-5",
            sistema="s",
            contenido="c",
            esquema=SalidaCritico,
        )
        agotada = Guardia(lambda gastado: "por_unidad: tokens 5/1")
        with pytest.raises(PresupuestoAgotado, match="por_unidad: tokens 5/1"):
            await _completar(proveedores, ORG, eleccion, peticion, "nodo", "sha", [], agotada)
        assert proveedor.peticiones == []
        # Con la respuesta ya guardada no hay llamada que frenar.
        libre = Guardia(lambda gastado: None)
        _, cacheada = await _completar(proveedores, ORG, eleccion, peticion, "nodo", "sha", [], libre)
        assert not cacheada and libre.gastado.tokens == 1200
        r, cacheada = await _completar(proveedores, ORG, eleccion, peticion, "nodo", "sha", [], agotada)
        assert cacheada and agotada.gastado.tokens == 0 and len(proveedor.peticiones) == 1

    correr(caso())


# --- Veredicto de la telemetría ----------------------------------------------------------------


def _veredictos(motor) -> list[str | None]:
    return [d.get("veredicto") for d in motor.n.almacen.db.telemetria.find({})]


def test_telemetria_aprobado_en_la_primera_iteracion():
    async def caso():
        motor, proveedor = construir()
        alcance = await arrancar(motor)
        await hasta(motor, alcance, es_checkpoint(TipoCheckpoint.aprobar_spec))
        assert len(proveedor.peticiones) == 2
        assert _veredictos(motor) == ["aprobado", "aprobado"]

    correr(caso())


def test_telemetria_refinado_si_el_gate_necesito_otra_iteracion():
    async def caso():
        llamadas = []

        def guion(peticion):
            llamadas.append(peticion)
            return SalidaCritico(hallazgos=[hallazgo_alto()] if len(llamadas) <= 2 else [])

        motor, _ = construir(guion)
        alcance = await arrancar(motor)
        await hasta(motor, alcance, es_checkpoint(TipoCheckpoint.aprobar_spec))
        gate = motor.n.almacen.obtener_estado(alcance).gates[GateFase.spec]
        assert gate.veredicto == Veredicto.refinado and gate.iteraciones == 2
        # Las dos iteraciones del gate: la que pidió refinar y la que aprobó después.
        assert _veredictos(motor) == ["refinado"] * 4

    correr(caso())


def test_telemetria_escalado_tambien_en_las_llamadas_fallidas():
    async def caso():
        motor, _ = construir(lambda p: ErrorProveedor("503 de Foundry"))
        alcance = await arrancar(motor)
        await hasta(motor, alcance, es_checkpoint(TipoCheckpoint.gate_escalado))
        filas = list(motor.n.almacen.db.telemetria.find({}))
        assert filas and {d["veredicto"] for d in filas} == {"escalado"}
        assert all(d["tokens_entrada"] == 0 for d in filas)

    correr(caso())
