"""Alertas y verificación de `/metrics`: que lo escrito contra el formato del servidor lo siga encajando."""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path

import yaml
from railspec.server.estado import VersionEsquema
from railspec.server.metricas import Metricas, renderizar

DEPLOY = Path(__file__).resolve().parents[1]
PROMETHEUS = DEPLOY / "prometheus"


def _cargar(nombre: str, ruta: Path):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = modulo
    spec.loader.exec_module(modulo)
    return modulo


verificar = _cargar("railspec_deploy_verificar_metricas", PROMETHEUS / "verificar_metricas.py")


def _ok():
    return None


def _caida():
    raise RuntimeError("sin base")


def _cuerpo(sondas=None, almacenada=3) -> str:
    return asyncio.run(
        renderizar(
            Metricas(),
            version_app="0.1",
            esquema=VersionEsquema(codigo=3, almacenada=almacenada),
            sondas=sondas or {"estado": _ok},
        )
    )


def _alertas() -> list[dict]:
    doc = yaml.safe_load((PROMETHEUS / "alertas.yaml").read_text(encoding="utf-8"))
    assert list(doc) == ["groups"]
    return [r for g in doc["groups"] for r in g["rules"]]


def test_las_alertas_tienen_la_forma_que_promtool_espera():
    reglas = _alertas()
    assert reglas, "sin alertas"
    nombres = [r["alert"] for r in reglas]
    assert len(nombres) == len(set(nombres))
    for r in reglas:
        assert set(r) <= {"alert", "expr", "for", "labels", "annotations"}, r["alert"]
        assert r["expr"].strip(), r["alert"]
        assert r["labels"]["severity"] in {"warning", "critical"}, r["alert"]
        assert {"summary", "description"} <= set(r["annotations"]), r["alert"]


def test_cada_metrica_de_las_alertas_la_publica_el_servidor():
    publicadas = set(re.findall(r"^# TYPE (\S+) ", _cuerpo(), re.M))
    usadas = {m for r in _alertas() for m in re.findall(r"\brailspec_[a-z_]+", r["expr"])}
    assert usadas and usadas <= publicadas, usadas - publicadas


def test_las_etiquetas_de_las_alertas_existen_en_las_series():
    cuerpo = _cuerpo()
    for r in _alertas():
        for etiqueta, valor in re.findall(r'\b(origen|estado|sonda|grupo)="([^"]+)"', r["expr"]):
            assert f'{etiqueta}="{valor}"' in cuerpo or etiqueta in {"estado", "grupo"}, (
                r["alert"],
                etiqueta,
            )
    # Las peticiones 5xx se publican con la etiqueta `estado="5xx"` y la superficie en `grupo`.
    m = Metricas()
    m.contar("/v1/algo", "GET", 503)
    ok = asyncio.run(
        renderizar(m, version_app="0", esquema=VersionEsquema(codigo=1, almacenada=1), sondas={})
    )
    assert 'railspec_http_peticiones_total{grupo="v1",metodo="GET",estado="5xx"}' in ok


def test_las_alertas_documentadas_estan_en_el_archivo():
    doc = (DEPLOY.parent / "docs" / "despliegue.md").read_text(encoding="utf-8")
    assert "deploy/prometheus/alertas.yaml" in doc
    assert "verificar_metricas.py" in doc


def test_verificar_acepta_lo_que_publica_el_servidor():
    assert verificar.problemas(_cuerpo()) == []


def test_verificar_dice_que_falla():
    assert any("sonda caída" in p for p in verificar.problemas(_cuerpo({"estado": _caida})))
    assert any("esquema desalineado" in p for p in verificar.problemas(_cuerpo(almacenada=2)))
    assert verificar.problemas("") == [f"falta la familia {f}" for f in verificar.FAMILIAS]
    assert verificar.problemas("esto no es una métrica") != []


def test_verificar_sin_token_o_argumento_sale_con_2(monkeypatch, capsys):
    monkeypatch.delenv("RAILSPEC_METRICAS_TOKEN", raising=False)
    assert verificar.main(["https://x"]) == 2
    monkeypatch.setenv("RAILSPEC_METRICAS_TOKEN", "x" * 16)
    assert verificar.main([]) == 2
    assert "uso:" in capsys.readouterr().err


def test_verificar_traduce_el_estado_http(monkeypatch, capsys):
    monkeypatch.setenv("RAILSPEC_METRICAS_TOKEN", "x" * 16)
    for estado, texto in (
        (401, "no es una activa de la consola"),
        (404, "ni claves activas"),
        (502, "502"),
    ):
        monkeypatch.setattr(verificar, "consultar", lambda b, t, e=estado: (e, ""))
        assert verificar.main(["https://x"]) == 1
        assert texto in capsys.readouterr().out
    monkeypatch.setattr(verificar, "consultar", lambda b, t: (200, _cuerpo()))
    assert verificar.main(["https://x"]) == 0
    assert "up == 1" in capsys.readouterr().out
