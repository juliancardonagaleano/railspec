"""Contexto de grafo en las órdenes de spec, plan y tasks: con grafo, sin grafo y con el grafo caído."""

from __future__ import annotations

import asyncio

import pytest
from apoyo_motor import BASE, JULIAN, ORG, REPO, WS, aprobar, avanzar, construir, entrada_start, reporte
from railspec.contracts.comun import AlcanceRepositorio, RolRepositorio
from railspec.contracts.estado import Decision
from railspec.contracts.snapshot import DeltaIndice, MotorIndice, Simbolo, TipoSimbolo, id_simbolo
from railspec.graph import AccesoGrafo, AlmacenGrafo, MotorMemoria
from railspec.server.motor import contexto_grafo
from railspec.server.motor.contexto_grafo import terminos


def correr(coro):
    return asyncio.run(coro)


ALCANCE_REPO = AlcanceRepositorio(org=ORG, workspace=WS, repositorio=REPO)


def simbolo(ruta: str, nombre: str) -> Simbolo:
    return Simbolo(
        id=id_simbolo(REPO, ruta, "funcion", nombre),
        nombre=nombre,
        tipo=TipoSimbolo.funcion,
        ruta=ruta,
        linea_inicio=1,
        linea_fin=9,
        sha256="b" * 64,
    )


def grafo_indexado(*simbolos: Simbolo) -> AlmacenGrafo:
    grafo = AlmacenGrafo(AccesoGrafo(MotorMemoria()))
    delta = DeltaIndice(motor=MotorIndice(version="0.11.0"), simbolos_upsert=list(simbolos))
    grafo.aplicar_delta(ALCANCE_REPO, BASE, delta, None)
    return grafo


async def primera_orden(motor):
    alcance = (await motor.start(entrada_start(), JULIAN)).estado.unidad
    av = await avanzar(motor, alcance)
    assert av.tipo == "orden" and av.orden.tipo == "redactar"
    return alcance, av.orden


# --- términos de la consulta --------------------------------------------------------------


def test_los_nombres_entre_comillas_van_primero_y_exactos():
    assert terminos("Emitir `pdf.firmar` al guardar", "Con `servicio.emitir`.") == [
        ("pdf.firmar", True),
        ("servicio.emitir", True),
        ("Emitir", False),
        ("guardar", False),
    ]


def test_las_palabras_vacias_las_cortas_y_las_repetidas_no_entran():
    assert terminos("Para que los PDF salgan con la firma", "con firma y PDF otra vez") == [
        ("PDF", False),
        ("salgan", False),
        ("firma", False),
        ("otra", False),
        ("vez", False),
    ]


def test_los_terminos_estan_acotados():
    muchas = " ".join(f"palabra{i}" for i in range(50))
    assert len(terminos(muchas)) == contexto_grafo.MAX_TERMINOS


# --- con grafo ---------------------------------------------------------------------------


def test_spec_plan_y_tasks_llevan_la_rebanada_del_grafo_con_su_frescura():
    firmar = simbolo("src/pdf.py", "pdf.firmar")
    render = simbolo("src/pdf.py", "pdf.render")
    otro = simbolo("src/usuarios.py", "usuarios.listar")

    async def caso():
        motor, _ = construir()
        motor.n.grafo = grafo_indexado(firmar, render, otro)
        alcance = (await motor.start(entrada_start(), JULIAN)).estado.unidad
        contextos = {}
        for _ in range(12):
            av = await avanzar(motor, alcance)
            if av.tipo == "cerrada":
                break
            if av.tipo == "orden":
                contextos[av.orden.tipo, getattr(av.orden, "artefacto", None)] = av.orden.contexto
                await motor.report(reporte(av.orden), JULIAN)
            else:
                await aprobar(motor, alcance, av.checkpoint.id, decision=Decision.aprobado)
        return contextos

    contextos = correr(caso())

    for artefacto in ("spec", "plan", "tasks"):
        ctx = next(
            c for (tipo, a), c in contextos.items() if tipo == "redactar" and a and a.value == artefacto
        )
        assert {n.nombre for n in ctx.grafo} == {"pdf.firmar", "pdf.render"}  # «PDF» coincide; usuarios no
        nodo = next(n for n in ctx.grafo if n.nombre == "pdf.firmar")
        assert nodo.simbolo == firmar.id and nodo.ruta == "src/pdf.py" and nodo.tipo == TipoSimbolo.funcion
        assert nodo.repositorio == REPO and nodo.rol == RolRepositorio.primario
        assert "PDF" in nodo.motivo
        f = ctx.grafo_frescura[REPO]
        assert f.indexado and f.commit == BASE and f.indexado_en is not None
        assert ctx.grafo_avisos == []
    # La orden de implementar no consulta el grafo: el arnés lo pide con graph_query.
    implementar = contextos["implementar", None]
    assert implementar.grafo == [] and implementar.grafo_frescura == {} and implementar.grafo_avisos == []


