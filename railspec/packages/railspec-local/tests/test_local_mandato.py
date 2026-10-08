"""Mandato (contrato 1.11) en el proxy local: avance detenido, paradas con causa, decisiones delegadas,
alcance del mandato, tools MCP para el arnés, CLI y plantillas de los adaptadores."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import fabricas
import pytest
from local_fabricas import T0, ServidorDoble, crear_proxy, orden_implementar
from mcp import Client
from railspec.contracts.comun import Modo
from railspec.contracts.estado import TipoCheckpoint
from railspec.contracts.mandato import (
    CausaParada,
    DecisionPropuesta,
    Delegacion,
    EstadoMandato,
    MandatoEnOrden,
    TipoDelegacion,
)
from railspec.contracts.reporte import ResultadoOrden
from railspec.contracts.tools import (
    TOOLS,
    AvanceCerrada,
    AvanceCheckpoint,
    AvanceEspera,
    AvanceMandatoParado,
    AvanceOrden,
    Superficie,
    nombre_mcp,
)
from railspec.local import adaptadores, cli, config
from railspec.local.almacen import Almacen
from railspec.local.cliente import ClienteServidor
from railspec.local.errores import (
    DecisionSinRespaldo,
    ErrorRailspec,
    FueraDeAlcance,
    FueraDeAlcanceMandato,
)
from railspec.local.servidor_mcp import COMO_RESOLVER_CHECKPOINT, como_resolver_checkpoint, crear_servidor

CORREGIDO = "def suma(a, b):\n    return a + b\n"
CONSOLA = "https://railspec.example/consola/"


def correr(coro):
    return asyncio.run(coro)


def _mandato_en_orden(rutas: list[str] | None = None) -> MandatoEnOrden:
    return MandatoEnOrden(
        mandato="pdf-a",
        modo=Modo.desatendido,
        caduca_en=T0 + timedelta(hours=12),
        delegaciones=[
            Delegacion(id="D-1", tipo=TipoDelegacion.pre_decidida, texto="Usa la biblioteca actual."),
            Delegacion(id="D-2", tipo=TipoDelegacion.con_criterio, texto="Nombres: estilo del módulo."),
            Delegacion(id="D-3", tipo=TipoDelegacion.reservada, texto="Esquema de base de datos."),
        ],
        rutas_permitidas=rutas or [],
    )


def _orden_con_mandato(rutas: list[str] | None = None):
    mandato = _mandato_en_orden(rutas)
    return lambda estado, secuencia, orden_id: orden_implementar(estado, secuencia, orden_id, mandato=mandato)


def _arrancar(tmp_path: Path, servidor: ServidorDoble, url: str | None = None):
    proxy = crear_proxy(tmp_path, servidor)
    if url:
        proxy.config = proxy.config.model_copy(update={"url": url})
    inicio = correr(proxy.iniciar("Corregir suma", "La suma resta en vez de sumar."))
    return proxy, Path(inicio["worktree"])


DECISION = {
    "delegacion": "D-2",
    "que": "Llamé `sumar_enteros` a la función nueva.",
    "alternativas": ["`suma2`", "`add`"],
    "revertir": "Renombrar la función en src/calc.py.",
}


# --- unit_advance: mandato-parado --------------------------------------------------------------------


def test_avanzar_con_el_mandato_parado_se_detiene_y_dice_que_hacer(tmp_path):
    servidor = ServidorDoble()
    proxy, worktree = _arrancar(tmp_path, servidor, url="https://railspec.example/mcp")
    servidor.mandato_parado = AvanceMandatoParado(
        mandato="pdf-a",
        causa=CausaParada.mandato_caducado,
        detalle="La aprobación caducó.",
        reintentar_en_s=120,
    )

    respuesta = correr(proxy.avanzar())

    assert respuesta["tipo"] == "mandato-parado"
    assert respuesta["mandato"] == "pdf-a" and respuesta["causa"] == "mandato-caducado"
    assert respuesta["detalle"] == "La aprobación caducó." and respuesta["reintentar_en_s"] == 120
    como = respuesta["como_resolver"]
    assert "Detente" in como and "Solo una persona" in como and CONSOLA in como
    assert "no existe tool" in como and "No reintentes en bucle" in como and "120 s" in como
    estado = Almacen(worktree).leer()
    assert estado.orden_en_curso is None
    assert estado.espejo_remoto is not None and estado.espejo_remoto.version == servidor.estado.version


def test_mandato_parado_sin_causa_ni_url_de_consola(tmp_path):
    servidor = ServidorDoble()
    proxy, _ = _arrancar(tmp_path, servidor)
    servidor.mandato_parado = AvanceMandatoParado(mandato="pdf-a", detalle="El mandato no está aprobado.")

    respuesta = correr(proxy.avanzar())

    assert respuesta["causa"] is None and respuesta["reintentar_en_s"] == 300
    assert "la consola web del servidor" in respuesta["como_resolver"]


def test_mandato_parado_no_rompe_la_cola_sin_conexion(tmp_path):
    servidor = ServidorDoble([orden_implementar])
    proxy, worktree = _arrancar(tmp_path, servidor)
    correr(proxy.avanzar())
    (worktree / "src" / "calc.py").write_text(CORREGIDO, encoding="utf-8")
    servidor.conectado = False
    assert correr(proxy.reportar(tareas_completadas=["T-01"]))["encolado"] is True
    assert correr(proxy.avanzar())["tipo"] == "sin-conexion"

    servidor.conectado = True
    servidor.mandato_parado = AvanceMandatoParado(
        mandato="pdf-a", causa=CausaParada.gate_escalado, detalle="Gate escalado."
    )
    respuesta = correr(proxy.avanzar())

    # El reporte pendiente viaja antes de que el servidor diga que se detenga.
    assert respuesta["tipo"] == "mandato-parado"
    assert len(servidor.reportes) == 1
    assert Almacen(worktree).leer().cola_pendiente == []


def test_el_avance_sigue_funcionando_tras_renovar_el_mandato(tmp_path):
    servidor = ServidorDoble([orden_implementar])
    proxy, _ = _arrancar(tmp_path, servidor)
    servidor.mandato_parado = AvanceMandatoParado(mandato="pdf-a", detalle="Caducó.")
    assert correr(proxy.avanzar())["tipo"] == "mandato-parado"
    servidor.mandato_parado = None
    assert correr(proxy.avanzar())["tipo"] == "orden"


def test_avanzar_conoce_todos_los_tipos_de_avance_del_contrato():
    # Si el contrato añade un avance, esta prueba obliga a decidir qué hace el proxy con él.
    tipos = {
        c.model_fields["tipo"].default
        for c in (AvanceOrden, AvanceCheckpoint, AvanceEspera, AvanceCerrada, AvanceMandatoParado)
    }
    skill = adaptadores.plantilla("skill-bucle.md")
    assert {t for t in tipos if f"`{t}`" not in skill} == set()


# --- checkpoints con causa de parada -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("causa", "debe", "no_debe"),
    [
        (CausaParada.unidad_amparada_fallida, ["quedó diferida", "NO esperes", "siguiente unidad"], []),
        (
            CausaParada.fuera_de_alcance,
            ["rutas que el mandato no permite", "La decide una persona"],
            ["NO esperes"],
        ),
        (CausaParada.decision_reservada, ["No decidas tú", "La decide una persona"], ["NO esperes"]),
        (CausaParada.reintentos_agotados, ["No la reintentes", "La decide una persona"], ["NO esperes"]),
        (
            CausaParada.gate_escalado,
            ["un gate escaló", "mandato-parado", "La decide una persona"],
            ["NO esperes"],
        ),
    ],
)
def test_checkpoint_con_causa_dice_que_hacer(tmp_path, causa, debe, no_debe):
    servidor = ServidorDoble()
    proxy, _ = _arrancar(tmp_path, servidor)

    async def flujo():
        async with Client(crear_servidor(lambda: proxy)) as cliente:
            servidor.abrir_checkpoint(TipoCheckpoint.parada, causa)
            return _datos(await cliente.call_tool("unit_advance", {}))

    respuesta = asyncio.run(flujo())

    assert respuesta["tipo"] == "checkpoint" and respuesta["checkpoint"]["causa_parada"] == causa.value
    for texto in debe:
        assert texto in respuesta["como_resolver"]
    for texto in no_debe:
        assert texto not in respuesta["como_resolver"]
    assert respuesta["como_resolver"] != COMO_RESOLVER_CHECKPOINT


def test_checkpoint_sin_causa_conserva_el_texto_de_siempre(tmp_path):
    servidor = ServidorDoble()
    proxy, _ = _arrancar(tmp_path, servidor)

    async def flujo():
        async with Client(crear_servidor(lambda: proxy)) as cliente:
            servidor.abrir_checkpoint()
            return _datos(await cliente.call_tool("unit_advance", {}))

    respuesta = asyncio.run(flujo())

    assert "causa_parada" not in respuesta["checkpoint"]
    assert respuesta["como_resolver"] == COMO_RESOLVER_CHECKPOINT
    assert (
        como_resolver_checkpoint(None) == como_resolver_checkpoint("causa-futura") == COMO_RESOLVER_CHECKPOINT
    )


# --- unit_report con decisiones -------------------------------------------------------------------------


def test_el_reporte_lleva_las_decisiones_validadas(tmp_path):
    servidor = ServidorDoble([_orden_con_mandato()])
    proxy, worktree = _arrancar(tmp_path, servidor)
    correr(proxy.avanzar())
    (worktree / "src" / "calc.py").write_text(CORREGIDO, encoding="utf-8")

    resultado = correr(proxy.reportar(tareas_completadas=["T-01"], decisiones=[DECISION]))

    assert resultado["aceptado"] is True
    (decision,) = servidor.reportes[-1].decisiones
    assert isinstance(decision, DecisionPropuesta) and decision.delegacion == "D-2"
    assert decision.alternativas == ["`suma2`", "`add`"] and decision.revertir.startswith("Renombrar")


def test_las_decisiones_sobreviven_a_la_cola_sin_conexion(tmp_path):
    servidor = ServidorDoble([_orden_con_mandato()])
    proxy, worktree = _arrancar(tmp_path, servidor)
    orden = correr(proxy.avanzar())["orden"]["id"]
    (worktree / "src" / "calc.py").write_text(CORREGIDO, encoding="utf-8")
    servidor.conectado = False

    resultado = correr(proxy.reportar(tareas_completadas=["T-01"], decisiones=[DECISION]))

    assert resultado["encolado"] is True and servidor.reportes == []
    pendiente = Almacen(worktree).leer_pendiente(UUID(orden))
    assert pendiente is not None and [d.que for d in pendiente.decisiones] == [DECISION["que"]]

    servidor.conectado = True
    assert correr(proxy.sincronizar())["rechazos"] == []
    (decision,) = servidor.reportes[-1].decisiones
    assert decision.model_dump(mode="json", exclude_none=True) == DECISION


@pytest.mark.parametrize(
    ("decisiones", "mensaje"),
    [
        ([{**DECISION, "delegacion": "D-3"}], "es `reservada`"),
        ([{**DECISION, "delegacion": "D-9"}], "no existe en el mandato"),
        ([{k: v for k, v in DECISION.items() if k != "revertir"}], "no cumple el contrato"),
        ([{**DECISION, "delegacion": "reintento"}], "no cumple el contrato"),
    ],
)
def test_una_decision_sin_respaldo_falla_antes_de_reportar(tmp_path, decisiones, mensaje):
    servidor = ServidorDoble([_orden_con_mandato()])
    proxy, worktree = _arrancar(tmp_path, servidor)
    correr(proxy.avanzar())
    (worktree / "src" / "calc.py").write_text(CORREGIDO, encoding="utf-8")

    with pytest.raises(ErrorRailspec, match=mensaje) as exc:
        correr(proxy.reportar(resultado=ResultadoOrden.completado, decisiones=decisiones))

    if "contrato" not in mensaje:
        assert isinstance(exc.value, DecisionSinRespaldo) and "bloqueado" in str(exc.value)
    estado = Almacen(worktree).leer()
    assert servidor.reportes == [] and estado.cola_pendiente == [] and estado.orden_en_curso is not None


def test_una_decision_en_una_orden_sin_mandato_falla(tmp_path):
    servidor = ServidorDoble([orden_implementar])
    proxy, worktree = _arrancar(tmp_path, servidor)
    correr(proxy.avanzar())
    (worktree / "src" / "calc.py").write_text(CORREGIDO, encoding="utf-8")

    with pytest.raises(DecisionSinRespaldo, match="no corre bajo un mandato"):
        correr(proxy.reportar(decisiones=[DECISION]))
    assert servidor.reportes == []


def test_reportar_bloqueado_sin_decisiones_no_cambia(tmp_path):
    servidor = ServidorDoble([_orden_con_mandato()])
    proxy, _ = _arrancar(tmp_path, servidor)
    correr(proxy.avanzar())

    resultado = correr(
        proxy.reportar(resultado=ResultadoOrden.bloqueado, motivo="Necesito cambiar el esquema (D-3).")
    )

    assert resultado["aceptado"] is True
    assert (
        servidor.reportes[-1].decisiones == [] and servidor.reportes[-1].resultado == ResultadoOrden.bloqueado
    )


# --- alcance del mandato --------------------------------------------------------------------------------


def test_una_ruta_fuera_de_las_del_mandato_falla_antes_de_reportar(tmp_path):
    servidor = ServidorDoble([_orden_con_mandato(["src/pdf/**"])])
    proxy, worktree = _arrancar(tmp_path, servidor)
    correr(proxy.avanzar())
    (worktree / "src" / "calc.py").write_text(
        CORREGIDO, encoding="utf-8"
    )  # permitida por la orden, no por el mandato

    with pytest.raises(FueraDeAlcanceMandato) as exc:
        correr(proxy.reportar(tareas_completadas=["T-01"]))

    assert exc.value.rutas == ["src/calc.py"]
    texto = str(exc.value)
    assert "pdf-a" in texto and "src/calc.py" in texto and "src/pdf/**" in texto
    assert "el servidor detendrá la unidad (`fuera-de-alcance`)" in texto
    assert not isinstance(exc.value, FueraDeAlcance)
    assert servidor.reportes == [] and Almacen(worktree).leer().cola_pendiente == []


def test_rutas_dentro_del_mandato_o_sin_rutas_pasan(tmp_path):
    for rutas in (["src/**"], []):
        servidor = ServidorDoble([_orden_con_mandato(rutas)])
        proxy, worktree = _arrancar(tmp_path / ("con" if rutas else "sin"), servidor)
        correr(proxy.avanzar())
        (worktree / "src" / "calc.py").write_text(CORREGIDO, encoding="utf-8")
        assert correr(proxy.reportar(tareas_completadas=["T-01"]))["aceptado"] is True


def test_el_alcance_de_la_orden_sigue_mandando_antes_que_el_del_mandato(tmp_path):
    servidor = ServidorDoble([_orden_con_mandato(["src/pdf/**"])])
    proxy, worktree = _arrancar(tmp_path, servidor)
    correr(proxy.avanzar())
    (worktree / "README.md").write_text("otro\n", encoding="utf-8")

    with pytest.raises(FueraDeAlcance):
        correr(proxy.reportar())


# --- tools MCP ------------------------------------------------------------------------------------------


def _datos(resultado) -> dict:
    assert not resultado.is_error, resultado.content
    return json.loads(resultado.content[0].text)


def _contenido() -> dict:
    return fabricas.mandato_contenido().model_dump(mode="json")


def test_el_arnes_redacta_consulta_y_revoca_pero_no_aprueba(tmp_path):
    servidor = ServidorDoble()
    proxy = crear_proxy(tmp_path, servidor)
    proxy.config = proxy.config.model_copy(update={"url": "https://railspec.example/mcp"})
    huella = fabricas.mandato_contenido().huella()

    async def flujo():
        async with Client(crear_servidor(lambda: proxy)) as cliente:
            nombres = {t.name for t in (await cliente.list_tools()).tools}
            propuesta = _datos(
                await cliente.call_tool("mandate_propose", {"id": "pdf-a", "contenido": _contenido()})
            )
            servidor.aprobar_mandato("pdf-a")
            vista = _datos(await cliente.call_tool("mandate_get", {"id": "pdf-a"}))
            lista = _datos(await cliente.call_tool("mandate_list", {"estado": ["aprobado"]}))
            vacia = _datos(await cliente.call_tool("mandate_list", {"estado": ["revocado"]}))
            revocado = _datos(
                await cliente.call_tool("mandate_revoke", {"id": "pdf-a", "motivo": "Lo pidió la persona."})
            )
            return nombres, propuesta, vista, lista, vacia, revocado

    nombres, propuesta, vista, lista, vacia, revocado = asyncio.run(flujo())

    assert {"mandate_propose", "mandate_get", "mandate_list", "mandate_revoke"} <= nombres
    assert "mandate_approve" not in nombres and "mandate_review" not in nombres
    assert (
        propuesta["huella"] == huella
        and propuesta["estado"] == "propuesto"
        and propuesta["aprobado"] is False
    )
    assert propuesta["consola"] == CONSOLA and "aprobarlo en la consola web" in propuesta["como_aprobar"]
    assert huella in propuesta["como_aprobar"] and "No existe tool" in propuesta["como_aprobar"]
    assert vista["vigente"] is True and vista["huella"] == huella and vista["mandato"]["estado"] == "aprobado"
    assert [m["id"] for m in lista["mandatos"]] == ["pdf-a"] and vacia["mandatos"] == []
    assert revocado["estado"] == "revocado" and revocado["version"] == 3
    llamadas = [t for t, _ in servidor.llamadas]
    assert llamadas == [
        "mandate.propose",
        "mandate.get",
        "mandate.list",
        "mandate.list",
        "mandate.get",
        "mandate.revoke",
    ]
    assert servidor.llamadas[0][1]["alcance"] == {"org": "acme", "workspace": "certificados"}


def test_proponer_edita_con_version_vista_y_rechaza_un_contenido_invalido(tmp_path):
    servidor = ServidorDoble()
    proxy = crear_proxy(tmp_path, servidor)
    contenido = fabricas.mandato_contenido()
    correr(proxy.proponer_mandato("pdf-a", contenido))

    editado = contenido.model_copy(update={"objetivo": "Otro objetivo."})
    respuesta = correr(proxy.proponer_mandato("pdf-a", editado, version_vista=1))
    assert respuesta["version"] == 2 and respuesta["huella"] == editado.huella() != contenido.huella()

    with pytest.raises(ErrorRailspec, match="conflicto-version"):
        correr(proxy.proponer_mandato("pdf-a", editado, version_vista=1))
    invalido = {**_contenido(), "modo": "interactivo"}
    with pytest.raises(ErrorRailspec, match="no cumple el contrato"):
        correr(proxy.proponer_mandato("pdf-a", invalido))
    # Salieron la primera, la edición y la del conflicto; el contenido inválido no sale del proxy.
    assert [t for t, _ in servidor.llamadas].count("mandate.propose") == 3


def test_por_mcp_un_contenido_invalido_llega_como_error_de_la_tool(tmp_path):
    proxy = crear_proxy(tmp_path, ServidorDoble())

    async def flujo():
        async with Client(crear_servidor(lambda: proxy)) as cliente:
            return await cliente.call_tool(
                "mandate_propose", {"id": "pdf-a", "contenido": {**_contenido(), "modo": "interactivo"}}
            )

    assert asyncio.run(flujo()).is_error is True


def test_aprobar_y_revisar_no_existen_para_el_proxy(tmp_path):
    assert TOOLS["mandate.approve"].superficies == {Superficie.http}
    assert TOOLS["mandate.review"].superficies == {Superficie.http}
    assert {nombre_mcp(n) for n in TOOLS if n.startswith("mandate.")} >= {
        "mandate_propose",
        "mandate_get",
        "mandate_list",
        "mandate_revoke",
    }
    servidor = ServidorDoble()
    proxy = crear_proxy(tmp_path, servidor)
    for nombre in ("mandate.approve", "mandate.review"):
        definicion = TOOLS[nombre]
        entrada = definicion.entrada.model_construct()
        with pytest.raises(ValueError, match="no se expone por MCP"):
            correr(ClienteServidor(servidor).llamar(nombre, entrada, definicion.salida))
    assert not hasattr(proxy, "aprobar_mandato") and not hasattr(proxy, "revisar_decision")
    assert servidor.llamadas == []


def test_los_permisos_de_los_adaptadores_cubren_las_tools_del_mandato():
    assert {"mandate_propose", "mandate_get", "mandate_list"} <= set(adaptadores.TOOLS_AUTOMATICAS)
    assert "mandate_revoke" in adaptadores.TOOLS_HUMANAS  # detiene todo el mandato: pregunta siempre
    assert not {"mandate_approve", "mandate_review"} & {
        *adaptadores.TOOLS_AUTOMATICAS,
        *adaptadores.TOOLS_HUMANAS,
    }


# --- CLI ------------------------------------------------------------------------------------------------


@pytest.fixture
def en_cli(tmp_path, monkeypatch):
    def preparar(servidor: ServidorDoble, url: str | None = "https://railspec.example/mcp"):
        proxy = crear_proxy(tmp_path / "repo", servidor)
        if url:
            proxy.config = proxy.config.model_copy(update={"url": url})
        monkeypatch.setattr(cli, "crear_proxy", lambda raiz: proxy)
        return proxy

    return preparar


def ejecutar(proxy, capsys, *args: str) -> tuple[int, str, str]:
    codigo = cli.main(["--repo", str(proxy.raiz), *args])
    captura = capsys.readouterr()
    return codigo, captura.out, captura.err


def test_cli_proponer_desde_json_imprime_la_huella_y_manda_a_la_consola(en_cli, capsys, tmp_path):
    servidor = ServidorDoble()
    proxy = en_cli(servidor)
    archivo = tmp_path / "pdf-a.json"
    archivo.write_text(json.dumps(_contenido()), encoding="utf-8")

    codigo, salida, _ = ejecutar(proxy, capsys, "mandato", "proponer", str(archivo))

    datos = json.loads(salida)
    assert codigo == 0 and datos["mandato"] == "pdf-a"  # el id sale del nombre del archivo
    assert datos["huella"] == fabricas.mandato_contenido().huella() and datos["aprobado"] is False
    assert datos["consola"] == CONSOLA
    assert "aprobarlo en la consola web" in datos["como_aprobar"] and CONSOLA in datos["como_aprobar"]


def test_cli_proponer_con_id_y_sin_url_derivable(en_cli, capsys, tmp_path):
    servidor = ServidorDoble()
    proxy = en_cli(servidor, url="https://railspec.example/otra-ruta")
    archivo = tmp_path / "borrador.json"
    archivo.write_text(json.dumps(_contenido()), encoding="utf-8")

    codigo, salida, _ = ejecutar(proxy, capsys, "mandato", "proponer", str(archivo), "--id", "migracion-pdf")

    datos = json.loads(salida)
    assert codigo == 0 and datos["mandato"] == "migracion-pdf" and "consola" not in datos
    assert "la consola web del servidor" in datos["como_aprobar"]


def test_cli_proponer_desde_yaml(en_cli, capsys, tmp_path):
    yaml = pytest.importorskip("yaml")
    proxy = en_cli(ServidorDoble())
    archivo = tmp_path / "pdf-a.yaml"
    archivo.write_text(yaml.safe_dump(_contenido(), allow_unicode=True), encoding="utf-8")

    codigo, salida, _ = ejecutar(proxy, capsys, "mandato", "proponer", str(archivo))

    assert codigo == 0 and json.loads(salida)["huella"] == fabricas.mandato_contenido().huella()


def test_cli_proponer_yaml_sin_pyyaml_pide_json(en_cli, capsys, tmp_path, monkeypatch):
    proxy = en_cli(ServidorDoble())
    archivo = tmp_path / "pdf-a.yml"
    archivo.write_text("titulo: x\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "yaml", None)  # `import yaml` falla como sin pyyaml

    codigo, salida, error = ejecutar(proxy, capsys, "mandato", "proponer", str(archivo))

    assert codigo == 1 and salida == ""
    assert "pyyaml no está instalado" in error and "JSON" in error and "Traceback" not in error


@pytest.mark.parametrize(
    ("nombre", "texto", "mensaje"),
    [
        ("pdf-a.json", "{no es json", "no es JSON válido"),
        ("pdf-a.json", json.dumps({"titulo": "x"}), "no cumple el contrato"),
        ("Pdf A.json", "{}", "no es un id de mandato válido"),
        ("inexistente.json", None, "No se pudo leer"),
    ],
)
def test_cli_proponer_con_errores_sale_en_una_linea_sin_traza(
    en_cli, capsys, tmp_path, nombre, texto, mensaje
):
    servidor = ServidorDoble()
    proxy = en_cli(servidor)
    archivo = tmp_path / nombre
    if texto is not None:
        archivo.write_text(texto, encoding="utf-8")

    codigo, salida, error = ejecutar(proxy, capsys, "mandato", "proponer", str(archivo))

    assert codigo == 1 and salida == "" and mensaje in error and "Traceback" not in error
    assert servidor.llamadas == []


def test_cli_estado_listar_y_revocar(en_cli, capsys):
    servidor = ServidorDoble()
    proxy = en_cli(servidor)
    correr(proxy.proponer_mandato("pdf-a", fabricas.mandato_contenido()))
    servidor.aprobar_mandato("pdf-a")

    codigo, salida, _ = ejecutar(proxy, capsys, "mandato", "estado", "pdf-a")
    datos = json.loads(salida)
    assert codigo == 0 and datos["vigente"] is True and datos["mandato"]["id"] == "pdf-a"

    for args in (("estado",), ("listar",), ("listar", "--estado", "aprobado")):
        codigo, salida, _ = ejecutar(proxy, capsys, "mandato", *args)
        assert codigo == 0 and [m["id"] for m in json.loads(salida)["mandatos"]] == ["pdf-a"]
    codigo, salida, _ = ejecutar(proxy, capsys, "mandato", "listar", "--estado", "revocado")
    assert codigo == 0 and json.loads(salida)["mandatos"] == []

    codigo, salida, _ = ejecutar(
        proxy, capsys, "mandato", "revocar", "pdf-a", "--motivo", "Ya no hace falta."
    )
    assert codigo == 0 and json.loads(salida)["estado"] == "revocado"
    assert servidor.mandatos["pdf-a"].estado == EstadoMandato.revocado
    assert servidor.mandatos["pdf-a"].revocacion.motivo == "Ya no hace falta."


def test_cli_revocar_exige_motivo_y_estado_de_uno_que_no_existe_sale_con_1(en_cli, capsys):
    proxy = en_cli(ServidorDoble())

    with pytest.raises(SystemExit) as exc:
        ejecutar(proxy, capsys, "mandato", "revocar", "pdf-a")
    assert exc.value.code == 2
    capsys.readouterr()

    codigo, salida, error = ejecutar(proxy, capsys, "mandato", "estado", "pdf-a")
    assert codigo == 1 and salida == "" and "no-encontrado" in error


def test_cli_no_tiene_aprobar_ni_revisar(en_cli, capsys):
    proxy = en_cli(ServidorDoble())
    for accion in ("aprobar", "revisar"):
        with pytest.raises(SystemExit) as exc:
            ejecutar(proxy, capsys, "mandato", accion, "pdf-a")
        assert exc.value.code == 2
        assert "invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("url", "esperada"),
    [
        ("https://railspec.example/mcp", CONSOLA),
        ("https://railspec.example/mcp/", CONSOLA),
        ("http://localhost:8080/mcp", "http://localhost:8080/consola/"),
        ("https://acme.example/railspec/mcp", "https://acme.example/railspec/consola/"),
        ("https://railspec.example/otra", None),
        ("https://railspec.example", None),
        ("railspec", None),
        ("", None),
        (None, None),
    ],
)
def test_la_consola_se_deriva_del_endpoint_mcp_solo_si_se_puede(url, esperada):
    assert config.url_consola(url) == esperada


# --- plantillas de los adaptadores -----------------------------------------------------------------------


def test_las_plantillas_explican_el_mandato_sin_ofrecer_aprobarlo():
    comando = adaptadores.plantilla("comando.md")
    skill = adaptadores.plantilla("skill-bucle.md")
    assert "id de un mandato que ya existe y está aprobado" in comando and "`mandate_get`" in comando
    assert "nunca aprobarlo" in comando and "$ARGUMENTS" in comando
    for fragmento in (
        "`mandato-parado`",
        "`causa_parada`",
        "`unidad-amparada-fallida`",
        "`decisiones`",
        "`reservada`",
        "`bloqueado`",
        "nunca se aprueba desde el arnés",
        "`mandate_revoke`",
    ):
        assert fragmento in skill, fragmento
    for causa in (
        CausaParada.fuera_de_alcance,
        CausaParada.decision_reservada,
        CausaParada.reintentos_agotados,
        CausaParada.gate_escalado,
    ):
        assert f"`{causa.value}`" in skill
    for nombre in ("reglas.md", "reglas-usuario.md"):
        reglas = adaptadores.plantilla(nombre)
        assert (
            "mandato" in reglas and "nunca aprobarlo ni renovarlo" in reglas and "`mandato-parado`" in reglas
        )
        assert "`bloqueado`" in reglas and "`mandate_propose`" in reglas


def test_las_plantillas_solo_nombran_tools_que_el_proxy_expone(tmp_path):
    import re

    async def nombres():
        async with Client(crear_servidor(lambda: None)) as cliente:
            return {t.name for t in (await cliente.list_tools()).tools}

    expuestas = asyncio.run(nombres())
    for nombre in ("comando.md", "skill-bucle.md", "reglas.md", "reglas-usuario.md"):
        texto = adaptadores.plantilla(nombre)
        citadas = set(re.findall(r"`((?:mandate|unit|graph|code|insumo)_[a-z_]+)`", texto))
        assert citadas <= expuestas, (nombre, citadas - expuestas)
        assert "mandate_approve" not in texto and "mandate_review" not in texto
