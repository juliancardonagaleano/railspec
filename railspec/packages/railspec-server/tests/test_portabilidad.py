"""unit.import y unit.export (contrato 1.4): retoma en fase, idempotencia por origen y paquete exportado."""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from apoyo_motor import (
    BASE,
    JULIAN,
    JULIAN_CONSOLA,
    PLAN,
    REPO,
    SPEC,
    TASKS,
    WS_ALCANCE,
    aprobar,
    avanzar,
    construir,
    entrada_start,
    reporte,
)
from railspec.contracts.comun import (
    Actor,
    Canal,
    CausaEscalado,
    EstadoFase,
    Fase,
    GateFase,
    Modo,
    Riesgo,
    TipoActor,
    Veredicto,
)
from railspec.contracts.estado import TipoCheckpoint
from railspec.contracts.orden import Artefacto
from railspec.contracts.portabilidad import ArtefactosPaquete, OrigenPaquete, PaqueteUnidad
from railspec.contracts.reporte import ArtefactoRedactado
from railspec.contracts.tools import (
    RepositorioInicio,
    Superficie,
    UnitExportEntrada,
    UnitImportEntrada,
)
from railspec.server.api import AutorizadorRoles, Registro
from railspec.server.motor.motor import ErrorNegocio
from railspec.server.portabilidad import Portabilidad, manejadores

SPEC_KIT = """# Spec — Emitir PDF firmado

## Problema / Motivación

Los certificados salen sin firma.

## Resultado esperado

- **CA-01** — El PDF lleva firma.
"""


def correr(coro):
    return asyncio.run(coro)


def artefacto(tipo: Artefacto, texto: str) -> ArtefactoRedactado:
    return ArtefactoRedactado(tipo=tipo, contenido=texto, sha256=hashlib.sha256(texto.encode()).hexdigest())


def paquete(fase: Fase, *textos: str, **extra) -> PaqueteUnidad:
    tipos = (Artefacto.spec, Artefacto.plan, Artefacto.tasks)
    return PaqueteUnidad(
        origen=OrigenPaquete(tipo="sdd-kit", id_original="0007-pce-mcp", repositorio=REPO),
        titulo="Emitir PDF firmado",
        pedido="Los certificados deben salir firmados.",
        artefactos=ArtefactosPaquete(
            **{t.value: artefacto(t, x) for t, x in zip(tipos, textos, strict=False)}
        ),
        fase_retomar=fase,
        **extra,
    )


def entrada(p: PaqueteUnidad) -> UnitImportEntrada:
    return UnitImportEntrada(
        alcance=WS_ALCANCE,
        repositorios=[RepositorioInicio(repositorio=REPO, rama="main", base_commit=BASE)],
        version_contrato_cliente="1.4",
        paquete=p,
    )


def montar():
    motor, _ = construir()
    return motor, Portabilidad(motor)


def test_importar_en_implement_retoma_implementando_y_cierra():
    async def caso():
        motor, port = montar()
        salida = await port.importar(entrada(paquete(Fase.implement, SPEC, PLAN, TASKS)), JULIAN)
        assert not salida.ya_existia and salida.version_contrato_negociada == "1.4"
        alcance = salida.estado.unidad
        assert alcance.unidad == "0001-emitir-pdf-firmado"
        assert salida.estado.dueno == JULIAN and salida.estado.fase == Fase.implement

        vistos = []
        for _ in range(10):
            av = await avanzar(motor, alcance)
            vistos.append(av.tipo if av.tipo != "orden" else f"orden:{av.orden.tipo}")
            if av.tipo == "cerrada":
                break
            assert av.tipo == "orden"
            if av.orden.tipo == "implementar":
                # Las tareas y el alcance salen del tasks y del plan importados.
                assert [t.id for t in av.orden.tareas] == ["T-01", "T-02"]
                assert av.orden.alcance.permitidos[:2] == ["src/pdf.py", "tests/test_pdf.py"]
            await motor.report(reporte(av.orden), JULIAN_CONSOLA)
        assert vistos == ["orden:implementar", "orden:validar", "cerrada"]
        estado = motor.n.almacen.obtener_estado(alcance)
        # Solo corrió el gate de código; los de spec, plan y tasks no se fingen.
        assert set(estado.gates) == {GateFase.codigo}

    correr(caso())


