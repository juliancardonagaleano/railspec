"""Gate, escalados, paradas y rechazos de reporte del motor."""

from __future__ import annotations

import asyncio
import os

import pytest
from apoyo_motor import (
    BASE,
    JULIAN,
    JULIAN_CONSOLA,
    ORG,
    PLAN,
    SPEC,
    WS,
    aprobar,
    avanzar,
    construir,
    critico_sin_hallazgos,
    entrada_start,
    reporte,
)
from railspec.contracts.comun import (
    CausaEscalado,
    EstadoFase,
    Fase,
    GateFase,
    GobernanzaConsultada,
    NivelCodigo,
    Presupuesto,
    Severidad,
    Veredicto,
)
from railspec.contracts.estado import Decision, TipoCheckpoint
from railspec.contracts.reporte import ResultadoOrden
from railspec.contracts.tools import CodigoError, RepositorioInicio, UnitIntegrateEntrada
from railspec.server.estado import CheckpointsMongo
from railspec.server.motor import ErrorNegocio, Motor
from railspec.server.motor.gate import HallazgoPropuesto, SalidaCritico
from railspec.server.motor.gobernanza import GobernanzaFija
from railspec.server.proveedores import ErrorProveedor


def correr(coro):
    return asyncio.run(coro)


async def iniciar(motor):
    return (await motor.start(entrada_start(), JULIAN)).estado.unidad


async def hasta(motor, alcance, parar, *, textos=None, decision=Decision.aprobado, maximo=30):
    """Recorre el protocolo reportando y aprobando hasta que ``parar(avance)`` sea cierto."""

    textos = textos or {}
    for _ in range(maximo):
        av = await avanzar(motor, alcance)
        if parar(av):
            return av
        if av.tipo == "orden":
            clave = (av.orden.tipo, getattr(av.orden, "artefacto", None))
            fn = textos.get(clave)
            await motor.report(fn(av.orden) if fn else reporte(av.orden), JULIAN)
        elif av.tipo == "checkpoint":
            await aprobar(motor, alcance, av.checkpoint.id, decision=decision)
        else:
            raise AssertionError(f"avance inesperado: {av}")
    raise AssertionError("no se alcanzó la condición")


def es_checkpoint(tipo):
    return lambda av: av.tipo == "checkpoint" and av.checkpoint.tipo == tipo


def es_orden(tipo, fase=None):
    return lambda av: av.tipo == "orden" and av.orden.tipo == tipo and (fase is None or av.orden.fase == fase)


def hallazgo_alto(lente="testeabilidad", titulo="CA-01 no es verificable"):
    return HallazgoPropuesto(
        lente=lente,
        severidad=Severidad.alta,
        criterio="CA-01",
        titulo=titulo,
        seccion="Criterios de aceptación",
        evidencia="«firma verificable» no dice cómo",
    )


def test_sin_gobernanza_escala_sin_gastar_tokens_y_se_rehabilita():
    async def caso():
        motor, proveedor = construir(gobernanza=GobernanzaFija(consultada=GobernanzaConsultada.no))
        alcance = await iniciar(motor)
        av = await hasta(motor, alcance, es_checkpoint(TipoCheckpoint.gate_escalado))
        estado = motor.n.almacen.obtener_estado(alcance)
        gate = estado.gates[GateFase.spec]
        assert gate.veredicto == Veredicto.escalado and gate.causa == CausaEscalado.sin_gobernanza
        assert estado.estado == EstadoFase.bloqueado and proveedor.peticiones == []
        await aprobar(motor, alcance, av.checkpoint.id, comentario="gobernanza vacía a propósito")
        estado = motor.n.almacen.obtener_estado(alcance)
        assert estado.gates[GateFase.spec].rehabilitado is not None
        # Interactivo: la rehabilitación avanza sin checkpoint de spec y pasa a redactar el plan.
        av = await avanzar(motor, alcance)
        assert av.tipo == "orden" and av.orden.artefacto.value == "plan"

    correr(caso())


