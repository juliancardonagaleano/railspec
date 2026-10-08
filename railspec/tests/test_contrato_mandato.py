"""Contrato 1.11: el mandato de los modos supervisado y desatendido."""

from __future__ import annotations

import copy
from datetime import timedelta
from typing import Any

import fabricas as f
import pytest
from pydantic import TypeAdapter, ValidationError
from railspec.contracts.comun import Modo, TipoActor
from railspec.contracts.estado import Checkpoint, EstadoUnidad, ResultadoGate, TipoCheckpoint
from railspec.contracts.mandato import (
    CAUSAS_DE_MANDATO,
    CausaParada,
    DecisionDelegada,
    DecisionPropuesta,
    EstadoMandato,
    Mandato,
    MandatoContenido,
    ParadaMandato,
    ResultadoRevision,
    RevisionDecision,
)
from railspec.contracts.reporte import ReporteOrden
from railspec.contracts.tools import (
    TOOLS,
    AvanceMandatoParado,
    Efecto,
    MandateApproveEntrada,
    MandateReviewEntrada,
    Superficie,
    UnitAdvanceSalida,
)


def _dump(fabrica: Any) -> dict[str, Any]:
    return copy.deepcopy(fabrica().model_dump(mode="json"))


def _rechaza(tipo: Any, datos: dict[str, Any], fragmento: str) -> None:
    with pytest.raises(ValidationError) as exc:
        TypeAdapter(tipo).validate_python(datos)
    assert fragmento in str(exc.value)


# --- Contenido -------------------------------------------------------------------------------------


def test_la_huella_depende_del_contenido_y_no_del_orden_de_claves() -> None:
    a = f.mandato_contenido()
    assert a.huella() == f.mandato_contenido().huella()
    assert len(a.huella()) == 64
    otro = a.model_copy(update={"objetivo": a.objetivo + " Y también los informes."})
    assert otro.huella() != a.huella()
    barajado = {k: v for k, v in reversed(list(a.model_dump(mode="json").items()))}
    assert MandatoContenido.model_validate(barajado).huella() == a.huella()


def test_un_mandato_solo_ampara_supervisado_o_desatendido() -> None:
    d = f.mandato_contenido().model_dump(mode="json")
    for modo in ("interactivo", "semi-autonomo"):
        _rechaza(MandatoContenido, {**d, "modo": modo}, "supervisado o desatendido")
    MandatoContenido.model_validate({**d, "modo": "supervisado"})


def test_desatendido_exige_un_tope_de_presupuesto_total() -> None:
    d = f.mandato_contenido().model_dump(mode="json")
    d["limites"]["presupuesto"] = {}
    _rechaza(MandatoContenido, d, "al menos un tope de presupuesto")
    # Supervisado puede ir sin tope: hay un humano mirando.
    MandatoContenido.model_validate({**d, "modo": "supervisado"})
    d["limites"]["presupuesto"] = {"llamadas_max": 200}
    MandatoContenido.model_validate(d)


def test_limites_con_valores_acotados() -> None:
    d = f.mandato_contenido().model_dump(mode="json")
    for campo, valor in (
        ("vigencia_horas", 169),
        ("vigencia_horas", 0),
        ("reintentos_parada", 4),
        ("max_unidades", 51),
        ("repositorios", []),
    ):
        malo = copy.deepcopy(d)
        malo["limites"][campo] = valor
        with pytest.raises(ValidationError):
            MandatoContenido.model_validate(malo)
    d["limites"]["repositorios"] = ["certificados-api", "certificados-api"]
    _rechaza(MandatoContenido, d, "repositorios del mandato repetidos")


def test_los_defectos_son_los_seguros() -> None:
    d = f.mandato_contenido().model_dump(mode="json")
    d["limites"] = {"repositorios": ["certificados-api"], "presupuesto": {"tokens_max": 10}}
    limites = MandatoContenido.model_validate(d).limites
    assert limites.reintentos_parada == 0  # ningún reintento sin preguntar
    assert limites.vigencia_horas == 24  # caduca
    assert limites.max_unidades == 5
    assert limites.rutas_permitidas == []