def test_artefacto_del_kit_que_no_encaja_vuelve_a_refinar_con_hallazgos_de_estructura():
    async def caso():
        motor, port = montar()
        salida = await port.importar(entrada(paquete(Fase.plan, SPEC_KIT)), JULIAN)
        av = await avanzar(motor, salida.estado.unidad)
        assert av.tipo == "orden" and av.orden.tipo == "refinar" and av.orden.artefacto == Artefacto.spec
        assert av.orden.sha256_actual == hashlib.sha256(SPEC_KIT.encode()).hexdigest()
        titulos = {h.titulo for h in av.orden.hallazgos}
        assert "falta la sección '## Alcance'" in titulos
        assert all(h.lente == "estructura" for h in av.orden.hallazgos)
        assert motor.n.almacen.obtener_estado(salida.estado.unidad).fase == Fase.spec

        # Refinado, el gate pasa y la unidad sigue con el plan.
        await motor.report(reporte(av.orden, texto=SPEC), JULIAN_CONSOLA)
        av = await avanzar(motor, salida.estado.unidad)
        assert av.tipo == "checkpoint" and av.checkpoint.tipo == TipoCheckpoint.aprobar_spec

    correr(caso())


@pytest.mark.parametrize(
    ("fase", "textos", "primera"),
    [
        (Fase.spec, (), "orden:redactar:spec"),
        (Fase.plan, (SPEC,), "orden:redactar:plan"),
        (Fase.tasks, (SPEC, PLAN), "orden:redactar:tasks"),
        (Fase.aprobacion, (SPEC, PLAN, TASKS), "orden:implementar:implement"),
    ],
)
def test_retoma_en_la_fase_del_paquete(fase, textos, primera):
    async def caso():
        motor, port = montar()
        salida = await port.importar(entrada(paquete(fase, *textos)), JULIAN)
        av = await avanzar(motor, salida.estado.unidad)
        assert f"{av.tipo}:{av.orden.tipo}:{av.orden.fase.value}" == primera

    correr(caso())


def test_aprobacion_en_semi_autonomo_abre_el_paquete_de_aprobacion():
    async def caso():
        motor, port = montar()
        p = paquete(Fase.aprobacion, SPEC, PLAN, TASKS, modo=Modo.semi_autonomo, riesgo=Riesgo.bajo)
        salida = await port.importar(entrada(p), JULIAN)
        assert salida.estado.modo == Modo.semi_autonomo
        assert salida.estado.modo_conversion[0].de == Modo.interactivo
        av = await avanzar(motor, salida.estado.unidad)
        assert av.tipo == "checkpoint" and av.checkpoint.tipo == TipoCheckpoint.paquete_aprobacion

    correr(caso())


def test_unidad_cerrada_entra_cerrada_con_el_gate_de_codigo_rehabilitado():
    async def caso():
        motor, port = montar()
        salida = await port.importar(entrada(paquete(Fase.done, SPEC_KIT, PLAN, TASKS)), JULIAN)
        estado = salida.estado
        assert (estado.fase, estado.estado) == (Fase.done, EstadoFase.completado)
        gate = estado.gates[GateFase.codigo]
        assert (gate.veredicto, gate.causa, gate.iteraciones) == (
            Veredicto.escalado,
            CausaEscalado.importado,
            0,
        )
        assert gate.rehabilitado is not None and gate.rehabilitado.actor == JULIAN
        assert (await avanzar(motor, estado.unidad)).tipo == "cerrada"

    correr(caso())