def test_hallazgo_alto_refina_y_el_gate_queda_refinado():
    llamadas = {"n": 0}

    def guion(peticion):
        llamadas["n"] += 1
        if peticion.esquema is SalidaCritico and llamadas["n"] == 1:
            return SalidaCritico(hallazgos=[hallazgo_alto()])
        return critico_sin_hallazgos(peticion)

    async def caso():
        motor, _ = construir(guion)
        alcance = await iniciar(motor)
        av = await hasta(motor, alcance, es_orden("refinar"))
        assert av.orden.artefacto.value == "spec"
        assert [h.titulo for h in av.orden.hallazgos] == ["CA-01 no es verificable"]
        await motor.report(reporte(av.orden), JULIAN)
        av = await avanzar(motor, alcance)
        assert av.tipo == "checkpoint" and av.checkpoint.tipo == TipoCheckpoint.aprobar_spec
        gate = motor.n.almacen.obtener_estado(alcance).gates[GateFase.spec]
        assert gate.veredicto == Veredicto.refinado and gate.iteraciones == 2

    correr(caso())


def test_hallazgo_que_reaparece_escala():
    def guion(peticion):
        return SalidaCritico(hallazgos=[hallazgo_alto()])

    async def caso():
        motor, _ = construir(guion)
        alcance = await iniciar(motor)
        await hasta(motor, alcance, es_checkpoint(TipoCheckpoint.gate_escalado))
        gate = motor.n.almacen.obtener_estado(alcance).gates[GateFase.spec]
        assert gate.veredicto == Veredicto.escalado
        assert gate.causa == CausaEscalado.sin_convergencia and gate.hallazgos

    correr(caso())


def test_error_de_proveedor_escala():
    async def caso():
        motor, _ = construir(lambda p: ErrorProveedor("503 de Foundry"))
        alcance = await iniciar(motor)
        await hasta(motor, alcance, es_checkpoint(TipoCheckpoint.gate_escalado))
        gate = motor.n.almacen.obtener_estado(alcance).gates[GateFase.spec]
        assert gate.causa == CausaEscalado.error_proveedor

    correr(caso())


def test_presupuesto_agotado_escala_antes_de_llamar_al_modelo():
    async def caso():
        motor, proveedor = construir()
        alcance = await iniciar(motor)
        motor.n.escribir(alcance, lambda e: {"presupuesto": Presupuesto(tokens_max=1)})
        motor.n.escribir(alcance, lambda e: {"consumo": e.consumo.model_copy(update={"tokens": 5})})
        await hasta(motor, alcance, es_checkpoint(TipoCheckpoint.gate_escalado))
        gate = motor.n.almacen.obtener_estado(alcance).gates[GateFase.spec]
        assert gate.causa == CausaEscalado.presupuesto_agotado and proveedor.peticiones == []

    correr(caso())


def test_capa_determinista_refina_sin_modelo():
    async def caso():
        motor, proveedor = construir()
        alcance = await iniciar(motor)
        sin_criterios = SPEC.split("## Criterios de aceptación")[0]
        textos = {("redactar", None): None}
        av = await avanzar(motor, alcance)
        await motor.report(reporte(av.orden, texto=sin_criterios), JULIAN)
        av = await avanzar(motor, alcance)
        assert av.tipo == "orden" and av.orden.tipo == "refinar"
        assert all(h.lente == "estructura" for h in av.orden.hallazgos)
        assert proveedor.peticiones == [] and textos

    correr(caso())


def test_cambios_solicitados_reabren_el_refinamiento():
    async def caso():
        motor, _ = construir()
        alcance = await iniciar(motor)
        av = await hasta(motor, alcance, es_checkpoint(TipoCheckpoint.aprobar_spec))
        await aprobar(
            motor, alcance, av.checkpoint.id, Decision.cambios_solicitados, "Agrega CA de revocación"
        )
        av = await avanzar(motor, alcance)
        assert av.tipo == "orden" and av.orden.tipo == "refinar"
        assert av.orden.hallazgos[0].lente == "humano"
        assert "revocación" in av.orden.hallazgos[0].evidencia

    correr(caso())


