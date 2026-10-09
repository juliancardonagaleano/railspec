"""Portabilidad: paquete en disco, importador del kit SDD y unit.import/unit.export contra el doble."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from local_fabricas import ServidorDoble, crear_proxy
from railspec.contracts import VERSION_CONTRATO
from railspec.contracts.comun import Fase, Perfil, Riesgo
from railspec.contracts.orden import Artefacto
from railspec.local import cli, portabilidad
from railspec.local.errores import ErrorRailspec, SecretosDetectados

ESTADO_KIT = """\
# Estado de la unidad.
id: 0007-pce-mcp-completo
titulo: "PCE MCP completo con el kit"
fase: implement       # research | spec | plan | tasks | aprobacion | implement | done
estado: en-progreso
governance_refs: [ninguna-aplicable]
comando_validacion: "python3 -m pytest -q"
modo: supervisado
riesgo: alto
perfil: profundo
depende_de: [0006-cli-unificado]
gates:
  spec:
    veredicto: aprobado
    iteraciones: 2
    criticos:
      - {lente: "L1", subagente: inline}
  plan:
    veredicto: aprobado
"""

SPEC = """\
# Spec — PCE MCP completo

## Problema / Motivación

El kit no consulta pce-mcp en todos los gates.

## Resultado esperado

