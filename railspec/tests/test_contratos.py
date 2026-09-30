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
from railspec.contracts.reporte import ReporteOrden
from railspec.contracts.repositorio import AsignacionRol, RegistroAuditoria, VinculoRepositorio
from railspec.contracts.snapshot import Snapshot, id_simbolo
from railspec.contracts.tools import (
    TOOLS,
    Efecto,
    GraphIndexEntrada,
    GraphQueryEntrada,
    Superficie,
    UnitApproveEntrada,
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
