"""``railspec insumo pull``: trae un insumo del chat de la consola al worktree.

El insumo (``railspec.insumo/v1``) nunca lleva texto de código; trae
afirmaciones con referencias tipadas. Aquí se escribe como Markdown en
``.railspec/insumos/<id>.md`` para que el arnés lo lea y resuelva en local el
texto de cada referencia contra su clon. Junto a él queda el JSON original,
cuyo hash se verificó al validarlo contra el contrato.
"""

from __future__ import annotations

from pathlib import Path

from railspec.contracts.insumo import Insumo
from railspec.contracts.referencias import (
    RefArchivo,
    RefCriterio,
    RefDecision,
    RefGobernanza,
    RefNodoGrafo,
    RefSimbolo,
    RefUnidad,
)

DIR_INSUMOS = Path(".railspec") / "insumos"


def describir_referencia(ref: object) -> str:
    if isinstance(ref, RefSimbolo):
        donde = f"`{ref.repositorio}:{ref.ruta}` @ {ref.commit[:12]}"
        return f"símbolo `{ref.nombre}` ({ref.tipo_simbolo.value}) en {donde}"
    if isinstance(ref, RefArchivo):
        lineas = f":{ref.linea_inicio}-{ref.linea_fin}" if ref.linea_inicio else ""
        return f"archivo `{ref.repositorio}:{ref.ruta}{lineas}` @ {ref.commit[:12]}"
    if isinstance(ref, RefNodoGrafo):
        return f"{ref.clase} `{ref.nombre}` del grafo de `{ref.repositorio}` @ {ref.commit[:12]}"
    if isinstance(ref, RefUnidad):
        return f"unidad `{ref.unidad}` del workspace `{ref.workspace}`"
    if isinstance(ref, RefCriterio):
        return f"criterio `{ref.criterio}` de la unidad `{ref.unidad}`"
    if isinstance(ref, RefGobernanza):
        return f"gobernanza `{ref.id}` ({ref.proveedor})"
    if isinstance(ref, RefDecision):
        return f"decisión `{ref.id}` del workspace `{ref.workspace}`"
    return repr(ref)


def a_markdown(insumo: Insumo) -> str:
    autor = insumo.autor.login or insumo.autor.agente or insumo.autor.tipo.value
    alcance = f"{insumo.alcance.org}/{insumo.alcance.workspace}"
    lineas = [
        f"# Insumo {insumo.id}",
        "",
        f"- Formato: `{insumo.formato}` · hash `{insumo.sha256[:16]}`",
        f"- Workspace: `{alcance}` · nivel `{insumo.nivel_efectivo.value}`",
        "- Repositorios: "
        + ", ".join(f"`{r.repositorio}` ({r.rol.value}) @ {r.base_commit[:12]}" for r in insumo.repositorios),
        f"- Creado: {insumo.creado_en.isoformat()} por {autor}",
        "",
        "## Objetivo",
        "",
        insumo.objetivo,
        "",
        "## Hallazgos",
        "",
    ]
    for n, afirmacion in enumerate(insumo.hallazgos, start=1):
        lineas.append(f"{n}. {afirmacion.texto}")
        for ref in afirmacion.referencias:
            lineas.append(f"   - {describir_referencia(ref)}")
    if insumo.restricciones:
        lineas += ["", "## Restricciones", ""] + [f"- {r}" for r in insumo.restricciones]
    if insumo.preguntas_abiertas:
        lineas += ["", "## Preguntas abiertas", ""] + [f"- {p}" for p in insumo.preguntas_abiertas]
    lineas += [
        "",
        "---",
        "Las referencias apuntan a commits concretos; resuélvelas en local contra el clon. "
        "El insumo no contiene código por diseño.",
        "",
    ]
    return "\n".join(lineas)


def escribir(destino: Path, insumo: Insumo) -> Path:
    carpeta = destino / DIR_INSUMOS
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / f"{insumo.id}.json").write_text(insumo.model_dump_json(indent=2) + "\n", encoding="utf-8")
    ruta = carpeta / f"{insumo.id}.md"
    ruta.write_text(a_markdown(insumo), encoding="utf-8")
    return ruta