def test_primera_resolucion_gana():
    async def caso():
        motor, _ = construir()
        alcance = await iniciar(motor)
        av = await hasta(motor, alcance, es_checkpoint(TipoCheckpoint.aprobar_spec))
        await aprobar(motor, alcance, av.checkpoint.id, actor=JULIAN_CONSOLA)
        with pytest.raises(ErrorNegocio) as exc:
            await aprobar(motor, alcance, av.checkpoint.id)
        assert exc.value.codigo == CodigoError.checkpoint_ya_resuelto
        estado = motor.n.almacen.obtener_estado(alcance)
        assert len(estado.resoluciones) == 1 and estado.resoluciones[0].actor.canal.value == "consola"

    correr(caso())


def test_parada_reintenta_con_orden_nueva_o_rechaza():
    async def caso():
        motor, _ = construir()
        alcance = await iniciar(motor)
        av = await avanzar(motor, alcance)
        primera = av.orden
        await motor.report(reporte(primera, resultado=ResultadoOrden.bloqueado, motivo="sin acceso"), JULIAN)
        av = await avanzar(motor, alcance)
        assert av.tipo == "checkpoint" and av.checkpoint.tipo == TipoCheckpoint.parada
        await aprobar(motor, alcance, av.checkpoint.id, Decision.cambios_solicitados, "usa el mirror")
        av = await avanzar(motor, alcance)
        assert (
            av.tipo == "orden" and av.orden.id != primera.id and av.orden.secuencia == primera.secuencia + 1
        )
        assert "usa el mirror" in av.orden.instrucciones
        await motor.report(reporte(av.orden, resultado=ResultadoOrden.fallido), JULIAN)
        av = await avanzar(motor, alcance)
        await aprobar(motor, alcance, av.checkpoint.id, Decision.rechazado, "abandonar")
        av = await avanzar(motor, alcance)
        assert av.tipo == "en-espera"
        assert motor.n.almacen.obtener_estado(alcance).estado == EstadoFase.bloqueado

    correr(caso())


def test_rechazos_de_reporte():
    async def caso():
        motor, _ = construir(nivel=NivelCodigo.interno)
        alcance = await iniciar(motor)
        av = await avanzar(motor, alcance)
        orden = av.orden
        with pytest.raises(ErrorNegocio) as exc:
            await motor.report(reporte(orden).model_copy(update={"base_commit": "f" * 40}), JULIAN)
        assert exc.value.codigo == CodigoError.base_commit_distinto
        with pytest.raises(ErrorNegocio) as exc:
            await motor.report(reporte(orden).model_copy(update={"secuencia": 99}), JULIAN)
        assert exc.value.codigo == CodigoError.orden_no_vigente
        await motor.report(reporte(orden), JULIAN)
        # Reenvío del mismo reporte (el proxy perdió la respuesta): se reconoce como ya aceptado.
        with pytest.raises(ErrorNegocio) as exc:
            await motor.report(reporte(orden), JULIAN)
        assert exc.value.codigo == CodigoError.secuencia_duplicada
        # Una orden que nunca se reportó y ya no está vigente sigue siendo orden-no-vigente.
        with pytest.raises(ErrorNegocio) as exc:
            await motor.report(reporte(orden).model_copy(update={"secuencia": orden.secuencia + 50}), JULIAN)
        assert exc.value.codigo == CodigoError.orden_no_vigente

        av = await hasta(motor, alcance, es_orden("implementar"))
        # El vínculo fija nivel interno: un snapshot restringido se rechaza.
        with pytest.raises(ErrorNegocio) as exc:
            await motor.report(reporte(av.orden), JULIAN)
        assert exc.value.codigo == CodigoError.snapshot_invalido
        sin_snapshot = reporte(av.orden).model_copy(update={"snapshot": None})
        with pytest.raises(ErrorNegocio):
            await motor.report(sin_snapshot, JULIAN)

    correr(caso())


def _snapshot_interno(orden, rutas):
    from apoyo_motor import snapshot

    r = reporte(orden)
    return r.model_copy(update={"snapshot": snapshot(orden, NivelCodigo.restringido, rutas)})


