"""Pruebas de los contratos v1: deriva de esquemas, ejemplos e invariantes."""

from __future__ import annotations

import base64
import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fabricas as f
import jsonschema
import pytest
from pydantic import TypeAdapter, ValidationError
from railspec.contracts import esquemas
from railspec.contracts.chat import MensajeChat, ReglaGate, VeredictoGateSalida
from railspec.contracts.comun import Actor, TipoActor, verificar_texto_plano
from railspec.contracts.estado import EstadoLocal, EstadoUnidad, ResultadoGate
from railspec.contracts.eventos import EventoSync
from railspec.contracts.insumo import Insumo
from railspec.contracts.orden import OrdenDeTrabajo
from railspec.contracts.portabilidad import PaqueteUnidad
from railspec.contracts.reporte import ReporteOrden
from railspec.contracts.repositorio import AsignacionRol, RegistroAuditoria, VinculoRepositorio
from railspec.contracts.snapshot import Snapshot, id_simbolo
from railspec.contracts.tools import (
    TOOLS,
    Efecto,
    GraphIndexEntrada,
    GraphQueryEntrada,
    GraphQuerySalida,
    Superficie,
    SyncPullEntrada,
    SyncPullSalida,
    SyncPushEntrada,
    UnitApproveEntrada,
    UnitExportSalida,
    UnitImportEntrada,
    UnitSetModeEntrada,
    UnitStartEntrada,
    resolver_tool,
    tools_para,
)

RAIZ = Path(__file__).parents[1]
ESQUEMAS = RAIZ / "schemas" / "v1"
EJEMPLOS = RAIZ / "examples" / "v1"


# --- Deriva ------------------------------------------------------------------------


def test_esquemas_versionados_sin_deriva() -> None:
    generados = esquemas.generar()
    en_disco = {p.name: p.read_text(encoding="utf-8") for p in ESQUEMAS.glob("*.json")}
    assert set(en_disco) == set(generados), "regenera con python -m railspec.contracts.esquemas"
    for nombre, contenido in generados.items():
        assert en_disco[nombre] == contenido, f"{nombre} derivó: regenera los esquemas"


@pytest.mark.parametrize("nombre", sorted(f.EJEMPLOS))
def test_ejemplos_sin_deriva(nombre: str) -> None:
    assert (EJEMPLOS / f"{nombre}.json").read_text(encoding="utf-8") == f.serializar(nombre)