def test_delegaciones_con_id_unico() -> None:
    d = f.mandato_contenido().model_dump(mode="json")
    d["delegaciones"][1]["id"] = "D-1"
    _rechaza(MandatoContenido, d, "delegaciones repetidos")


# --- Mandato -----------------------------------------------------------------------------------------


def test_un_mandato_aprobado_descansa_en_la_huella_de_su_contenido() -> None:
    d = _dump(f.mandato)
    d["aprobaciones"][0]["huella"] = "0" * 64
    _rechaza(Mandato, d, "el contenido cambió después de la última aprobación")
    d = _dump(f.mandato)
    d["contenido"]["objetivo"] = "Otro objetivo"
    _rechaza(Mandato, d, "debe volver a propuesto")


def test_solo_un_humano_aprueba_o_revoca() -> None:
    d = _dump(f.mandato)
    d["aprobaciones"][0]["actor"] = f.SERVIDOR.model_dump(mode="json")
    _rechaza(Mandato, d, "aprobar un mandato exige un actor humano")
    d = _dump(f.mandato)
    d.update(
        estado="revocado",
        revocacion={"actor": f.SERVIDOR.model_dump(mode="json"), "en": f.T0.isoformat(), "motivo": "x"},
    )
    _rechaza(Mandato, d, "revocar un mandato exige un actor humano")


def test_la_aprobacion_caduca_despues_de_darse() -> None:
    d = _dump(f.mandato)
    d["aprobaciones"][0]["caduca_en"] = d["aprobaciones"][0]["en"]
    _rechaza(Mandato, d, "caduca después de darse")


def test_estados_del_mandato() -> None:
    m = f.mandato()
    # propuesto: sin parada; puede conservar la historia de aprobaciones.
    d = _dump(f.mandato)
    d["estado"] = "propuesto"
    Mandato.model_validate(d)
    d["parada"] = {"causa": "mandato-caducado", "detalle": "x", "en": f.T0.isoformat()}
    _rechaza(Mandato, d, "propuesto no está parado")
    # aprobado necesita aprobación; parado necesita parada.
    d = _dump(f.mandato)
    d["aprobaciones"] = []
    _rechaza(Mandato, d, "necesita una aprobación")
    d = _dump(f.mandato)
    d["estado"] = "parado"
    _rechaza(Mandato, d, "necesita su parada")
    d["parada"] = {
        "causa": "gate-escalado",
        "unidad": "0001-emitir-pdf",
        "detalle": "x",
        "en": f.T0.isoformat(),
    }
    assert Mandato.model_validate(d).estado == EstadoMandato.parado
    d["estado"] = "aprobado"
    _rechaza(Mandato, d, "aprobado no lleva parada")
    # revocado necesita su revocación y solo él la lleva.
    d = _dump(f.mandato)
    d["estado"] = "revocado"
    _rechaza(Mandato, d, "necesita su revocación")
    assert m.estado == EstadoMandato.aprobado


def test_vigencia_se_evalua_contra_el_reloj() -> None:
    m = f.mandato()
    assert m.vigente(f.T0 + timedelta(hours=11))
    assert m.motivo_no_vigente(f.T0 + timedelta(hours=11)) is None
    assert not m.vigente(f.T0 + timedelta(hours=12))
    assert "caducó" in (m.motivo_no_vigente(f.T0 + timedelta(hours=13)) or "")
    assert m.vigente_hasta == f.T0 + timedelta(hours=12)
    propuesto = m.model_copy(update={"estado": EstadoMandato.propuesto})
    assert not propuesto.vigente(f.T0)
    assert "no está aprobado" in (propuesto.motivo_no_vigente(f.T0) or "")