def test_gate_de_codigo_archivo_fuera_del_plan_y_validacion_fallida():
    async def caso():
        motor, proveedor = construir()
        alcance = await iniciar(motor)
        av = await hasta(motor, alcance, es_orden("implementar"))
        await motor.report(reporte(av.orden, rutas=("src/pdf.py", "infra/deploy.yaml")), JULIAN)
        av = await avanzar(motor, alcance)
        assert av.orden.tipo == "validar"
        llamadas_antes = len(proveedor.peticiones)
        await motor.report(reporte(av.orden, codigo_salida=1), JULIAN)
        av = await avanzar(motor, alcance)
        assert av.tipo == "orden" and av.orden.tipo == "implementar"
        titulos = {h.titulo for h in av.orden.contexto.hallazgos_previos}
        assert "archivo fuera del plan: infra/deploy.yaml" in titulos
        assert any(t.startswith("la validación falla") for t in titulos)
        assert len(proveedor.peticiones) == llamadas_antes  # capa determinista: sin tokens

    correr(caso())


def test_modo_segundo_plano_y_esperar():
    async def caso():
        motor, _ = construir(en_linea=False)
        alcance = await iniciar(motor)
        await motor.esperar()
        av = await avanzar(motor, alcance)
        assert av.tipo == "orden"
        await motor.report(reporte(av.orden), JULIAN)
        await motor.esperar()
        av = await avanzar(motor, alcance)
        assert av.tipo == "checkpoint" and av.checkpoint.tipo == TipoCheckpoint.aprobar_spec

    correr(caso())


def test_otra_replica_reanuda_desde_los_checkpoints():
    async def caso():
        motor, _ = construir()
        alcance = await iniciar(motor)
        av = await avanzar(motor, alcance)
        # Otra instancia del servidor, mismo Mongo: reanuda el DAG desde el checkpoint de MAF.
        otra = Motor(motor.n, CheckpointsMongo(motor.n.almacen.db), en_linea=True)
        await otra.report(reporte(av.orden), JULIAN)
        av = await avanzar(otra, alcance)
        assert av.tipo == "checkpoint" and av.checkpoint.tipo == TipoCheckpoint.aprobar_spec
        await aprobar(otra, alcance, av.checkpoint.id)
        av = await avanzar(motor, alcance)
        assert av.tipo == "orden" and av.orden.artefacto.value == "plan"

    correr(caso())


def test_entrada_pendiente_sin_procesar_se_retoma():
    """Si la réplica cae tras registrar el reporte, el siguiente ``advance`` lo procesa."""

    async def caso():
        motor, _ = construir()
        alcance = await iniciar(motor)
        av = await avanzar(motor, alcance)
        motor.en_linea = False
        original = motor._reanudar

        async def caida(_):
            return None

        motor._reanudar = caida
        await motor.report(reporte(av.orden), JULIAN)
        assert motor.n.almacen.entradas_pendientes(alcance)
        motor._reanudar = original
        motor.en_linea = True
        av = await avanzar(motor, alcance)
        assert av.tipo == "checkpoint"
        assert not motor.n.almacen.entradas_pendientes(alcance)

    correr(caso())


def test_impacto_de_grafo_llega_al_panel_de_codigo():
    from railspec.contracts.referencias import RefArchivo
    from railspec.contracts.snapshot import Relacion
    from railspec.contracts.tools import ResultadoGrafo

    class GrafoFalso:
        def __init__(self):
            self.deltas = []

        def aplicar_delta(self, *a):
            self.deltas.append(a)

        def impacto_superposicion(self, consulta, visibles):
            assert consulta.unidad and visibles[0].repositorio == "certificados-api"
            ref = RefArchivo(
                repositorio="certificados-api", commit="4063ae9" + "0" * 33, ruta="src/emision.py"
            )
            return ["s1"], [ResultadoGrafo(ref=ref, relacion=Relacion.llama, distancia=1, riesgo="medio")]

    async def caso():
        motor, proveedor = construir()
        motor.n.grafo = GrafoFalso()
        alcance = await iniciar(motor)
        await hasta(motor, alcance, lambda av: av.tipo == "cerrada")
        codigo = [p for p in proveedor.peticiones if "Impacto en el grafo" in p.contenido]
        assert codigo and "src/emision.py" in codigo[0].contenido

    correr(caso())