@pytest.mark.parametrize("nombre", sorted(esquemas.MENSAJES))
def test_ejemplo_valida_con_json_schema_y_pydantic(nombre: str) -> None:
    datos = json.loads((EJEMPLOS / f"{nombre}.json").read_text(encoding="utf-8"))
    esquema = json.loads((ESQUEMAS / f"{nombre}.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(esquema)
    jsonschema.Draft202012Validator(esquema).validate(datos)
    modelo = TypeAdapter(esquemas.MENSAJES[nombre]).validate_python(datos)
    assert modelo.model_dump(mode="json", exclude_none=True) == datos


def test_todo_mensaje_tiene_ejemplo() -> None:
    assert set(esquemas.MENSAJES) == set(f.EJEMPLOS)


# --- Invariantes (cada caso rompe una regla y debe rechazarse) ----------------------


def _dump(fabrica: Callable[[], Any]) -> dict[str, Any]:
    return copy.deepcopy(fabrica().model_dump(mode="json"))


def _rechaza(tipo: Any, datos: dict[str, Any], fragmento: str) -> None:
    with pytest.raises(ValidationError) as exc:
        TypeAdapter(tipo).validate_python(datos)
    assert fragmento in str(exc.value)


def test_snapshot_restringido_no_lleva_texto_de_codigo() -> None:
    d = _dump(f.snapshot)
    d["diff"] = "--- a/src/pdf.py\n+++ b/src/pdf.py\n"
    _rechaza(Snapshot, d, "nivel restringido")


def test_snapshot_interno_admite_diff_y_fragmentos_de_archivos_tocados() -> None:
    d = _dump(f.snapshot)
    d.update(
        nivel_codigo="interno",
        diff="+x",
        fragmentos=[{"ruta": "src/pdf.py", "sha256": f.sha("x"), "texto": "x"}],
    )
    Snapshot.model_validate(d)
    d["fragmentos"][0]["ruta"] = "src/otro.py"
    _rechaza(Snapshot, d, "fuera de los archivos tocados")


def test_snapshot_con_secretos_es_invalido() -> None:
    d = _dump(f.snapshot)
    d["escaneo_secretos"]["hallazgos"] = 1
    _rechaza(Snapshot, d, "less than or equal to 0")


def test_snapshot_solo_hashes_no_lleva_delta() -> None:
    d = _dump(f.snapshot)
    d["modo_delta"] = "solo-hashes"
    _rechaza(Snapshot, d, "solo-hashes")


def test_embedding_de_longitud_incorrecta() -> None:
    d = _dump(f.snapshot)
    d["delta_indice"]["embeddings"][0]["vector_b64"] = "AAAA"
    _rechaza(Snapshot, d, "se esperaban 768")


def test_ruta_con_punto_punto_se_rechaza() -> None:
    d = _dump(f.snapshot)
    d["archivos"][0]["ruta"] = "../etc/passwd"
    _rechaza(Snapshot, d, "pattern")


def test_orden_implementar_exige_alcance_y_criterios_conocidos() -> None:
    d = _dump(f.orden)
    d["alcance"]["permitidos"] = []
    _rechaza(OrdenDeTrabajo, d, "alcance.permitidos")
    d = _dump(f.orden)
    d["tareas"][0]["criterios"] = ["CA-09"]
    _rechaza(OrdenDeTrabajo, d, "CA-09")


def test_orden_validar_exige_comando() -> None:
    d = _dump(f.orden)
    for campo in ("grupo", "tareas", "complejidad"):
        d.pop(campo)
    d.update(
        tipo="validar",
        comando_validacion=None,
        reporte_requerido={"snapshot": False, "validacion": True, "artefacto": False},
    )
    _rechaza(OrdenDeTrabajo, d, "comando_validacion")


def test_reporte_con_snapshot_de_otro_commit() -> None:
    d = _dump(f.reporte)
    d["base_commit"] = "c" * 40
    _rechaza(ReporteOrden, d, "otro base_commit")


def test_reporte_bloqueado_exige_motivo() -> None:
    d = _dump(f.reporte)
    d["resultado"] = "bloqueado"
    _rechaza(ReporteOrden, d, "necesita motivo")


def test_artefacto_con_hash_incorrecto() -> None:
    d = _dump(f.reporte)
    d["artefacto"] = {"tipo": "spec", "contenido": "# Spec", "sha256": f.sha("otro")}
    _rechaza(ReporteOrden, d, "sha256 no coincide")


def test_evento_en_direccion_equivocada() -> None:
    d = _dump(f.evento)
    d["direccion"] = "remoto-a-local"
    _rechaza(EventoSync, d, "viaja local-a-remoto")


def _gate(**cambios: Any) -> dict[str, Any]:
    base = _dump(f.estado_unidad)["gates"]["spec"]
    base.update(cambios)
    return base


HALLAZGO_ALTO = {
    "id": "H-1",
    "gate": "spec",
    "lente": "correctitud",
    "severidad": "alta",
    "titulo": "Falta criterio",
    "cita": {"seccion": "Criterios"},
    "evidencia": "No hay CA para la firma.",
}


def test_gate_nunca_aprueba_por_agotamiento() -> None:
    _rechaza(ResultadoGate, _gate(hallazgos=[HALLAZGO_ALTO]), "debe escalar")
    refutado = dict(HALLAZGO_ALTO, refutado=True)
    ResultadoGate.model_validate(_gate(hallazgos=[refutado]))


def test_gate_nunca_critica_de_memoria() -> None:
    _rechaza(ResultadoGate, _gate(gobernanza_consultada="no"), "sin-gobernanza")
    ResultadoGate.model_validate(
        _gate(veredicto="escalado", causa="sin-gobernanza", gobernanza_consultada="no")
    )


def test_gate_escalado_exige_causa() -> None:
    _rechaza(ResultadoGate, _gate(veredicto="escalado"), "necesita causa")


def test_rehabilitacion_exige_humano() -> None:
    rehab = {"actor": f.SERVIDOR.model_dump(mode="json"), "en": f.T0.isoformat(), "motivo": "ok"}
    _rechaza(
        ResultadoGate,
        _gate(veredicto="escalado", causa="hallazgos-sin-resolver", rehabilitado=rehab),
        "actor humano",
    )


def test_unidad_done_exige_gate_de_codigo() -> None:
    d = _dump(f.estado_unidad)
    d.update(fase="done", estado="completado", orden_vigente=None)
    _rechaza(EstadoUnidad, d, "gate de codigo")
    d["gates"]["codigo"] = _gate()
    EstadoUnidad.model_validate(d)


def test_integracion_solo_tras_cierre() -> None:
    d = _dump(f.estado_unidad)
    d["integracion"] = {
        "actor": f.JULIAN_WEB.model_dump(mode="json"),
        "en": f.T0.isoformat(),
        "especificacion_viva": "specs/certificados.md",
        "pr_url": "https://github.com/acme/certificados-api/pull/7",
    }
    _rechaza(EstadoUnidad, d, "solo una unidad cerrada")


def test_unidad_nace_interactivo() -> None:
    d = _dump(f.estado_unidad)
    d["modo"] = "supervisado"
    _rechaza(EstadoUnidad, d, "modo_conversion")


def test_orden_y_checkpoint_son_excluyentes() -> None:
    d = _dump(f.estado_unidad)
    d["checkpoint_pendiente"] = {
        "id": str(f.uid(11)),
        "tipo": "aprobar-plan",
        "fase": "plan",
        "pregunta": "¿Apruebas el plan?",
        "abierto_en": f.T0.isoformat(),
    }
    _rechaza(EstadoUnidad, d, "a la vez orden vigente y checkpoint")


def test_cola_local_solo_crece_y_solo_sube() -> None:
    d = _dump(f.estado_local)
    d["ultima_secuencia_confirmada"] = 7
    _rechaza(EstadoLocal, d, "no crecientes")


def test_actor_humano_exige_identidad_github() -> None:
    _rechaza(Actor, {"tipo": "humano", "canal": "consola", "login": "x"}, "github_id")
    _rechaza(Actor, {"tipo": "servicio", "canal": "ci"}, "OIDC")


def test_aprobar_con_cambios_exige_comentario() -> None:
    datos = {
        "unidad": f.ALCANCE_UNIDAD.model_dump(),
        "checkpoint": str(f.uid(11)),
        "decision": "cambios-solicitados",
    }
    _rechaza(UnitApproveEntrada, datos, "necesita comentario")


def test_asignacion_org_admin_va_a_nivel_organizacion() -> None:
    d = _dump(f.asignacion_rol)
    d["rol"] = "org-admin"
    _rechaza(AsignacionRol, d, "org-admin")


def test_vinculo_restringido_no_usa_modelos_fuera_de_azure() -> None:
    d = _dump(f.vinculo)
    d["chat_contexto_codigo"]["hosting"] = "cualquiera"
    _rechaza(VinculoRepositorio, d, "zona de datos")
    d = _dump(f.vinculo)
    d["chat_contexto_codigo"]["fragmentos_en_respuesta"] = True
    _rechaza(VinculoRepositorio, d, "nunca salen fragmentos")


def test_auditoria_de_llamada_a_modelo_completa() -> None:
    d = _dump(f.auditoria)
    d["region"] = None
    _rechaza(RegistroAuditoria, d, "region")


# --- Chat, gate de salida e insumo (R6, R9, R10) ------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "mira esto:\n```python\nprint(1)\n```",
        "usa <script>alert(1)</script>",
        "detalles en https://evil.example/x?d=",
        "![img](data:image/png;base64,AAAA)",
        "abre www.evil.example",
    ],
)
def test_texto_plano_rechaza_codigo_html_y_urls(texto: str) -> None:
    with pytest.raises(ValueError):
        verificar_texto_plano(texto, "t")