def test_una_parada_de_mandato_es_de_mandato() -> None:
    for causa in CAUSAS_DE_MANDATO:
        ParadaMandato(causa=causa, detalle="x", en=f.T0)
    for causa in set(CausaParada) - CAUSAS_DE_MANDATO:
        with pytest.raises(ValidationError, match="detiene una unidad"):
            ParadaMandato(causa=causa, detalle="x", en=f.T0)


# --- Unidad, gate, checkpoint, orden y reporte -----------------------------------------------------------


def _decision(**cambios: Any) -> dict[str, Any]:
    base = {
        "id": "DD-1",
        "delegacion": "D-2",
        "que": "Llamé `emitir_pdfa` a la función nueva.",
        "alternativas": ["emitir_pdf_a"],
        "revertir": "Renombrar en src/pdf/emitir.py.",
        "fase": "implement",
        "tomada_por": f.SERVIDOR.model_dump(mode="json"),
        "en": f.T0.isoformat(),
    }
    return {**base, **cambios}


def test_decision_delegada_cita_una_delegacion_o_el_reintento() -> None:
    DecisionDelegada.model_validate(_decision())
    DecisionDelegada.model_validate(_decision(delegacion="reintento"))
    for malo in ("D-", "D-1000", "otra", ""):
        with pytest.raises(ValidationError):
            DecisionDelegada.model_validate(_decision(delegacion=malo))


def test_la_decision_la_toma_un_agente_o_un_humano() -> None:
    _rechaza(DecisionDelegada, _decision(tomada_por=f.CI.model_dump(mode="json")), "agente o un humano")


def test_revision_de_una_decision_es_humana_y_revertir_pide_comentario() -> None:
    base = {"resultado": "aceptada", "actor": f.JULIAN_WEB.model_dump(mode="json"), "en": f.T0.isoformat()}
    RevisionDecision.model_validate(base)
    _rechaza(RevisionDecision, {**base, "resultado": "revertida"}, "necesita comentario")
    _rechaza(RevisionDecision, {**base, "actor": f.SERVIDOR.model_dump(mode="json")}, "exige un actor humano")


def test_la_unidad_lleva_decisiones_solo_bajo_un_mandato() -> None:
    d = _dump(f.estado_unidad)
    d["decisiones"] = [_decision()]
    _rechaza(EstadoUnidad, d, "solo una unidad de un mandato")
    d["unidad"]["plan"] = "pdf-a"
    EstadoUnidad.model_validate(d)
    d["decisiones"] = [_decision(), _decision()]
    _rechaza(EstadoUnidad, d, "decisiones delegadas repetidos")


def test_un_gate_solo_se_difiere_si_escalo() -> None:
    gate = _dump(f.estado_unidad)["gates"]["spec"]
    gate["diferido"] = True
    _rechaza(ResultadoGate, gate, "solo un gate escalado puede diferirse")
    gate.update(veredicto="escalado", causa="hallazgos-sin-resolver", hallazgos=[])
    assert ResultadoGate.model_validate(gate).diferido


def test_la_causa_de_parada_solo_va_en_paradas() -> None:
    base = {
        "id": str(f.uid(77)),
        "fase": "implement",
        "pregunta": "¿Reintentar?",
        "abierto_en": f.T0.isoformat(),
        "causa_parada": "fuera-de-alcance",
    }
    Checkpoint.model_validate({**base, "tipo": "parada"})
    Checkpoint.model_validate({**base, "tipo": "gate-escalado"})
    assert TipoCheckpoint.aprobar_spec.value == "aprobar-spec"
    _rechaza(Checkpoint, {**base, "tipo": "aprobar-spec"}, "causa_parada solo va en una parada")


def test_el_reporte_registra_decisiones_con_tope() -> None:
    d = _dump(f.reporte)
    propuesta = {"delegacion": "D-1", "que": "x", "revertir": "y"}
    d["decisiones"] = [propuesta]
    assert ReporteOrden.model_validate(d).decisiones[0] == DecisionPropuesta(**propuesta)
    d["decisiones"] = [propuesta] * 21
    with pytest.raises(ValidationError):
        ReporteOrden.model_validate(d)
    _rechaza(ReporteOrden, {**_dump(f.reporte), "decisiones": [{**propuesta, "delegacion": "libre"}]}, "D-")