def test_grafo_real_traza_criterios_informa_al_gate_y_se_descarta_al_integrar():
    """Fase 5 con railspec-graph de verdad (motor en memoria): el snapshot de
    implementar crea la superposición y enlaza los CA-NN de sus tareas; el
    panel de código recibe impacto y trazas; integrar descarta la superposición."""

    from railspec.contracts.comun import AlcanceRepositorio, NivelCodigo
    from railspec.contracts.snapshot import DeltaIndice, ModoDelta, MotorIndice, Simbolo
    from railspec.contracts.tools import UnitIntegrateEntrada
    from railspec.graph import AccesoGrafo, AlmacenGrafo, MotorMemoria, Traza

    firma = Simbolo(
        id="a" * 64,
        nombre="pdf.firmar",
        tipo="funcion",
        ruta="src/pdf.py",
        linea_inicio=1,
        linea_fin=9,
        sha256="b" * 64,
    )

    def implementar(orden):
        r = reporte(orden)
        delta = DeltaIndice(motor=MotorIndice(version="0.11.0"), simbolos_upsert=[firma])
        snap = r.snapshot.model_copy(update={"modo_delta": ModoDelta.completo, "delta_indice": delta})
        return r.model_copy(update={"snapshot": snap})

    async def caso():
        motor, proveedor = construir(nivel=NivelCodigo.restringido)
        acceso = AccesoGrafo(MotorMemoria())
        motor.n.grafo = AlmacenGrafo(acceso)
        alcance = await iniciar(motor)
        await hasta(
            motor, alcance, lambda av: av.tipo == "cerrada", textos={("implementar", None): implementar}
        )
        repo = AlcanceRepositorio(
            org=alcance.org, workspace=alcance.workspace, repositorio="certificados-api"
        )
        assert motor.n.grafo.trazas(repo, alcance.unidad) == [
            Traza(alcance.unidad, "CA-01", firma.id),
            Traza(alcance.unidad, "CA-02", firma.id),
        ]
        codigo = [p.contenido for p in proveedor.peticiones if "Trazas CA-NN" in p.contenido]
        assert codigo and "CA-01: 1 símbolos" in codigo[0] and "Impacto en el grafo" in codigo[0]
        assert acceso.superposiciones(repo) == [alcance.unidad]

        await motor.integrate(
            UnitIntegrateEntrada(unidad=alcance, especificacion_viva="specs/pdf.md"), JULIAN
        )
        assert acceso.superposiciones(repo) == []
        assert motor.n.grafo.trazas(repo, alcance.unidad)

    correr(caso())


COMMIT_A, COMMIT_B = "a" * 40, "b" * 40


@pytest.fixture(params=["memoria"] + (["falkordb"] if os.environ.get("RAILSPEC_FALKORDB_URL") else []))
def motor_grafo(request):
    """El motor del grafo: el doble en memoria y, con ``RAILSPEC_FALKORDB_URL``, FalkorDB real."""

    from railspec.graph import MotorMemoria

    if request.param == "memoria":
        yield MotorMemoria()
        return
    from railspec.graph.motor_falkordb import MotorFalkor

    motor = MotorFalkor.desde_url(os.environ["RAILSPEC_FALKORDB_URL"])
    prefijo = f"railspec:{ORG}:{WS}:"

    def limpiar():
        for g in motor.listar(prefijo):
            motor.borrar(g)

    limpiar()
    yield motor
    limpiar()