def test_texto_plano_admite_explicaciones_con_simbolos() -> None:
    verificar_texto_plano("pdf.emitir llama a firma.aplicar en src/pdf.py (líneas 10 a 42).", "t")


def test_respuesta_del_chat_con_bloque_de_codigo_es_invalida() -> None:
    d = _dump(f.mensaje_chat)
    d["respuesta"]["afirmaciones"][0]["texto"] = "```\ndef emitir(): pass\n```"
    _rechaza(MensajeChat, d, "bloques de código")


def test_veredicto_evalua_todas_las_reglas_y_es_coherente() -> None:
    d = _dump(f.veredicto_permitido)
    d["reglas"] = d["reglas"][:-1]
    _rechaza(VeredictoGateSalida, d, "at least")
    d = _dump(f.veredicto_permitido)
    d["reglas"][1]["resultado"] = "bloquea"
    _rechaza(VeredictoGateSalida, d, "permitido debe ser falso")


def test_respuesta_bloqueada_no_guarda_texto_del_modelo() -> None:
    d = _dump(f.mensaje_chat)
    d["veredicto_gate"]["permitido"] = False
    d["veredicto_gate"]["reglas"][1].update(resultado="bloquea", huellas_coincidentes=[f.sha("h")])
    _rechaza(MensajeChat, d, "nunca se guarda")
    d["respuesta"] = None
    d["conservar_en_insumo"] = False
    d["aviso_bloqueo"] = [ReglaGate.huella_contexto.value]
    MensajeChat.model_validate(d)