def test_importar_es_idempotente_por_origen():
    async def caso():
        motor, port = montar()
        p = paquete(Fase.plan, SPEC)
        primera = await port.importar(entrada(p), JULIAN)
        segunda = await port.importar(entrada(p), JULIAN)
        assert segunda.ya_existia and segunda.estado.unidad == primera.estado.unidad
        auditoria = list(motor.n.almacen.db.auditoria.find({"evento": "importacion"}))
        assert len(auditoria) == 1 and auditoria[0]["origen_importacion"]["id_original"] == "0007-pce-mcp"
        assert (
            auditoria[0]["artefactos_importados"] == 1
            and auditoria[0]["unidad"] == primera.estado.unidad.unidad
        )
        otro = p.model_copy(update={"origen": p.origen.model_copy(update={"id_original": "0008-otra"})})
        tercera = await port.importar(entrada(otro), JULIAN)
        assert not tercera.ya_existia and tercera.estado.unidad != primera.estado.unidad

    correr(caso())


def test_importar_exige_una_persona():
    agente = Actor(tipo=TipoActor.agente, canal=Canal.arnes, agente="bucle", en_nombre_de=JULIAN.github_id)
    _, port = montar()
    with pytest.raises(ErrorNegocio) as exc:
        correr(port.importar(entrada(paquete(Fase.spec)), agente))
    assert exc.value.codigo.value == "fuera-de-alcance"


def test_exportar_devuelve_los_artefactos_aprobados_como_prefijo():
    async def caso():
        motor, port = montar()
        salida = await motor.start(entrada_start(version_contrato_cliente="1.4"), JULIAN)
        alcance = salida.estado.unidad
        # Recién arrancada: nada aprobado, retoma en spec.
        vacio = (await port.exportar(UnitExportEntrada(unidad=alcance), JULIAN)).paquete
        assert vacio.artefactos.presentes() == 0 and vacio.fase_retomar == Fase.spec

        for _ in range(3):  # spec redactado y aprobado; plan redactado y en su checkpoint
            av = await avanzar(motor, alcance)
            if av.tipo == "orden":
                await motor.report(reporte(av.orden), JULIAN_CONSOLA)
            else:
                await aprobar(motor, alcance, av.checkpoint.id, actor=JULIAN_CONSOLA)
        p = (await port.exportar(UnitExportEntrada(unidad=alcance), JULIAN)).paquete
        assert p.origen.tipo == "railspec" and p.origen.id_original == alcance.unidad
        assert p.origen.repositorio == REPO
        assert (
            p.fase_retomar == Fase.plan and p.artefactos.spec.contenido == SPEC and p.artefactos.plan is None
        )
        assert [(g.gate, g.resultado) for g in p.historial_gates] == [
            ("spec", "aprobado"),
            ("plan", "aprobado"),
        ]

        # El paquete exportado se importa en otro workspace y retoma igual.
        destino = entrada(p).model_copy(
            update={"alcance": WS_ALCANCE.model_copy(update={"workspace": "otro"})}
        )
        importada = await port.importar(destino, JULIAN)
        assert importada.estado.unidad.workspace == "otro" and importada.estado.fase == Fase.plan

    correr(caso())


def test_el_registro_expone_import_y_export_por_mcp_y_http():
    motor, _ = construir()
    registro = Registro.del_motor(motor, AutorizadorRoles(motor.n.almacen, abierto=True), manejadores(motor))
    for superficie in (Superficie.mcp, Superficie.http):
        nombres = {t.nombre for t in registro.tools(superficie)}
        assert {"unit.import", "unit.export"} <= nombres
    resultado = correr(
        registro.invocar(
            "unit_import", entrada(paquete(Fase.spec)).model_dump(mode="json"), JULIAN, Superficie.mcp
        )
    )
    assert resultado.ok and resultado.cuerpo["ya_existia"] is False
