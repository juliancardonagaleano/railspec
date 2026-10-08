"""Mandato de los modos supervisado y desatendido (contrato 1.11): ciclo de vida, retención, paradas."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from apoyo_motor import (
    JULIAN,
    JULIAN_CONSOLA,
    PLAN_PDF,
    WS_ALCANCE,
    aprobar,
    aprobar_mandato,
    avanzar,
    contenido_mandato,
    entrada_mandato,
    entrada_start,
    mandato_aprobado,
    proponer_mandato,
    reporte,
)
from apoyo_motor import (
    construir as _construir,
)
from railspec.contracts.comun import (
    Actor,
    Canal,
    GateFase,
    GobernanzaConsultada,
    Modo,
    NivelCodigo,
    Presupuesto,
    Severidad,
    TipoActor,
)
from railspec.contracts.estado import Decision, TipoCheckpoint
from railspec.contracts.mandato import (
    CausaParada,
    DecisionPropuesta,
    EstadoMandato,
    ResultadoRevision,
)
from railspec.contracts.reporte import ResultadoOrden
from railspec.contracts.tools import (
    CodigoError,
    MandateApproveEntrada,
    MandateGetEntrada,
    MandateListEntrada,
    MandateReviewEntrada,
    MandateRevokeEntrada,
    Superficie,
    UnitSetModeEntrada,
)
from railspec.server.api import AutorizadorRoles, Registro
from railspec.server.motor import ErrorNegocio
from railspec.server.motor.gate import HallazgoPropuesto, SalidaCritico
from railspec.server.motor.gobernanza import GobernanzaFija

AGENTE = Actor(tipo=TipoActor.agente, canal=Canal.consola, agente="redactor", en_nombre_de=83125327)


def construir(*args, **kw):
    """El motor de pruebas con el repositorio vinculado: un mandato solo ampara repositorios vinculados."""

    kw.setdefault("nivel", NivelCodigo.restringido)
    return _construir(*args, **kw)


def correr(coro):
    return asyncio.run(coro)


async def iniciar(motor, modo=Modo.supervisado, plan=PLAN_PDF, **extra):
    return (await motor.start(entrada_mandato(modo, plan, **extra), JULIAN)).estado.unidad


def estado(motor, alcance):
    return motor.n.almacen.obtener_estado(alcance)


def mandato(motor, id_=PLAN_PDF):
    return motor.n.almacen.obtener_mandato(WS_ALCANCE, id_)


async def reportar_siguiente(motor, alcance, **kw):
    av = await avanzar(motor, alcance)
    assert av.tipo == "orden", av
    await motor.report(reporte(av.orden, **kw), JULIAN)
    return av.orden


def auditadas(motor, evento):
    return [d["detalle"] for d in motor.n.almacen.db.auditoria.find({"evento": evento})]


def hallazgo_alto():
    return HallazgoPropuesto(
        lente="testeabilidad",
        severidad=Severidad.alta,
        criterio="CA-01",
        titulo="CA-01 no es verificable",
        seccion="Criterios de aceptación",
        evidencia="«firma verificable» no dice cómo",
    )


# --- ciclo de vida del mandato ----------------------------------------------------------------------


def test_redactar_aprobar_editar_y_revocar():
    async def caso():
        motor, _ = construir()
        creado = await proponer_mandato(motor)
        m = creado.mandato
        assert m.estado == EstadoMandato.propuesto and m.version == 1 and not m.aprobaciones
        assert creado.huella == m.contenido.huella()
        assert not m.vigente(motor.n.reloj())

        aprobado = (await aprobar_mandato(motor, comentario="de acuerdo")).mandato
        assert aprobado.estado == EstadoMandato.aprobado and aprobado.version == 2
        ap = aprobado.aprobaciones[-1]
        assert ap.huella == m.contenido.huella() and ap.comentario == "de acuerdo"
        assert ap.caduca_en - ap.en == timedelta(hours=24)
        assert aprobado.vigente(motor.n.reloj())

        # Sin cambios no pierde su aprobación; con cambios vuelve a propuesto y la historia se conserva.
        editado = await motor.n.mandatos.propose(
            _propose(contenido_mandato(max_unidades=9), version_vista=aprobado.version), AGENTE
        )
        assert editado.mandato.estado == EstadoMandato.propuesto
        assert len(editado.mandato.aprobaciones) == 1 and not editado.mandato.vigente(motor.n.reloj())
        sin_cambios = await motor.n.mandatos.propose(
            _propose(editado.mandato.contenido, version_vista=editado.mandato.version), AGENTE
        )
        assert sin_cambios.mandato.version == editado.mandato.version

        rev = await motor.n.mandatos.revoke(
            MandateRevokeEntrada(
                alcance=WS_ALCANCE, id=PLAN_PDF, version_vista=sin_cambios.mandato.version, motivo="ya no"
            ),
            JULIAN,
        )
        assert rev.mandato.estado == EstadoMandato.revocado and rev.mandato.revocacion.motivo == "ya no"
        with pytest.raises(ErrorNegocio) as exc:
            await motor.n.mandatos.propose(
                _propose(contenido_mandato(), version_vista=rev.mandato.version), AGENTE
            )
        assert exc.value.codigo == CodigoError.conversion_no_permitida
        with pytest.raises(ErrorNegocio) as exc:
            await aprobar_mandato(motor)
        assert exc.value.codigo == CodigoError.mandato_no_vigente

        acciones = [d["accion"] for d in auditadas(motor, "cambio-mandato")]
        assert acciones == ["propuesto", "aprobado", "propuesto", "revocado"]

    correr(caso())


def _propose(contenido, version_vista=None, id_=PLAN_PDF):
    from railspec.contracts.tools import MandateProposeEntrada

    return MandateProposeEntrada(alcance=WS_ALCANCE, id=id_, contenido=contenido, version_vista=version_vista)


def test_aprobar_exige_persona_consola_y_la_huella_que_vio():
    async def caso():
        motor, _ = construir()
        m = (await proponer_mandato(motor)).mandato
        entrada = MandateApproveEntrada(
            alcance=WS_ALCANCE, id=PLAN_PDF, version_vista=m.version, huella=m.contenido.huella()
        )
        # Ni el arnés, ni un agente, aunque actúe en nombre de una persona, aprueban.
        for actor in (JULIAN, AGENTE):
            with pytest.raises(ErrorNegocio) as exc:
                await motor.n.mandatos.approve(entrada, actor)
            assert exc.value.codigo == CodigoError.fuera_de_alcance
        # Una huella que no es la del contenido vigente se rechaza.
        with pytest.raises(ErrorNegocio) as exc:
            await motor.n.mandatos.approve(entrada.model_copy(update={"huella": "0" * 64}), JULIAN_CONSOLA)
        assert exc.value.codigo == CodigoError.conflicto_version
        # Ni una versión vieja.
        with pytest.raises(ErrorNegocio) as exc:
            await motor.n.mandatos.approve(entrada.model_copy(update={"version_vista": 5}), JULIAN_CONSOLA)
        assert exc.value.codigo == CodigoError.conflicto_version
        assert mandato(motor).estado == EstadoMandato.propuesto

    correr(caso())


def test_crear_dos_veces_o_con_un_repositorio_sin_vincular_se_rechaza():
    async def caso():
        motor, _ = construir()
        await proponer_mandato(motor)
        with pytest.raises(ErrorNegocio) as exc:
            await proponer_mandato(motor)
        assert exc.value.codigo == CodigoError.conflicto_version
        with pytest.raises(ErrorNegocio) as exc:
            await proponer_mandato(motor, id_="otro", repositorios=["no-vinculado"])
        assert exc.value.codigo == CodigoError.fuera_de_alcance

    correr(caso())


def test_las_tools_de_aprobar_y_revisar_solo_viajan_por_http_y_con_una_persona():
    async def caso():
        motor, _ = construir()
        registro = Registro.del_motor(motor, AutorizadorRoles(motor.n.almacen, abierto=True))
        m = (await proponer_mandato(motor)).mandato
        cuerpo = MandateApproveEntrada(
            alcance=WS_ALCANCE, id=PLAN_PDF, version_vista=m.version, huella=m.contenido.huella()
        ).model_dump(mode="json")
        r = await registro.invocar("mandate.approve", cuerpo, JULIAN_CONSOLA, Superficie.mcp)
        assert not r.ok and r.cuerpo["codigo"] == "fuera-de-alcance"
        r = await registro.invocar("mandate.approve", cuerpo, JULIAN, Superficie.http)
        assert not r.ok and r.estado_http == 403
        assert {t.nombre for t in registro.tools(Superficie.mcp)} >= {
            "mandate.propose",
            "mandate.revoke",
            "mandate.get",
            "mandate.list",
        }
        assert not {"mandate.approve", "mandate.review"} & {t.nombre for t in registro.tools(Superficie.mcp)}
        r = await registro.invocar("mandate.approve", cuerpo, JULIAN_CONSOLA, Superficie.http)
        assert r.ok and r.cuerpo["mandato"]["estado"] == "aprobado" and r.cuerpo["huella"] == cuerpo["huella"]
        # Sin mandato aprobado, unit.start en un modo con mandato es un conflicto, no un 500.
        r = await registro.invocar(
            "unit.start", entrada_mandato(plan="no-existe").model_dump(mode="json"), JULIAN, Superficie.mcp
        )
        assert not r.ok and r.estado_http == 409 and r.cuerpo["codigo"] == "mandato-no-vigente"

    correr(caso())


# --- arrancar y convertir -----------------------------------------------------------------------------


def test_unit_start_exige_un_mandato_vigente_del_mismo_modo_y_con_cupo():
    async def caso():
        motor, _ = construir()
        with pytest.raises(ErrorNegocio) as exc:
            await iniciar(motor)
        assert exc.value.codigo == CodigoError.mandato_no_vigente and "no existe" in exc.value.detalle

        await proponer_mandato(motor, max_unidades=1)
        with pytest.raises(ErrorNegocio) as exc:  # redactado, no aprobado
            await iniciar(motor)
        assert exc.value.codigo == CodigoError.mandato_no_vigente
        await aprobar_mandato(motor)

        with pytest.raises(ErrorNegocio) as exc:  # el mandato ampara supervisado
            await iniciar(motor, modo=Modo.desatendido)
        assert exc.value.codigo == CodigoError.fuera_de_alcance

        alcance = await iniciar(motor)
        assert estado(motor, alcance).modo == Modo.supervisado and alcance.plan == PLAN_PDF
        with pytest.raises(ErrorNegocio) as exc:
            await iniciar(motor, titulo="Otra unidad")
        assert exc.value.codigo == CodigoError.fuera_de_alcance and "a lo sumo 1" in exc.value.detalle

        # Una unidad interactiva con el mismo plan no pasa por el mandato ni gasta su cupo.
        interactiva = (await motor.start(entrada_start(plan=PLAN_PDF, titulo="Manual"), JULIAN)).estado
        assert interactiva.modo == Modo.interactivo

    correr(caso())


def test_un_mandato_caducado_o_con_repositorios_ajenos_no_ampara_el_arranque():
    async def caso():
        motor, _ = construir()
        await mandato_aprobado(motor)
        motor.n.reloj.t += timedelta(hours=25)
        with pytest.raises(ErrorNegocio) as exc:
            await iniciar(motor)
        assert exc.value.codigo == CodigoError.mandato_no_vigente and "caducó" in exc.value.detalle
        assert mandato(motor).parada.causa == CausaParada.mandato_caducado

    correr(caso())


def test_las_ordenes_llevan_el_mandato_y_las_de_una_unidad_interactiva_no():
    async def caso():
        motor, _ = construir()
        await mandato_aprobado(motor, reintentos_parada=2, rutas_permitidas=["src/*"])
        alcance = await iniciar(motor)
        av = await avanzar(motor, alcance)
        m = av.orden.mandato
        assert m.mandato == PLAN_PDF and m.modo == Modo.supervisado and m.reintentos_parada == 2
        assert [d.id for d in m.delegaciones] == ["D-1", "D-2", "D-3"] and m.rutas_permitidas == ["src/*"]
        assert m.caduca_en == mandato(motor).vigente_hasta

        manual = (await motor.start(entrada_start(titulo="Manual"), JULIAN)).estado.unidad
        assert (await avanzar(motor, manual)).orden.mandato is None

    correr(caso())


def test_supervisado_corre_el_protocolo_completo_sin_checkpoints_humanos():
    async def caso():
        motor, _ = construir()
        await mandato_aprobado(motor)
        alcance = await iniciar(motor)
        tipos = []
        for _ in range(20):
            av = await avanzar(motor, alcance)
            tipos.append(av.tipo)
            if av.tipo == "cerrada":
                break
            assert av.tipo == "orden", av
            await motor.report(reporte(av.orden), JULIAN)
        assert tipos[-1] == "cerrada" and "checkpoint" not in tipos
        e = estado(motor, alcance)
        assert e.gates[GateFase.codigo].superado
        assert e.consumo.llamadas > 0  # el tope llamadas_max cuenta lo que sale hacia el proveedor

    correr(caso())


def test_las_llamadas_al_modelo_se_cuentan_y_el_tope_de_la_unidad_las_aplica():
    async def caso():
        motor, proveedor = construir()
        alcance = (await motor.start(entrada_start(), JULIAN)).estado.unidad
        await reportar_siguiente(motor, alcance)
        e = estado(motor, alcance)
        assert e.consumo.llamadas == len(proveedor.peticiones) > 0

        motor.n.escribir(alcance, lambda x: {"presupuesto": Presupuesto(llamadas_max=e.consumo.llamadas)})
        av = await avanzar(motor, alcance)
        assert av.tipo == "checkpoint"  # el spec ya está aprobado en interactivo
        await aprobar(motor, alcance, av.checkpoint.id)
        av = await avanzar(motor, alcance)
        await motor.report(reporte(av.orden), JULIAN)
        av = await avanzar(motor, alcance)
        assert av.tipo == "checkpoint" and av.checkpoint.tipo == TipoCheckpoint.gate_escalado
        assert "llamadas" in av.checkpoint.pregunta

    correr(caso())


def test_convertir_a_un_modo_con_mandato_exige_el_mandato_y_el_modo_correctos():
    async def caso():
        motor, _ = construir()
        alcance = (await motor.start(entrada_start(plan=PLAN_PDF), JULIAN)).estado.unidad
        await reportar_siguiente(motor, alcance)
        av = await avanzar(motor, alcance)
        assert av.checkpoint.tipo == TipoCheckpoint.aprobar_spec

        def convertir(modo):
            e = estado(motor, alcance)
            return motor.set_mode(
                UnitSetModeEntrada(unidad=e.unidad, modo=modo, motivo="a mandato", version_vista=e.version),
                JULIAN,
            )

        with pytest.raises(ErrorNegocio) as exc:
            await convertir(Modo.supervisado)
        assert exc.value.codigo == CodigoError.mandato_no_vigente
        await mandato_aprobado(motor)
        with pytest.raises(ErrorNegocio) as exc:
            await convertir(Modo.desatendido)
        assert exc.value.codigo == CodigoError.fuera_de_alcance
        convertido = (await convertir(Modo.supervisado)).estado
        assert convertido.modo == Modo.supervisado and convertido.modo_conversion[-1].tras is not None

    correr(caso())


# --- retención ------------------------------------------------------------------------------------------


def test_caducado_el_mandato_retiene_la_unidad_y_renovarlo_la_reanuda():
    async def caso():
        motor, _ = construir()
        await mandato_aprobado(motor)
        alcance = await iniciar(motor)
        av = await avanzar(motor, alcance)
        assert av.tipo == "orden"
        motor.n.reloj.t += timedelta(hours=25)

        av = await avanzar(motor, alcance)
        assert av.tipo == "mandato-parado" and av.causa == CausaParada.mandato_caducado
        assert av.mandato == PLAN_PDF
        assert mandato(motor).estado == EstadoMandato.parado

        # El reporte se acepta y se guarda, pero no corre el gate ni emite la orden siguiente.
        await motor.report(
            reporte(
                av_orden := motor.n.almacen.obtener_orden(alcance, str(estado(motor, alcance).orden_vigente))
            ),
            JULIAN,
        )
        assert av_orden is not None
        assert len(motor.n.almacen.entradas_pendientes(alcance)) == 1
        assert estado(motor, alcance).orden_vigente is None and not estado(motor, alcance).gates
        assert (await avanzar(motor, alcance)).tipo == "mandato-parado"

        await aprobar_mandato(motor)  # renovar
        assert not motor.n.almacen.entradas_pendientes(alcance)
        av = await avanzar(motor, alcance)
        assert av.tipo == "orden" and av.orden.artefacto.value == "plan"
        renovaciones = [d for d in auditadas(motor, "cambio-mandato") if d.get("renovacion")]
        assert len(renovaciones) == 1

    correr(caso())


def test_revocar_detiene_las_unidades_y_bajar_la_autonomia_las_libera():
    async def caso():
        motor, _ = construir()
        await mandato_aprobado(motor)
        alcance = await iniciar(motor)
        await reportar_siguiente(motor, alcance)  # spec redactado: el gate corre y pide el plan
        m = mandato(motor)
        await reportar_siguiente(motor, alcance)  # plan
        await motor.n.mandatos.revoke(
            MandateRevokeEntrada(alcance=WS_ALCANCE, id=PLAN_PDF, version_vista=m.version, motivo="basta"),
            JULIAN,
        )
        av = await avanzar(motor, alcance)
        assert av.tipo == "mandato-parado" and av.causa == CausaParada.mandato_revocado

        await reportar_siguiente_pendiente(motor, alcance)
        assert motor.n.almacen.entradas_pendientes(alcance)

        # Bajar la autonomía se admite en cualquier momento, también con el mandato revocado.
        e = estado(motor, alcance)
        salida = await motor.set_mode(
            UnitSetModeEntrada(
                unidad=e.unidad, modo=Modo.interactivo, motivo="sin mandato", version_vista=e.version
            ),
            JULIAN,
        )
        assert salida.estado.modo == Modo.interactivo
        assert not motor.n.almacen.entradas_pendientes(alcance)
        av = await avanzar(motor, alcance)
        assert av.tipo == "orden"  # las entradas retenidas se entregaron: ya hay la orden de tasks

    correr(caso())


async def reportar_siguiente_pendiente(motor, alcance):
    """Reporta la orden vigente aunque el mandato retenga la unidad (el avance no la ofrece)."""

    e = estado(motor, alcance)
    orden = motor.n.almacen.obtener_orden(alcance, str(e.orden_vigente))
    await motor.report(reporte(orden), JULIAN)


def test_una_unidad_sin_su_mandato_queda_retenida():
    async def caso():
        motor, _ = construir()
        await mandato_aprobado(motor)
        alcance = await iniciar(motor)
        motor.n.almacen.db.mandatos.delete_many({})
        av = await avanzar(motor, alcance)
        assert av.tipo == "mandato-parado" and av.causa is None and "no existe" in av.detalle

    correr(caso())


def test_el_presupuesto_total_detiene_el_mandato_y_se_reanuda_editando_el_tope():
    async def caso():
        motor, _ = construir()
        await mandato_aprobado(motor, presupuesto=Presupuesto(llamadas_max=1))
        alcance = await iniciar(motor)
        await reportar_siguiente(motor, alcance)
        # El panel alcanzó el tope a mitad del gate: el gate escala y el mandato entero se detiene.
        av = await avanzar(motor, alcance)
        assert av.tipo == "checkpoint" and av.checkpoint.causa_parada == CausaParada.presupuesto_mandato
        assert "por_mandato" in av.checkpoint.pregunta
        parada = mandato(motor).parada
        assert parada.causa == CausaParada.presupuesto_mandato and parada.unidad == alcance.unidad

        # Aprobar sin tocar el tope no sirve: el presupuesto ya está alcanzado.
        with pytest.raises(ErrorNegocio) as exc:
            await aprobar_mandato(motor)
        assert exc.value.codigo == CodigoError.presupuesto_agotado

        m = mandato(motor)
        await motor.n.mandatos.propose(
            _propose(contenido_mandato(presupuesto=Presupuesto(llamadas_max=500)), m.version), JULIAN_CONSOLA
        )
        await aprobar(motor, alcance, av.checkpoint.id, comentario="subo el tope")
        assert (await avanzar(motor, alcance)).tipo == "mandato-parado"  # editado: falta aprobarlo
        await aprobar_mandato(motor)
        av = await avanzar(motor, alcance)
        assert av.tipo == "orden" and av.orden.artefacto.value == "plan"

    correr(caso())


def test_el_consumo_ya_alcanzado_detiene_el_mandato_al_mirarlo():
    async def caso():
        motor, _ = construir()
        await mandato_aprobado(motor, presupuesto=Presupuesto(llamadas_max=5))
        alcance = await iniciar(motor)
        motor.n.escribir(alcance, lambda e: {"consumo": e.consumo.model_copy(update={"llamadas": 5})})
        av = await avanzar(motor, alcance)
        assert av.tipo == "mandato-parado" and av.causa == CausaParada.presupuesto_mandato

    correr(caso())


def test_que_causas_afectan_a_todo_el_mandato():
    from railspec.contracts.comun import CausaEscalado
    from railspec.server.motor.mandatos import es_comun

    assert es_comun(CausaEscalado.sin_gobernanza, "gobernanza no disponible")
    assert es_comun(CausaEscalado.error_proveedor, "proveedor: 503")
    assert es_comun(CausaEscalado.presupuesto_agotado, "presupuesto agotado (por_mandato: llamadas 3/1)")
    assert es_comun(CausaEscalado.presupuesto_agotado, "presupuesto agotado (mensual_usd 2026-10: costo 9/9)")
    assert not es_comun(CausaEscalado.presupuesto_agotado, "presupuesto agotado (por_unidad: tokens 5/1)")
    assert not es_comun(CausaEscalado.presupuesto_agotado, "presupuesto agotado (por_fase spec: tokens 5/1)")
    assert not es_comun(CausaEscalado.sin_convergencia, "oscila")
    assert not es_comun(CausaEscalado.hallazgos_sin_resolver, "hay hallazgos")


def test_el_presupuesto_del_mandato_suma_todas_sus_unidades():
    async def caso():
        motor, _ = construir()
        await mandato_aprobado(motor, presupuesto=Presupuesto(tokens_max=100_000))
        a = await iniciar(motor)
        b = await iniciar(motor, titulo="Segunda unidad")
        await reportar_siguiente(motor, a)
        await reportar_siguiente(motor, b)
        consumo = (
            await motor.n.mandatos.get(MandateGetEntrada(alcance=WS_ALCANCE, id=PLAN_PDF), JULIAN)
        ).consumo
        assert consumo.tokens == estado(motor, a).consumo.tokens + estado(motor, b).consumo.tokens > 0
        assert consumo.llamadas == estado(motor, a).consumo.llamadas + estado(motor, b).consumo.llamadas

    correr(caso())


# --- gates rojos ------------------------------------------------------------------------------------------


def test_supervisado_un_gate_rojo_congela_el_mandato_entero():
    async def caso():
        motor, _ = construir(gobernanza=GobernanzaFija(consultada=GobernanzaConsultada.no))
        await mandato_aprobado(motor)
        a = await iniciar(motor)
        b = await iniciar(motor, titulo="Segunda unidad")
        await reportar_siguiente(motor, a)
        av = await avanzar(motor, a)
        assert av.tipo == "checkpoint" and av.checkpoint.tipo == TipoCheckpoint.gate_escalado
        assert av.checkpoint.causa_parada == CausaParada.gate_escalado
        gate = estado(motor, a).gates[GateFase.spec]
        assert not gate.diferido
        m = mandato(motor)
        assert m.estado == EstadoMandato.parado and m.parada.causa == CausaParada.gate_escalado
        assert m.parada.unidad == a.unidad
        # La otra unidad también queda detenida.
        assert (await avanzar(motor, b)).tipo == "mandato-parado"

        # La persona resuelve el checkpoint y renueva el mandato: sigue donde estaba.
        await aprobar(motor, a, av.checkpoint.id, comentario="gobernanza vacía a propósito")
        assert (await avanzar(motor, a)).tipo == "mandato-parado"
        await aprobar_mandato(motor)
        assert not motor.n.almacen.entradas_pendientes(a)
        assert estado(motor, a).gates[GateFase.spec].rehabilitado is not None
        assert (await avanzar(motor, b)).tipo == "orden"

    correr(caso())


def test_desatendido_un_gate_rojo_difiere_la_unidad_y_las_demas_siguen():
    def guion(peticion):
        return SalidaCritico(hallazgos=[hallazgo_alto()]) if peticion.esquema is SalidaCritico else None

    async def caso():
        motor, _ = construir(guion)
        await mandato_aprobado(motor, modo=Modo.desatendido)
        a = await iniciar(motor, Modo.desatendido)
        b = await iniciar(motor, Modo.desatendido, titulo="Segunda unidad")
        await reportar_siguiente(motor, a)  # el hallazgo se refina...
        await reportar_siguiente(motor, a)  # ...y reaparece: el gate escala
        av = await avanzar(motor, a)
        assert av.tipo == "checkpoint" and av.checkpoint.causa_parada == CausaParada.unidad_amparada_fallida
        assert estado(motor, a).gates[GateFase.spec].diferido
        assert mandato(motor).estado == EstadoMandato.aprobado
        assert (await avanzar(motor, b)).tipo == "orden"

        vista = await motor.n.mandatos.get(MandateGetEntrada(alcance=WS_ALCANCE, id=PLAN_PDF), JULIAN)
        diferidas = [u.unidad for u in vista.unidades if u.diferida]
        assert diferidas == [a.unidad] and vista.vigente

        # La persona la rehabilita y la unidad sigue.
        await aprobar(motor, a, av.checkpoint.id, comentario="aceptado")
        assert (await avanzar(motor, a)).tipo == "orden"

    correr(caso())


def test_desatendido_una_causa_comun_aborta_el_mandato():
    async def caso():
        motor, _ = construir(gobernanza=GobernanzaFija(consultada=GobernanzaConsultada.no))
        await mandato_aprobado(motor, modo=Modo.desatendido)
        a = await iniciar(motor, Modo.desatendido)
        await reportar_siguiente(motor, a)
        m = mandato(motor)
        assert m.estado == EstadoMandato.parado and m.parada.causa == CausaParada.plan_incompleto
        av = await avanzar(motor, a)
        assert av.tipo == "checkpoint" and av.checkpoint.causa_parada == CausaParada.plan_incompleto

    correr(caso())


# --- paradas de la unidad -------------------------------------------------------------------------------


def test_una_orden_bloqueada_es_decision_reservada_y_no_para_a_las_demas():
    async def caso():
        motor, _ = construir()
        await mandato_aprobado(motor)
        a = await iniciar(motor)
        b = await iniciar(motor, titulo="Segunda unidad")
        await reportar_siguiente(motor, a, resultado=ResultadoOrden.bloqueado)
        av = await avanzar(motor, a)
        assert av.tipo == "checkpoint" and av.checkpoint.tipo == TipoCheckpoint.parada
        assert av.checkpoint.causa_parada == CausaParada.decision_reservada
        assert "no delega" in av.checkpoint.pregunta
        assert mandato(motor).estado == EstadoMandato.aprobado
        assert (await avanzar(motor, b)).tipo == "orden"

        await aprobar(motor, a, av.checkpoint.id, decision=Decision.cambios_solicitados, comentario="usa X")
        av = await avanzar(motor, a)
        assert av.tipo == "orden" and "usa X" in av.orden.instrucciones and av.orden.secuencia == 2

    correr(caso())


def test_sin_reintentos_delegados_una_orden_fallida_para_la_unidad():
    async def caso():
        motor, _ = construir()
        await mandato_aprobado(motor)
        a = await iniciar(motor)
        await reportar_siguiente(motor, a, resultado=ResultadoOrden.fallido)
        av = await avanzar(motor, a)
        assert av.tipo == "checkpoint" and av.checkpoint.causa_parada == CausaParada.reintentos_agotados
        assert not estado(motor, a).decisiones

    correr(caso())


def test_el_mandato_reintenta_sin_preguntar_hasta_su_limite_y_lo_registra():
    async def caso():
        motor, _ = construir()
        await mandato_aprobado(motor, reintentos_parada=1)
        a = await iniciar(motor)
        await reportar_siguiente(motor, a, resultado=ResultadoOrden.fallido, motivo="se cayó el linter")
        av = await avanzar(motor, a)
        assert av.tipo == "orden" and av.orden.secuencia == 2
        assert "Reintento automático 1 de 1" in av.orden.instrucciones
        (d,) = estado(motor, a).decisiones
        assert d.id == "DD-1" and d.delegacion == "reintento" and d.revision is None
        assert "se cayó el linter" in d.que and d.orden is not None
        assert [x["decision"] for x in auditadas(motor, "decision-delegada")] == ["DD-1"]

        # Un segundo fallo seguido ya no tiene reintentos delegados.
        await motor.report(reporte(av.orden, resultado=ResultadoOrden.fallido), JULIAN)
        av = await avanzar(motor, a)
        assert av.tipo == "checkpoint" and av.checkpoint.causa_parada == CausaParada.reintentos_agotados

    correr(caso())


def test_completar_la_orden_devuelve_los_reintentos_al_principio():
    async def caso():
        motor, _ = construir()
        await mandato_aprobado(motor, reintentos_parada=1)
        a = await iniciar(motor)
        await reportar_siguiente(motor, a, resultado=ResultadoOrden.fallido)
        await reportar_siguiente(motor, a)  # el reintento sale bien: spec → plan
        await reportar_siguiente(motor, a, resultado=ResultadoOrden.fallido)  # plan falla de nuevo
        av = await avanzar(motor, a)
        assert av.tipo == "orden" and av.orden.artefacto.value == "plan"  # reintento, no parada
        assert [d.id for d in estado(motor, a).decisiones] == ["DD-1", "DD-2"]

    correr(caso())


def _hasta_implementar(motor, a):
    async def caso():
        for _ in range(10):
            av = await avanzar(motor, a)
            if av.orden.tipo == "implementar":
                return av.orden
            await motor.report(reporte(av.orden), JULIAN)

    return caso()


def test_tocar_rutas_fuera_del_mandato_detiene_la_unidad():
    async def caso():
        motor, _ = construir()
        await mandato_aprobado(motor, rutas_permitidas=["src/*", "tests/*"])
        a = await iniciar(motor)
        orden = await _hasta_implementar(motor, a)
        assert orden.mandato.rutas_permitidas == ["src/*", "tests/*"]
        await motor.report(reporte(orden, rutas=("src/pdf.py", "infra/prod.tf")), JULIAN)
        av = await avanzar(motor, a)
        assert av.tipo == "checkpoint" and av.checkpoint.causa_parada == CausaParada.fuera_de_alcance
        assert "infra/prod.tf" in av.checkpoint.pregunta and "src/pdf.py" not in av.checkpoint.pregunta
        assert not estado(motor, a).gates.get(GateFase.codigo)
        assert mandato(motor).estado == EstadoMandato.aprobado

        # Reintentar con indicaciones vuelve a emitir la orden de implementación.
        await aprobar(
            motor, a, av.checkpoint.id, decision=Decision.cambios_solicitados, comentario="solo src"
        )
        orden = (await avanzar(motor, a)).orden
        assert orden.tipo == "implementar" and orden.secuencia > 1
        await motor.report(reporte(orden, rutas=("src/pdf.py",)), JULIAN)
        assert (await avanzar(motor, a)).tipo in ("orden", "cerrada")

    correr(caso())


def test_sin_rutas_permitidas_el_mandato_no_restringe_mas_que_el_plan():
    async def caso():
        motor, _ = construir()
        await mandato_aprobado(motor)
        a = await iniciar(motor)
        orden = await _hasta_implementar(motor, a)
        await motor.report(reporte(orden, rutas=("src/pdf.py",)), JULIAN)
        av = await avanzar(motor, a)
        assert av.tipo != "checkpoint" or av.checkpoint.causa_parada != CausaParada.fuera_de_alcance

    correr(caso())


# --- decisiones delegadas --------------------------------------------------------------------------------


def _decision(delegacion="D-2", que="Llamé al helper firmar_pdf_test"):
    return DecisionPropuesta(
        delegacion=delegacion, que=que, alternativas=["firmar_test"], revertir="Renombrar el helper"
    )


def test_las_decisiones_delegadas_se_registran_y_una_persona_las_revisa():
    async def caso():
        motor, _ = construir()
        await mandato_aprobado(motor)
        a = await iniciar(motor)
        av = await avanzar(motor, a)
        informe = reporte(av.orden).model_copy(
            update={"decisiones": [_decision(), _decision("D-1", "Usé el firmador")]}
        )
        await motor.report(informe, JULIAN)
        e = estado(motor, a)
        assert [(d.id, d.delegacion) for d in e.decisiones] == [("DD-1", "D-2"), ("DD-2", "D-1")]
        assert e.decisiones[0].orden == av.orden.id and e.decisiones[0].tomada_por == JULIAN
        assert len(auditadas(motor, "decision-delegada")) == 2

        revision = MandateReviewEntrada(
            unidad=a,
            decision="DD-1",
            resultado=ResultadoRevision.revertida,
            comentario="no me gusta",
            version_vista=e.version,
        )
        with pytest.raises(ErrorNegocio) as exc:
            await motor.n.mandatos.review(revision, JULIAN)  # el arnés no revisa
        assert exc.value.codigo == CodigoError.fuera_de_alcance
        nuevo = (await motor.n.mandatos.review(revision, JULIAN_CONSOLA)).estado
        assert nuevo.decisiones[0].revision.resultado == ResultadoRevision.revertida
        assert nuevo.decisiones[1].revision is None
        assert auditadas(motor, "revision-decision")[0]["resultado"] == "revertida"
        with pytest.raises(ErrorNegocio) as exc:
            await motor.n.mandatos.review(
                revision.model_copy(update={"version_vista": nuevo.version}), JULIAN_CONSOLA
            )
        assert exc.value.codigo == CodigoError.checkpoint_ya_resuelto
        with pytest.raises(ErrorNegocio) as exc:
            await motor.n.mandatos.review(revision, JULIAN_CONSOLA)  # versión vieja
        assert exc.value.codigo == CodigoError.conflicto_version

        vista = await motor.n.mandatos.get(MandateGetEntrada(alcance=WS_ALCANCE, id=PLAN_PDF), JULIAN)
        assert {x.decision.id for x in vista.decisiones} == {"DD-1", "DD-2"}
        assert vista.unidades[0].decisiones_pendientes == 1
        (resumen,) = (await motor.n.mandatos.list(MandateListEntrada(alcance=WS_ALCANCE), JULIAN)).mandatos
        assert resumen.decisiones_pendientes == 1 and resumen.unidades == 1 and resumen.vigente

    correr(caso())


def test_una_decision_reservada_o_inexistente_o_sin_mandato_se_rechaza_y_la_orden_sigue_abierta():
    async def caso():
        motor, _ = construir()
        await mandato_aprobado(motor)
        a = await iniciar(motor)
        av = await avanzar(motor, a)
        for delegacion in ("D-3", "D-9"):
            informe = reporte(av.orden).model_copy(update={"decisiones": [_decision(delegacion)]})
            with pytest.raises(ErrorNegocio) as exc:
                await motor.report(informe, JULIAN)
            assert exc.value.codigo == CodigoError.fuera_de_alcance
        assert estado(motor, a).orden_vigente == av.orden.id and not estado(motor, a).decisiones

        manual = (await motor.start(entrada_start(titulo="Manual"), JULIAN)).estado.unidad
        orden = (await avanzar(motor, manual)).orden
        with pytest.raises(ErrorNegocio) as exc:
            await motor.report(reporte(orden).model_copy(update={"decisiones": [_decision()]}), JULIAN)
        assert exc.value.codigo == CodigoError.fuera_de_alcance

    correr(caso())


def test_get_y_list_describen_el_mandato():
    async def caso():
        motor, _ = construir()
        await mandato_aprobado(motor, max_unidades=3)
        await proponer_mandato(motor, id_="segundo", modo=Modo.desatendido)
        a = await iniciar(motor)
        vista = await motor.n.mandatos.get(MandateGetEntrada(alcance=WS_ALCANCE, id=PLAN_PDF), JULIAN)
        assert (
            vista.vigente
            and vista.motivo_no_vigente is None
            and vista.huella == vista.mandato.contenido.huella()
        )
        (u,) = vista.unidades
        assert (
            u.unidad == a.unidad and u.modo == Modo.supervisado and not u.diferida and u.causa_parada is None
        )

        todos = (await motor.n.mandatos.list(MandateListEntrada(alcance=WS_ALCANCE), JULIAN)).mandatos
        assert {m.id: m.estado for m in todos} == {
            PLAN_PDF: EstadoMandato.aprobado,
            "segundo": EstadoMandato.propuesto,
        }
        solo = await motor.n.mandatos.list(
            MandateListEntrada(alcance=WS_ALCANCE, estado=[EstadoMandato.propuesto]), JULIAN
        )
        assert [m.id for m in solo.mandatos] == ["segundo"]
        with pytest.raises(ErrorNegocio) as exc:
            await motor.n.mandatos.get(MandateGetEntrada(alcance=WS_ALCANCE, id="no-existe"), JULIAN)
        assert exc.value.codigo == CodigoError.no_encontrado

    correr(caso())


def test_cambiar_el_modo_de_un_mandato_con_unidades_no_se_permite():
    async def caso():
        motor, _ = construir()
        m = await mandato_aprobado(motor)
        await iniciar(motor)
        with pytest.raises(ErrorNegocio) as exc:
            await motor.n.mandatos.propose(
                _propose(contenido_mandato(modo=Modo.desatendido), m.version), JULIAN_CONSOLA
            )
        assert exc.value.codigo == CodigoError.conversion_no_permitida

    correr(caso())