def test_insumo_protegido_por_hash_y_sin_codigo() -> None:
    d = _dump(f.insumo)
    d["objetivo"] = "Firmar los PDF"
    _rechaza(Insumo, d, "sha256 no coincide")
    d = _dump(f.insumo)
    d["hallazgos"][0]["texto"] = "<b>x</b>"
    _rechaza(Insumo, d, "HTML")


def test_insumo_exige_gate_permitido() -> None:
    d = _dump(f.insumo)
    d["veredicto_gate"]["permitido"] = False
    _rechaza(Insumo, d, "permitido")


# --- Registro de tools (R1) -----------------------------------------------------------


def test_registro_de_tools() -> None:
    assert set(TOOLS) == {
        "unit.start",
        "unit.advance",
        "unit.report",
        "unit.approve",
        "unit.integrate",
        "unit.status",
        "unit.list",
        "graph.query",
        "code.read",
        "insumo.get",
        "telemetry.query",
        "graph.index",
        "unit.set_mode",
        "sync.pull",
        "sync.push",
        "unit.import",
        "unit.export",
    }
    assert all(t.efecto == Efecto.lectura for t in tools_para(Superficie.chat))
    assert [t.nombre for t in TOOLS.values() if Superficie.chat in t.superficies and t.nombre == "code.read"]
    assert "code.read" not in {t.nombre for t in tools_para(Superficie.http)}
    assert "code.read" not in {t.nombre for t in tools_para(Superficie.mcp)}


def test_code_read_marca_codigo_interno() -> None:
    assert TOOLS["code.read"].campos_codigo_interno() == ["fragmentos[].texto"]
    for nombre, tool in TOOLS.items():
        if nombre != "code.read" and Superficie.chat in tool.superficies:
            assert tool.campos_codigo_interno() == [], nombre