def test_el_contexto_no_lleva_texto_de_codigo_y_esta_acotado(monkeypatch):
    monkeypatch.setattr(contexto_grafo, "MAX_NODOS", 3)
    simbolos = [simbolo(f"src/pdf{i}.py", f"pdf.funcion{i}") for i in range(10)]

    async def caso():
        motor, _ = construir()
        motor.n.grafo = grafo_indexado(*simbolos)
        return (await primera_orden(motor))[1].contexto

    ctx = correr(caso())

    assert len(ctx.grafo) == 3
    assert set(type(ctx.grafo[0]).model_fields) == {
        "repositorio",
        "rol",
        "simbolo",
        "nombre",
        "tipo",
        "ruta",
        "motivo",
    }


def test_el_tope_de_caracteres_corta_la_rebanada(monkeypatch):
    monkeypatch.setattr(contexto_grafo, "MAX_CARACTERES", 120)
    simbolos = [simbolo(f"src/pdf{i}.py", f"pdf.funcion{i}") for i in range(10)]

    async def caso():
        motor, _ = construir()
        motor.n.grafo = grafo_indexado(*simbolos)
        return (await primera_orden(motor))[1].contexto

    ctx = correr(caso())

    assert 0 < len(ctx.grafo) < 10
    assert sum(len(n.nombre) + len(n.ruta) + len(n.motivo) for n in ctx.grafo) <= 120


def test_un_canonico_sin_coincidencias_dice_que_la_busqueda_es_por_nombre():
    async def caso():
        motor, _ = construir()
        motor.n.grafo = grafo_indexado(simbolo("src/usuarios.py", "usuarios.listar"))
        return (await primera_orden(motor))[1].contexto

    ctx = correr(caso())

    assert ctx.grafo == [] and ctx.grafo_frescura[REPO].indexado
    (aviso,) = ctx.grafo_avisos
    assert "no encontró símbolos" in aviso and "por nombre de símbolo, no por significado" in aviso


def test_un_canonico_desactualizado_llega_al_contexto():
    from datetime import UTC, datetime, timedelta

    reloj = [datetime(2026, 1, 1, tzinfo=UTC)]
    grafo = AlmacenGrafo(AccesoGrafo(MotorMemoria()), reloj=lambda: reloj[0], frescura_horas=24)
    delta = DeltaIndice(
        motor=MotorIndice(version="0.11.0"), simbolos_upsert=[simbolo("src/pdf.py", "pdf.firmar")]
    )
    grafo.aplicar_delta(ALCANCE_REPO, BASE, delta, None)
    reloj[0] += timedelta(hours=100)

    async def caso():
        motor, _ = construir()
        motor.n.grafo = grafo
        return (await primera_orden(motor))[1].contexto

    ctx = correr(caso())

    assert [n.nombre for n in ctx.grafo] == ["pdf.firmar"]  # el grafo viejo sigue aportando
    assert ctx.grafo_frescura[REPO].desactualizado
    assert any("puede no reflejar el código actual" in a for a in ctx.grafo_avisos)