def test_integrar_con_commit_retiene_la_superposicion_hasta_que_ci_indexa_ese_commit(motor_grafo):
    """Extremo a extremo con railspec-graph de verdad: unit.integrate con ``commit_integrado``
    deja el código de la unidad visible a sus consultas hasta que ``graph.index`` lleva el
    canónico a ese commit, y entonces la superposición se retira."""

    from railspec.contracts.comun import AlcanceRepositorio
    from railspec.contracts.snapshot import DeltaIndice, ModoDelta, MotorIndice, Simbolo
    from railspec.contracts.tools import GraphIndexEntrada, GraphQueryEntrada
    from railspec.graph import AccesoGrafo, AlmacenGrafo, IndexadorCanonico

    firma = Simbolo(
        id="a" * 64,
        nombre="pdf.firmar",
        tipo="funcion",
        ruta="src/pdf.py",
        linea_inicio=1,
        linea_fin=9,
        sha256="b" * 64,
    )
    base = Simbolo(
        id="c" * 64,
        nombre="pdf.render",
        tipo="funcion",
        ruta="src/pdf.py",
        linea_inicio=10,
        linea_fin=19,
        sha256="d" * 64,
    )
    motor_indice = MotorIndice(version="0.11.0")

    def implementar(orden):
        r = reporte(orden)
        delta = DeltaIndice(motor=motor_indice, simbolos_upsert=[firma])
        snap = r.snapshot.model_copy(update={"modo_delta": ModoDelta.completo, "delta_indice": delta})
        return r.model_copy(update={"snapshot": snap})

    def visibles(grafo, repo, unidad=None):
        q = GraphQueryEntrada.model_validate(
            {
                "alcance": {"org": repo.org, "workspace": repo.workspace},
                "unidad": unidad,
                "consulta": {"verbo": "search", "texto": "pdf"},
                "limite": 50,
            }
        )
        return {r.ref.nombre for r in grafo.consultar(q, [repo]).resultados}

    def indice(repo, commit, simbolos, anterior=None):
        return GraphIndexEntrada(
            alcance=repo,
            rama="main",
            commit=commit,
            commit_anterior=anterior,
            lote=1,
            lotes=1,
            delta=DeltaIndice(motor=motor_indice, simbolos_upsert=simbolos),
        )

    async def caso():
        motor, _ = construir(nivel=NivelCodigo.restringido)
        acceso = AccesoGrafo(motor_grafo)
        grafo = AlmacenGrafo(acceso)
        motor.n.grafo = grafo
        alcance = await iniciar(motor)
        repo = AlcanceRepositorio(
            org=alcance.org, workspace=alcance.workspace, repositorio="certificados-api"
        )
        vinculo = motor.n.almacen.vinculo(repo)
        indexador = IndexadorCanonico(acceso, grafo)
        indexador.recibir(indice(repo, COMMIT_A, [base]), vinculo)

        await hasta(
            motor, alcance, lambda av: av.tipo == "cerrada", textos={("implementar", None): implementar}
        )
        assert acceso.superposiciones(repo) == [alcance.unidad]
        assert visibles(grafo, repo, alcance.unidad) == {"pdf.render", "pdf.firmar"}

        await motor.integrate(
            UnitIntegrateEntrada(
                unidad=alcance, especificacion_viva="specs/pdf.md", commit_integrado=COMMIT_B
            ),
            JULIAN,
        )
        # Integrada, pero CI aún no indexó COMMIT_B: la unidad conserva su código; el resto, no lo ve.
        assert acceso.superposiciones(repo) == [alcance.unidad]
        assert visibles(grafo, repo, alcance.unidad) == {"pdf.render", "pdf.firmar"}
        assert visibles(grafo, repo) == {"pdf.render"}

        indexador.recibir(indice(repo, COMMIT_B, [firma], anterior=COMMIT_A), vinculo)
        assert acceso.superposiciones(repo) == []
        assert visibles(grafo, repo, alcance.unidad) == {"pdf.render", "pdf.firmar"}  # ahora del canónico
        assert visibles(grafo, repo) == {"pdf.render", "pdf.firmar"}
        assert grafo.trazas(repo, alcance.unidad)  # las trazas CA-NN no dependen de la superposición

    correr(caso())