def test_un_tool_de_escritura_no_puede_exponerse_al_chat() -> None:
    from railspec.contracts.tools import ToolDef

    base = TOOLS["unit.start"]
    with pytest.raises(ValidationError):
        ToolDef(**{**base.__dict__, "superficies": frozenset({Superficie.chat})})


# --- Contrato 1.1: pedidos del hilo del grafo -----------------------------------------------


def _busqueda(**cambios: Any) -> dict[str, Any]:
    consulta = {"verbo": "search", "texto": "firma del pdf", "semantica": True}
    consulta.update(cambios)
    return {"alcance": f.ALCANCE_WS.model_dump(), "consulta": consulta}


VECTOR = base64.b64encode(bytes(768)).decode()


def test_busqueda_con_vector_calculado_en_local() -> None:
    GraphQueryEntrada.model_validate(_busqueda(vector_b64=VECTOR, modelo_embedding="nomic-embed-code"))
    GraphQueryEntrada.model_validate(_busqueda())  # sin vector: el servidor codifica o cae a texto


def test_vector_de_busqueda_exige_semantica_modelo_y_longitud() -> None:
    _rechaza(
        GraphQueryEntrada,
        _busqueda(semantica=False, vector_b64=VECTOR, modelo_embedding="nomic-embed-code"),
        "semantica",
    )
    _rechaza(GraphQueryEntrada, _busqueda(vector_b64=VECTOR), "modelo_embedding")
    _rechaza(
        GraphQueryEntrada,
        _busqueda(vector_b64="AAAA", modelo_embedding="nomic-embed-code"),
        "se esperaban 768",
    )


def test_graph_index_solo_ci_por_http() -> None:
    tool = TOOLS["graph.index"]
    assert tool.efecto == Efecto.escritura
    assert tool.superficies == {Superficie.http}
    assert tool.tipos_actor == {TipoActor.servicio}
    from railspec.contracts.tools import ToolDef

    with pytest.raises(ValidationError):
        ToolDef(**{**tool.__dict__, "tipos_actor": frozenset(TipoActor)})


def test_graph_index_lotes_coherentes() -> None:
    delta = f.snapshot().delta_indice.model_dump(mode="json")
    base = {
        "alcance": f.ALCANCE_REPO.model_dump(),
        "rama": "main",
        "commit": f.BASE,
        "lote": 1,
        "lotes": 2,
        "delta": delta,
    }
    GraphIndexEntrada.model_validate(base)
    _rechaza(GraphIndexEntrada, {**base, "lote": 3}, "lote mayor")
    _rechaza(GraphIndexEntrada, {**base, "commit_anterior": f.BASE}, "igual a commit")


def test_id_simbolo_es_la_convencion_publicada() -> None:
    assert id_simbolo("certificados-api", "src/pdf.py", "funcion", "pdf.emitir") == f.SIMBOLO_ID


def test_arista_hacia_otro_repositorio_del_workspace() -> None:
    d = _dump(f.snapshot)
    arista = d["delta_indice"]["aristas_agregadas"][0]
    assert arista["repositorio_destino"] == "reporteria"
    assert arista["destino"] == id_simbolo("reporteria", "src/informes.py", "funcion", "informes.registrar")


# --- Contrato 1.2: pedidos del hilo del motor ----------------------------------------------


def _conversion(de: str, a: str, tras: str | None) -> dict[str, Any]:
    return {
        "de": de,
        "a": a,
        "actor": f.JULIAN.model_dump(mode="json"),
        "en": f.T0.isoformat(),
        "motivo": "El spec quedó aprobado; el resto puede ir sin checkpoints.",
        "tras": tras,
    }


def test_gate_escalado_sin_convergencia() -> None:
    ResultadoGate.model_validate(
        _gate(veredicto="escalado", causa="sin-convergencia", iteraciones=2, hallazgos=[HALLAZGO_ALTO])
    )
    _rechaza(ResultadoGate, _gate(veredicto="escalado", causa="sin-convergencia", iteraciones=0), "iteración")