def test_un_canonico_que_no_coincide_con_el_repositorio_llega_al_contexto():
    """La divergencia de contenido que ``graph.index`` detectó viaja como la de frescura: en ``avisos``."""

    from railspec.graph.motor import Meta

    acceso = AccesoGrafo(MotorMemoria())
    grafo = AlmacenGrafo(acceso)
    delta = DeltaIndice(
        motor=MotorIndice(version="0.11.0"), simbolos_upsert=[simbolo("src/pdf.py", "pdf.firmar")]
    )
    grafo.aplicar_delta(ALCANCE_REPO, BASE, delta, None)
    acceso.espacio(ALCANCE_REPO).fijar_meta(
        Meta(
            commit=BASE,
            actualizado=grafo.ahora(),
            contenido_verificado=False,
            divergencias_total=2,
            rutas_divergentes=["src/a.py", "src/b.py"],
        )
    )

    async def caso():
        motor, _ = construir()
        motor.n.grafo = grafo
        return (await primera_orden(motor))[1].contexto

    ctx = correr(caso())

    assert [n.nombre for n in ctx.grafo] == ["pdf.firmar"]  # sigue aportando, con el aviso
    f = ctx.grafo_frescura[REPO]
    assert f.contenido_verificado is False and f.divergencias_total == 2
    assert f.rutas_divergentes == ["src/a.py", "src/b.py"]
    (aviso,) = ctx.grafo_avisos
    assert "no coincide" in aviso and "src/a.py" in aviso and "completo" in aviso


# --- degradación ---------------------------------------------------------------------------


def test_sin_grafo_configurado_la_orden_sale_y_el_contexto_lo_dice():
    async def caso():
        motor, _ = construir()
        assert motor.n.grafo is None
        return (await primera_orden(motor))[1]

    orden = correr(caso())

    assert orden.contexto.grafo == [] and orden.contexto.grafo_frescura == {}
    (aviso,) = orden.contexto.grafo_avisos
    assert "no tiene grafo de código configurado" in aviso


def test_el_canonico_sin_indexar_sale_sin_rebanada_y_con_aviso():
    async def caso():
        motor, _ = construir()
        motor.n.grafo = AlmacenGrafo(AccesoGrafo(MotorMemoria()))  # nadie indexó el repositorio
        return (await primera_orden(motor))[1].contexto

    ctx = correr(caso())

    assert ctx.grafo == []
    assert ctx.grafo_frescura[REPO].indexado is False
    (aviso,) = (
        ctx.grafo_avisos
    )  # el de sin índice; no se añade "no encontró" a un grafo vacío por falta de índice
    assert f"{REPO}: sin índice canónico" in aviso


@pytest.mark.parametrize("fallo", [ConnectionError("falkordb caído"), AttributeError("sin consultar")])
def test_un_grafo_que_falla_no_frena_la_orden(fallo):
    class GrafoRoto:
        def consultar(self, *a, **k):
            raise fallo

    async def caso():
        motor, _ = construir()
        motor.n.grafo = GrafoRoto()
        return (await primera_orden(motor))[1].contexto

    ctx = correr(caso())

    assert ctx.grafo == []
    (aviso,) = ctx.grafo_avisos
    assert "no respondió" in aviso and type(fallo).__name__ in aviso


def test_sin_terminos_utiles_no_se_consulta_y_se_avisa():
    class GrafoQueNoSeDebeLlamar:
        def consultar(self, *a, **k):
            raise AssertionError("no debía consultarse")

    async def caso():
        motor, _ = construir()
        motor.n.grafo = GrafoQueNoSeDebeLlamar()
        alcance = (await motor.start(entrada_start(titulo="Ya", pedido="de la a"), JULIAN)).estado.unidad
        return (await avanzar(motor, alcance)).orden.contexto

    ctx = correr(caso())

    assert ctx.grafo == []
    (aviso,) = ctx.grafo_avisos
    assert "no traen nombres ni palabras" in aviso