def test_integrar_reparte_retener_y_descartar_segun_commit_y_rol():
    """El contrato trae un solo commit: lo retiene el repositorio primario; los transversales, y
    todos si no hay commit, se descartan al integrar."""

    class GrafoFalso:
        def __init__(self):
            self.llamadas = []

        def aplicar_delta(self, *a):
            pass

        def retener_superposicion(self, alcance, unidad, integrado):
            self.llamadas.append(("retener", alcance.repositorio, integrado))

        def descartar_superposicion(self, alcance, unidad, hasta):
            self.llamadas.append(("descartar", alcance.repositorio))

    async def caso(commit):
        motor, _ = construir()
        motor.n.grafo = GrafoFalso()
        entrada = entrada_start(
            repositorios=[
                RepositorioInicio(repositorio="certificados-api", rama="main", base_commit=BASE),
                RepositorioInicio(repositorio="reporteria", rama="main", base_commit=BASE),
            ]
        )
        alcance = (await motor.start(entrada, JULIAN)).estado.unidad
        await hasta(motor, alcance, lambda av: av.tipo == "cerrada")
        await motor.integrate(
            UnitIntegrateEntrada(unidad=alcance, especificacion_viva="specs/pdf.md", commit_integrado=commit),
            JULIAN,
        )
        return motor.n.grafo.llamadas

    assert correr(caso(COMMIT_B)) == [("retener", "certificados-api", COMMIT_B), ("descartar", "reporteria")]
    assert correr(caso(None)) == [("descartar", "certificados-api"), ("descartar", "reporteria")]


def test_plan_invalido_no_se_acepta():
    async def caso():
        motor, _ = construir()
        alcance = await iniciar(motor)
        await hasta(motor, alcance, es_orden("redactar", Fase.plan))
        av = await avanzar(motor, alcance)
        sin_validacion = PLAN.split("## Validación")[0]
        await motor.report(reporte(av.orden, texto=sin_validacion), JULIAN)
        av = await avanzar(motor, alcance)
        assert av.tipo == "orden" and av.orden.tipo == "refinar" and av.orden.artefacto.value == "plan"

    correr(caso())


def test_modo_inicial_y_pedido_quedan_en_el_estado():
    async def caso():
        from railspec.contracts.comun import Modo

        motor, _ = construir()
        salida = await motor.start(entrada_start(modo=Modo.semi_autonomo), JULIAN)
        estado = salida.estado
        assert estado.modo == Modo.semi_autonomo and estado.pedido == "Los certificados deben salir firmados."
        assert [(c.de, c.a, c.tras) for c in estado.modo_conversion] == [
            (Modo.interactivo, Modo.semi_autonomo, None)
        ]
        tipos = []
        await hasta(
            motor,
            estado.unidad,
            lambda av: (
                av.tipo == "cerrada"
                or (av.tipo == "checkpoint" and tipos.append(av.checkpoint.tipo) and False)
            ),
        )
        assert tipos == [TipoCheckpoint.paquete_aprobacion]

    correr(caso())


def test_set_mode_solo_tras_el_checkpoint_del_spec():
    from railspec.contracts.comun import Modo
    from railspec.contracts.tools import UnitSetModeEntrada

    def entrada(motor, alcance, modo=Modo.semi_autonomo, version=None):
        v = version or motor.n.almacen.obtener_estado(alcance).version
        return UnitSetModeEntrada(unidad=alcance, modo=modo, motivo="spec claro", version_vista=v)

    async def caso():
        motor, _ = construir()
        alcance = await iniciar(motor)
        with pytest.raises(ErrorNegocio) as exc:
            await motor.set_mode(entrada(motor, alcance), JULIAN)
        assert exc.value.codigo == CodigoError.conversion_no_permitida
        av = await hasta(motor, alcance, es_checkpoint(TipoCheckpoint.aprobar_spec))
        with pytest.raises(ErrorNegocio) as exc:
            await motor.set_mode(entrada(motor, alcance, version=1), JULIAN)
        assert exc.value.codigo == CodigoError.conflicto_version
        estado = (await motor.set_mode(entrada(motor, alcance), JULIAN)).estado
        assert estado.modo == Modo.semi_autonomo and estado.modo_conversion[-1].tras == Fase.spec
        await aprobar(motor, alcance, av.checkpoint.id)
        # Semi-autónomo desde aquí: sin checkpoint de plan, un paquete de aprobación tras las tareas.
        av = await hasta(motor, alcance, lambda a: a.tipo == "checkpoint")
        assert av.checkpoint.tipo == TipoCheckpoint.paquete_aprobacion
        with pytest.raises(ErrorNegocio) as exc:
            await motor.set_mode(entrada(motor, alcance, Modo.interactivo), JULIAN)
        assert exc.value.codigo == CodigoError.conversion_no_permitida

    correr(caso())