def test_estado_guarda_el_pedido_original() -> None:
    assert _dump(f.estado_unidad)["pedido"].startswith("Quiero")


def test_conversion_de_modo_registrada() -> None:
    d = _dump(f.estado_unidad)
    d.update(modo="semi-autonomo", modo_conversion=[_conversion("interactivo", "semi-autonomo", "spec")])
    EstadoUnidad.model_validate(d)
    d["modo_conversion"][0]["actor"] = f.SERVIDOR.model_dump(mode="json")
    _rechaza(EstadoUnidad, d, "actor humano")


def test_solo_la_primera_conversion_puede_no_tener_fase() -> None:
    d = _dump(f.estado_unidad)
    d.update(
        modo="interactivo",
        modo_conversion=[
            _conversion("interactivo", "semi-autonomo", None),
            _conversion("semi-autonomo", "interactivo", None),
        ],
    )
    _rechaza(EstadoUnidad, d, "primera conversión")


def test_supervisado_exige_mandato() -> None:
    d = _dump(f.estado_unidad)
    d.update(modo="supervisado", modo_conversion=[_conversion("interactivo", "supervisado", "spec")])
    _rechaza(EstadoUnidad, d, "exige un mandato")
    d["unidad"]["plan"] = "certificados-q4"
    EstadoUnidad.model_validate(d)
    entrada = {
        "unidad": f.ALCANCE_UNIDAD.model_dump(),
        "modo": "desatendido",
        "motivo": "Lote nocturno",
        "version_vista": 12,
    }
    _rechaza(UnitSetModeEntrada, entrada, "exige un mandato")


def test_unit_set_mode_solo_humanos() -> None:
    tool = TOOLS["unit.set_mode"]
    assert tool.efecto == Efecto.escritura
    assert tool.tipos_actor == {TipoActor.humano}
    assert Superficie.chat not in tool.superficies


def test_unit_start_con_modo_inicial() -> None:
    base = {
        "alcance": f.ALCANCE_WS.model_dump(),
        "repositorios": [{"repositorio": "certificados-api", "rama": "main", "base_commit": f.BASE}],
        "titulo": "Firmar PDF",
        "pedido": "Firmar los certificados",
        "version_contrato_cliente": "1.2",
    }
    UnitStartEntrada.model_validate({**base, "modo": "semi-autonomo"})
    _rechaza(UnitStartEntrada, {**base, "modo": "supervisado"}, "exige un mandato")


# --- Contrato 1.3: pedidos del hilo del proxy local ---------------------------------------


def _subida(n: int, secuencia: int) -> dict[str, Any]:
    return {
        "id": str(f.uid(100 + n)),
        "secuencia": secuencia,
        "emitido_en": "2026-09-30T20:00:00Z",
        "carga": {
            "tipo": "commit.empujado",
            "repositorio": "certificados-api",
            "rama": "railspec/0001-emitir-pdf",
            "commit": f.BASE,
        },
    }


def test_sync_push_sube_commit_empujado_sin_actor() -> None:
    entrada = {"unidad": f.ALCANCE_UNIDAD.model_dump(), "eventos": [_subida(1, 8), _subida(2, 9)]}
    SyncPushEntrada.model_validate(entrada)
    _rechaza(SyncPushEntrada, {**entrada, "eventos": [_subida(1, 9), _subida(2, 8)]}, "crecientes")
    _rechaza(SyncPushEntrada, {**entrada, "eventos": [_subida(1, 8), _subida(1, 9)]}, "repetidos")
    con_actor = {**_subida(1, 8), "actor": f.JULIAN.model_dump()}
    _rechaza(SyncPushEntrada, {**entrada, "eventos": [con_actor]}, "actor")
    remoto = {
        **_subida(1, 8),
        "carga": {"tipo": "orden.emitida", "orden_id": str(f.uid(9)), "secuencia_orden": 1},
    }
    _rechaza(SyncPushEntrada, {**entrada, "eventos": [remoto]}, "orden.emitida")