- CA-01: los gates consultan pce-mcp.
"""
PLAN = "# Plan\n\nG1.\n"
TAREAS = "# Tareas\n\n- [ ] T1\n"

RAIZ_REPO = Path(__file__).resolve().parents[4]


def correr(coro):
    return asyncio.run(coro)


def unidad_kit(tmp_path: Path, *archivos: str, estado: str = ESTADO_KIT) -> Path:
    carpeta = tmp_path / "kit" / "0007-pce-mcp-completo"
    carpeta.mkdir(parents=True)
    (carpeta / "_estado.yaml").write_text(estado, encoding="utf-8")
    contenidos = {"spec.md": SPEC, "plan.md": PLAN, "tasks.md": TAREAS}
    for nombre in archivos:
        (carpeta / nombre).write_text(contenidos[nombre], encoding="utf-8")
    (carpeta / "bitacora.md").write_text("# Bitácora\n", encoding="utf-8")
    return carpeta


def test_unidad_del_kit_se_convierte_con_su_fase_y_metadatos(tmp_path):
    conversion = portabilidad.desde_unidad_sdd(
        unidad_kit(tmp_path, "spec.md", "plan.md", "tasks.md"), "certificados-api"
    )
    paquete = conversion.paquete

    assert paquete.origen.tipo == "sdd-kit" and paquete.origen.id_original == "0007-pce-mcp-completo"
    assert paquete.origen.repositorio == "certificados-api"
    assert paquete.titulo == "PCE MCP completo con el kit"
    assert paquete.pedido == "El kit no consulta pce-mcp en todos los gates."
    assert paquete.fase_retomar == Fase.implement
    assert paquete.artefactos.presentes() == 3 and paquete.artefactos.plan.contenido == PLAN
    # Supervisado exige mandato: no se importa y la conversión lo avisa.
    assert paquete.modo is None and any("supervisado" in a for a in conversion.avisos)
    assert (paquete.riesgo, paquete.perfil) == (Riesgo.alto, Perfil.profundo)
    assert paquete.governance_refs == ["ninguna-aplicable"]
    assert paquete.depende_de_original == ["0006-cli-unificado"]
    assert [(g.gate, g.resultado, g.iteraciones) for g in paquete.historial_gates] == [
        ("spec", "aprobado", 2),
        ("plan", "aprobado", None),
    ]
    assert conversion.borradores == {}


@pytest.mark.parametrize(
    ("fase", "archivos", "retomar", "aprobados", "borradores"),
    [
        # El artefacto de la fase en curso es un borrador, no está aprobado.
        ("spec", ("spec.md",), Fase.spec, [], [Artefacto.spec]),
        (
            "tasks",
            ("spec.md", "plan.md", "tasks.md"),
            Fase.tasks,
            [Artefacto.spec, Artefacto.plan],
            [Artefacto.tasks],
        ),
        # Falta el plan: se retoma en plan y las tasks sueltas quedan como borrador.
        ("implement", ("spec.md", "tasks.md"), Fase.plan, [Artefacto.spec], [Artefacto.tasks]),
        ("done", (), Fase.spec, [], []),
        ("research", (), Fase.research, [], []),
    ],
)
def test_fase_de_retoma_y_reparto_de_artefactos(tmp_path, fase, archivos, retomar, aprobados, borradores):
    estado = ESTADO_KIT.replace("fase: implement", f"fase: {fase}")
    conversion = portabilidad.desde_unidad_sdd(unidad_kit(tmp_path, *archivos, estado=estado))

    assert conversion.paquete.fase_retomar == retomar
    assert list(portabilidad.artefactos_del_paquete(conversion.paquete)) == aprobados
    assert list(conversion.borradores) == borradores


def test_estado_del_kit_con_valores_desconocidos_no_rompe(tmp_path):
    estado = 'id: 0001-x\ntitulo: "X"\nfase: rara\nmodo: otro\n'
    conversion = portabilidad.desde_unidad_sdd(unidad_kit(tmp_path, estado=estado))
    assert conversion.paquete.fase_retomar == Fase.spec
    assert conversion.paquete.modo is None and conversion.paquete.pedido == "X"
    assert any("desconocida" in a for a in conversion.avisos)


def test_paquete_en_disco_ida_y_vuelta_y_detecta_ediciones(tmp_path):
    estado = ESTADO_KIT.replace("fase: implement", "fase: tasks")
    conversion = portabilidad.desde_unidad_sdd(
        unidad_kit(tmp_path, "spec.md", "plan.md", "tasks.md", estado=estado)
    )
    destino = portabilidad.escribir_paquete(conversion, tmp_path / "paquete")

    manifiesto = json.loads((destino / "unidad.json").read_text(encoding="utf-8"))
    assert manifiesto["formato"] == "railspec.unidad/v1"
    assert manifiesto["artefactos"]["spec"] == {
        "archivo": "spec.md",
        "sha256": conversion.paquete.artefactos.spec.sha256,
    }
    assert "contenido" not in json.dumps(manifiesto)
    assert (destino / "borradores" / "tasks.md").read_text(encoding="utf-8") == TAREAS
    leida = portabilidad.leer_paquete(destino)
    assert leida.paquete == conversion.paquete and leida.borradores == conversion.borradores
    assert portabilidad.leer_origen(destino).paquete == conversion.paquete

    with pytest.raises(ErrorRailspec, match="no está vacía"):
        portabilidad.escribir_paquete(conversion, destino)
    (destino / "spec.md").write_text("# Spec editado\n", encoding="utf-8")
    with pytest.raises(ErrorRailspec, match="sha256"):
        portabilidad.leer_paquete(destino)
    with pytest.raises(ErrorRailspec, match="ni un paquete"):
        portabilidad.leer_origen(tmp_path)


def test_importar_registra_con_unit_import_y_siembra_los_artefactos(tmp_path):
    servidor = ServidorDoble()
    proxy = crear_proxy(tmp_path, servidor)
    estado = ESTADO_KIT.replace("fase: implement", "fase: tasks")
    conversion = portabilidad.desde_unidad_sdd(
        unidad_kit(tmp_path, "spec.md", "plan.md", "tasks.md", estado=estado), "certificados-api"
    )

    resultado = correr(portabilidad.importar(proxy, conversion))

    tool, entrada = servidor.llamadas[0]
    assert tool == "unit.import"
    assert entrada["paquete"]["origen"]["id_original"] == "0007-pce-mcp-completo"
    assert entrada["paquete"]["fase_retomar"] == "tasks"
    assert entrada["repositorios"][0]["repositorio"] == "certificados-api"
    assert resultado["fase"] == "tasks" and resultado["ya_existia"] is False
    assert resultado["aprobados"] == ["spec", "plan"] and resultado["borradores"] == ["tasks"]
    carpeta = Path(resultado["worktree"]) / ".railspec" / "unidades" / "0001-sumar"
    assert (carpeta / "spec.md").read_text(encoding="utf-8") == SPEC
    assert (carpeta / "plan.md").read_text(encoding="utf-8") == PLAN
    assert (carpeta / "tasks.md").read_text(encoding="utf-8") == TAREAS
    assert proxy.unidades_locales() == {"0001-sumar": Path(resultado["worktree"])}

    # El servidor es idempotente por origen: la segunda vez no abre otro worktree.
    otra = correr(portabilidad.importar(proxy, conversion))
    assert otra["ya_existia"] is True and otra["worktree"] == resultado["worktree"]
    assert len(proxy.unidades_locales()) == 1


def test_importar_con_secretos_no_llama_al_servidor(tmp_path):
    servidor = ServidorDoble()
    proxy = crear_proxy(tmp_path, servidor)
    carpeta = unidad_kit(tmp_path, "spec.md")
    (carpeta / "spec.md").write_text(SPEC + "\nclave: AKIAABCDEFGHIJKLMNOP\n", encoding="utf-8")

    with pytest.raises(SecretosDetectados) as exc:
        correr(portabilidad.importar(proxy, portabilidad.desde_unidad_sdd(carpeta)))
    assert "AKIA" not in str(exc.value)
    assert servidor.llamadas == []


def test_exportar_escribe_el_paquete_de_unit_export(tmp_path):
    servidor = ServidorDoble()
    proxy = crear_proxy(tmp_path, servidor)
    conversion = portabilidad.desde_unidad_sdd(unidad_kit(tmp_path, "spec.md", "plan.md", "tasks.md"))
    correr(portabilidad.importar(proxy, conversion))

    resultado = correr(portabilidad.exportar(proxy, "0001-sumar", tmp_path / "exportada"))

    assert servidor.llamadas[-1] == (
        "unit.export",
        {
            "unidad": {"org": "acme", "workspace": "certificados", "unidad": "0001-sumar"},
            "version_contrato": VERSION_CONTRATO,
        },
    )
    assert resultado["artefactos"] == ["spec", "plan", "tasks"]
    exportado = portabilidad.leer_paquete(tmp_path / "exportada").paquete
    assert exportado.origen.tipo == "railspec" and exportado.origen.id_original == "0001-sumar"
    assert exportado.fase_retomar == Fase.implement
    assert exportado.artefactos.spec.contenido == SPEC


def test_cli_solo_convertir_no_necesita_servidor(tmp_path, capsys):
    carpeta = unidad_kit(tmp_path, "spec.md")
    salida = tmp_path / "paquetes"

    assert (
        cli.main(["--repo", str(RAIZ_REPO), "importar", str(carpeta), "--solo-convertir", str(salida)]) == 0
    )

    impreso = json.loads(capsys.readouterr().out)["paquetes"]
    assert impreso[0]["paquete"] == str(salida / "0007-pce-mcp-completo")
    assert impreso[0]["fase_retomar"] == "plan"
    leido = portabilidad.leer_paquete(salida / "0007-pce-mcp-completo")
    assert leido.paquete.titulo == "PCE MCP completo con el kit"