def test_las_ordenes_pueden_llevar_el_mandato_y_no_es_obligatorio() -> None:
    assert f.orden().mandato is None
    d = _dump(f.orden)
    d["mandato"] = {
        "mandato": "pdf-a",
        "modo": "desatendido",
        "caduca_en": (f.T0 + timedelta(hours=12)).isoformat(),
        "delegaciones": [{"id": "D-1", "tipo": "pre-decidida", "texto": "x"}],
        "rutas_permitidas": ["src/pdf/**"],
        "reintentos_parada": 1,
    }
    orden = TypeAdapter(type(f.orden())).validate_python(d)
    assert orden.mandato is not None and orden.mandato.reintentos_parada == 1


# --- Tools -------------------------------------------------------------------------------------------------


def test_aprobar_revisar_y_revocar_solo_las_hace_una_persona() -> None:
    for nombre in ("mandate.approve", "mandate.revoke", "mandate.review"):
        t = TOOLS[nombre]
        assert t.efecto == Efecto.escritura
        assert t.tipos_actor == {TipoActor.humano}
        assert Superficie.chat not in t.superficies


def test_aprobar_y_revisar_no_se_exponen_por_mcp() -> None:
    # El MCP lo usa el arnés (un agente); la aprobación solo se hace desde la consola.
    assert TOOLS["mandate.approve"].superficies == {Superficie.http}
    assert TOOLS["mandate.review"].superficies == {Superficie.http}
    # Detener es siempre más seguro que seguir: revocar sí está en el MCP.
    assert Superficie.mcp in TOOLS["mandate.revoke"].superficies
    assert Superficie.mcp in TOOLS["mandate.propose"].superficies


def test_proponer_lo_puede_hacer_un_agente_pero_no_aprobar() -> None:
    assert TipoActor.agente in TOOLS["mandate.propose"].tipos_actor
    assert TipoActor.agente not in TOOLS["mandate.approve"].tipos_actor


def test_aprobar_exige_la_huella_de_lo_que_se_vio() -> None:
    base = {"alcance": f.ALCANCE_WS.model_dump(), "id": "pdf-a", "version_vista": 2}
    MandateApproveEntrada.model_validate({**base, "huella": f.mandato_contenido().huella()})
    for mala in ("", "abc", "G" * 64):
        with pytest.raises(ValidationError):
            MandateApproveEntrada.model_validate({**base, "huella": mala})


def test_revertir_una_decision_pide_comentario() -> None:
    base = {
        "unidad": f.ALCANCE_UNIDAD.model_dump(),
        "decision": "DD-1",
        "resultado": "revertida",
        "version_vista": 3,
    }
    _rechaza(MandateReviewEntrada, base, "necesita comentario")
    MandateReviewEntrada.model_validate({**base, "comentario": "El nombre no sigue el estilo."})
    assert ResultadoRevision.aceptada.value == "aceptada"


def test_unit_advance_puede_decir_que_el_mandato_esta_parado() -> None:
    salida = {
        "version_estado": 4,
        "avance": {
            "tipo": "mandato-parado",
            "mandato": "pdf-a",
            "causa": "mandato-caducado",
            "detalle": "la aprobación caducó",
        },
    }
    avance = UnitAdvanceSalida.model_validate(salida).avance
    assert isinstance(avance, AvanceMandatoParado) and avance.reintentar_en_s == 300
    salida["avance"].pop("causa")
    assert UnitAdvanceSalida.model_validate(salida).avance.causa is None  # type: ignore[union-attr]


def test_modos_con_mandato_y_modo_del_contenido() -> None:
    assert f.mandato().contenido.modo == Modo.desatendido