def test_sync_pull_solo_remoto_a_local_en_orden() -> None:
    SyncPullEntrada.model_validate({"unidad": f.ALCANCE_UNIDAD.model_dump(), "desde": 0})
    remoto = {
        **f.evento().model_dump(mode="json"),
        "direccion": "remoto-a-local",
        "carga": {"tipo": "veredicto.emitido", "gate": "codigo", "veredicto": "aprobado"},
    }
    SyncPullSalida.model_validate({"eventos": [remoto], "ultima_secuencia": 7, "hay_mas": False})
    _rechaza(
        SyncPullSalida, {"eventos": [remoto], "ultima_secuencia": 3, "hay_mas": False}, "ultima_secuencia"
    )
    siguiente = {**remoto, "id": str(f.uid(50)), "secuencia": 6}
    _rechaza(
        SyncPullSalida,
        {"eventos": [remoto, siguiente], "ultima_secuencia": 7, "hay_mas": False},
        "crecientes",
    )
    local = f.evento().model_dump(mode="json")
    _rechaza(SyncPullSalida, {"eventos": [local], "ultima_secuencia": 7, "hay_mas": False}, "remoto→local")


def test_sync_tools_solo_mcp_y_sin_servicios() -> None:
    for nombre in ("sync.pull", "sync.push"):
        tool = TOOLS[nombre]
        assert tool.superficies == {Superficie.mcp}
        assert TipoActor.servicio not in tool.tipos_actor
    assert TOOLS["sync.pull"].efecto == Efecto.lectura
    assert TOOLS["sync.push"].efecto == Efecto.escritura


def test_alias_mcp_deterministas_y_unicos() -> None:
    import re

    alias = {t.nombre_mcp for t in TOOLS.values()}
    assert len(alias) == len(TOOLS)
    for tool in TOOLS.values():
        assert re.fullmatch(r"[a-z]+_[a-z_]+", tool.nombre_mcp)
        assert tool.nombre_mcp == tool.nombre.replace(".", "_")
        assert resolver_tool(tool.nombre) is tool
        assert resolver_tool(tool.nombre_mcp) is tool
        assert tool.manifiesto()["mcp_name"] == tool.nombre_mcp
    assert resolver_tool("unit-start") is None


# --- Contrato 1.4: pedidos del hilo del grafo central ------------------------------------


def _consulta(consulta: dict[str, Any], unidad: str | None = "0001-emitir-pdf") -> dict[str, Any]:
    return {"alcance": f.ALCANCE_WS.model_dump(), "unidad": unidad, "consulta": consulta}


def test_graph_impact_exige_unidad() -> None:
    entrada = GraphQueryEntrada.model_validate(_consulta({"verbo": "impact"}))
    assert entrada.consulta.profundidad == 3
    _rechaza(GraphQueryEntrada, _consulta({"verbo": "impact"}, unidad=None), "impact exige unidad")
    _rechaza(GraphQueryEntrada, _consulta({"verbo": "impact", "profundidad": 6}), "less than or equal")


def test_graph_trace_criterio_o_simbolo() -> None:
    GraphQueryEntrada.model_validate(_consulta({"verbo": "trace", "criterio": "CA-03"}))
    GraphQueryEntrada.model_validate(_consulta({"verbo": "trace", "simbolo": f.SIMBOLO_ID}, unidad=None))
    _rechaza(
        GraphQueryEntrada,
        _consulta({"verbo": "trace", "criterio": "CA-03"}, unidad=None),
        "trace por criterio exige unidad",
    )
    _rechaza(GraphQueryEntrada, _consulta({"verbo": "trace"}), "exactamente uno")
    _rechaza(
        GraphQueryEntrada,
        _consulta({"verbo": "trace", "criterio": "CA-03", "simbolo": f.SIMBOLO_ID}),
        "exactamente uno",
    )


def test_resultado_grafo_admite_ref_criterio() -> None:
    salida = GraphQuerySalida.model_validate(
        {
            "resultados": [
                {
                    "ref": {
                        "tipo": "criterio",
                        "workspace": "certificados",
                        "unidad": "0001-emitir-pdf",
                        "criterio": "CA-03",
                    }
                }
            ],
            "commits": {"certificados-api": f.BASE},
        }
    )
    assert salida.resultados[0].ref.tipo == "criterio"


# --- Contrato 1.4: portabilidad (unit.import y unit.export) ------------------------------


def test_paquete_fase_retomar_coherente_con_artefactos() -> None:
    d = _dump(f.paquete_unidad)
    _rechaza(PaqueteUnidad, {**d, "fase_retomar": "implement"}, "fase_retomar debe ser una de: tasks")
    sin_spec = {**d, "artefactos": {"plan": d["artefactos"]["plan"]}}
    _rechaza(PaqueteUnidad, sin_spec, "plan sin spec")
    cambiado = copy.deepcopy(d)
    cambiado["artefactos"]["spec"] = d["artefactos"]["plan"]
    _rechaza(PaqueteUnidad, cambiado, "es de tipo plan")
    _rechaza(PaqueteUnidad, {**d, "modo": "supervisado"}, "exige un mandato")
    alterado = copy.deepcopy(d)
    alterado["artefactos"]["spec"]["contenido"] += "x"
    _rechaza(PaqueteUnidad, alterado, "sha256")


def test_unit_import_y_export() -> None:
    entrada = {
        "alcance": f.ALCANCE_WS.model_dump(),
        "repositorios": [{"repositorio": "certificados-api", "rama": "main", "base_commit": f.BASE}],
        "version_contrato_cliente": "1.4",
        "paquete": _dump(f.paquete_unidad),
    }
    UnitImportEntrada.model_validate(entrada)
    _rechaza(UnitExportSalida, {"paquete": _dump(f.paquete_unidad)}, "origen railspec")
    importar, exportar = TOOLS["unit.import"], TOOLS["unit.export"]
    assert importar.efecto == Efecto.escritura and exportar.efecto == Efecto.lectura
    assert importar.superficies == exportar.superficies == {Superficie.mcp, Superficie.http}
    assert importar.tipos_actor == {TipoActor.humano}
    assert exportar.rol_minimo.value == "lector"


def test_causa_importado_se_rehabilita() -> None:
    gate = ResultadoGate.model_validate(
        {
            "veredicto": "escalado",
            "causa": "importado",
            "iteraciones": 0,
            "gobernanza_consultada": "parcial",
            "rehabilitado": {
                "actor": f.JULIAN.model_dump(),
                "en": "2026-10-01T00:00:00Z",
                "motivo": "importada",
            },
            "cerrado_en": "2026-10-01T00:00:00Z",
        }
    )
    assert gate.superado


def test_auditoria_de_importacion() -> None:
    base = {
        "id": str(f.uid(70)),
        "alcance": f.ALCANCE_WS.model_dump(),
        "evento": "importacion",
        "actor": f.JULIAN.model_dump(),
        "en": "2026-10-01T00:00:00Z",
        "unidad": "0001-emitir-pdf",
        "origen_importacion": {"tipo": "sdd-kit", "id_original": "0042-firmar-pdf"},
        "artefactos_importados": 2,
    }
    RegistroAuditoria.model_validate(base)
    _rechaza(RegistroAuditoria, {**base, "artefactos_importados": None}, "importacion necesita")
    _rechaza(RegistroAuditoria, {**base, "actor": f.SERVIDOR.model_dump()}, "actor humano")
    _rechaza(RegistroAuditoria, {**base, "evento": "integracion"}, "solo van con importacion")
