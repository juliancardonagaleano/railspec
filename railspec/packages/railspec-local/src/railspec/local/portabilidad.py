"""Portabilidad de unidades: paquete ``railspec.unidad/v1`` en disco, exportar e importar.

El paquete es el del contrato (``railspec.contracts.portabilidad``). En disco es
una carpeta con ``unidad.json`` (el paquete sin el texto de los artefactos) y un
Markdown por artefacto (``spec.md``, ``plan.md``, ``tasks.md``); el manifiesto
lleva el sha256 de cada uno y leer el paquete lo verifica, así que un paquete
editado a medias no entra. ``borradores/`` guarda, fuera del paquete, el
artefacto que la unidad de origen estaba redactando: no está aprobado, pero al
importar se siembra en el worktree para que el agente parta de él.

Dos orígenes:

- una unidad de Railspec: ``railspec exportar`` pide el paquete con ``unit.export``;
- una unidad del kit SDD embebido (``.spec/units/<id>/``), que se convierte a
  demanda: no hay retrocompatibilidad automática.

``importar`` llama ``unit.import`` (idempotente por origen en el servidor), abre
el worktree de la unidad como ``unit.start`` y deja los artefactos en
``.railspec/unidades/<unidad>/``, la misma ruta de las órdenes de redactar.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from railspec.contracts._base import VERSION_CONTRATO
from railspec.contracts.comun import (
    MODOS_CON_MANDATO,
    AlcanceUnidad,
    AlcanceWorkspace,
    Fase,
    Modo,
    Perfil,
    Riesgo,
)
from railspec.contracts.orden import Artefacto
from railspec.contracts.portabilidad import (
    FORMATO_PAQUETE,
    ArtefactosPaquete,
    GateImportado,
    OrigenPaquete,
    PaqueteUnidad,
)
from railspec.contracts.reporte import ArtefactoRedactado
from railspec.contracts.tools import UnitExportEntrada, UnitExportSalida, UnitImportEntrada, UnitImportSalida

from .almacen import Almacen
from .errores import ErrorRailspec, SecretosDetectados
from .secretos import escanear

MANIFIESTO = "unidad.json"
DIR_BORRADORES = "borradores"
MAX_PEDIDO = 20_000
ORDEN_ARTEFACTOS = (Artefacto.spec, Artefacto.plan, Artefacto.tasks)
_FASE_DE = {Artefacto.spec: Fase.spec, Artefacto.plan: Fase.plan, Artefacto.tasks: Fase.tasks}
#: Artefactos aprobados que implica cada fase de origen (spec en curso: ninguno aprobado todavía).
_APROBADOS_EN = {
    Fase.research: 0,
    Fase.spec: 0,
    Fase.plan: 1,
    Fase.tasks: 2,
    Fase.aprobacion: 3,
    Fase.implement: 3,
    Fase.done: 3,
}

Json = dict[str, Any]


@dataclass
class Conversion:
    """Paquete del contrato más lo que no cabe en él: borradores y avisos de la conversión."""

    paquete: PaqueteUnidad
    borradores: dict[Artefacto, str] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)


def artefacto(tipo: Artefacto, contenido: str) -> ArtefactoRedactado:
    return ArtefactoRedactado(
        tipo=tipo, contenido=contenido, sha256=hashlib.sha256(contenido.encode("utf-8")).hexdigest()
    )


def artefactos_del_paquete(paquete: PaqueteUnidad) -> dict[Artefacto, ArtefactoRedactado]:
    return {t: a for t in ORDEN_ARTEFACTOS if (a := getattr(paquete.artefactos, t.value)) is not None}


def repartir(fase: Fase, textos: dict[Artefacto, str]) -> tuple[Fase, list[Artefacto], list[Artefacto]]:
    """Fase en que se retoma, artefactos aprobados (prefijo) y borradores.

    Se aprueban los que la fase de origen da por cerrados y forman prefijo
    spec → plan → tasks; si falta uno, se retoma en su fase. Lo que sobra (el
    artefacto en redacción, o uno suelto tras un hueco) queda como borrador.
    """

    aprobados: list[Artefacto] = []
    for tipo in ORDEN_ARTEFACTOS[: _APROBADOS_EN[fase]]:
        if tipo not in textos:
            fase = _FASE_DE[tipo]
            break
        aprobados.append(tipo)
    borradores = [t for t in ORDEN_ARTEFACTOS if t in textos and t not in aprobados]
    return fase, aprobados, borradores


# --- paquete en disco ---------------------------------------------------------------------


def escribir_paquete(conversion: Conversion, destino: Path) -> Path:
    """Escribe el paquete en ``destino`` (carpeta nueva o vacía); devuelve la carpeta."""

    if destino.exists() and (not destino.is_dir() or any(destino.iterdir())):
        raise ErrorRailspec(f"{destino} ya existe y no está vacía; elige otra carpeta.")
    destino.mkdir(parents=True, exist_ok=True)
    paquete = conversion.paquete
    manifiesto = paquete.model_dump(mode="json", exclude_none=True)
    manifiesto["artefactos"] = {}
    for tipo, a in artefactos_del_paquete(paquete).items():
        nombre = f"{tipo.value}.md"
        (destino / nombre).write_text(a.contenido, encoding="utf-8")
        manifiesto["artefactos"][tipo.value] = {"archivo": nombre, "sha256": a.sha256}
    (destino / MANIFIESTO).write_text(
        json.dumps(manifiesto, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if conversion.borradores:
        (destino / DIR_BORRADORES).mkdir()
        for tipo, texto in conversion.borradores.items():
            (destino / DIR_BORRADORES / f"{tipo.value}.md").write_text(texto, encoding="utf-8")
    return destino


def leer_paquete(origen: Path) -> Conversion:
    ruta = origen / MANIFIESTO
    if not ruta.is_file():
        raise ErrorRailspec(f"{origen} no es un paquete {FORMATO_PAQUETE}: falta {MANIFIESTO}.")
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ErrorRailspec(f"{ruta} no es JSON válido: {exc}") from exc
    if not isinstance(datos, dict) or datos.get("formato") != FORMATO_PAQUETE:
        formato = datos.get("formato") if isinstance(datos, dict) else None
        raise ErrorRailspec(f"{ruta}: formato {formato!r} no soportado (se espera {FORMATO_PAQUETE}).")
    artefactos: Json = {}
    for tipo, entrada in (datos.get("artefactos") or {}).items():
        archivo = origen / Path(str(entrada.get("archivo", ""))).name
        if not archivo.is_file():
            raise ErrorRailspec(f"{origen}: el manifiesto cita {archivo.name} y no existe.")
        artefactos[tipo] = {
            "tipo": tipo,
            "contenido": archivo.read_text(encoding="utf-8"),
            "sha256": entrada.get("sha256"),
        }
    datos["artefactos"] = artefactos
    try:
        paquete = PaqueteUnidad.model_validate(datos)
    except ValidationError as exc:
        # El caso típico: un artefacto editado después de exportar (sha256 distinto).
        raise ErrorRailspec(f"{origen}: el paquete no es válido: {exc}") from exc
    borradores = {
        t: (origen / DIR_BORRADORES / f"{t.value}.md").read_text(encoding="utf-8")
        for t in ORDEN_ARTEFACTOS
        if (origen / DIR_BORRADORES / f"{t.value}.md").is_file()
    }
    return Conversion(paquete, borradores)


# --- desde el kit SDD embebido (.spec/units/) ------------------------------------------------

_ARCHIVOS_KIT = {Artefacto.spec: "spec.md", Artefacto.plan: "plan.md", Artefacto.tasks: "tasks.md"}


def _enum(tipo: type, valor: Any) -> Any:
    try:
        return tipo(valor) if valor else None
    except ValueError:
        return None


def _seccion(texto: str, titulo: str) -> str | None:
    """Cuerpo de la sección ``## <titulo...>`` de un Markdown, sin el encabezado."""

    lineas = texto.splitlines()
    for i, linea in enumerate(lineas):
        if linea.startswith("## ") and linea[3:].strip().lower().startswith(titulo.lower()):
            fin = next((j for j in range(i + 1, len(lineas)) if lineas[j].startswith("## ")), len(lineas))
            return "\n".join(lineas[i + 1 : fin]).strip() or None
    return None


def _gates_kit(gates: Any) -> list[GateImportado]:
    historial = []
    for nombre, datos in (gates or {}).items() if isinstance(gates, dict) else []:
        if not isinstance(datos, dict) or not datos.get("veredicto"):
            continue
        iteraciones = datos.get("iteraciones")
        historial.append(
            GateImportado(
                gate=str(nombre)[:40],
                resultado=str(datos["veredicto"])[:40],
                iteraciones=iteraciones if isinstance(iteraciones, int) and iteraciones >= 0 else None,
            )
        )
    return historial


def desde_unidad_sdd(carpeta: Path, repositorio: str | None = None) -> Conversion:
    """Convierte una unidad del kit SDD (``_estado.yaml`` + Markdown) en paquete."""

    import yaml

    ruta_estado = carpeta / "_estado.yaml"
    if not ruta_estado.is_file():
        raise ErrorRailspec(f"{carpeta} no es una unidad del kit SDD: falta _estado.yaml.")
    try:
        estado = yaml.safe_load(ruta_estado.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ErrorRailspec(f"{ruta_estado} no es YAML válido: {exc}") from exc
    if not isinstance(estado, dict):
        raise ErrorRailspec(f"{ruta_estado} no es un mapa YAML.")
    textos = {
        tipo: texto
        for tipo, nombre in _ARCHIVOS_KIT.items()
        if (carpeta / nombre).is_file() and (texto := (carpeta / nombre).read_text(encoding="utf-8")).strip()
    }
    id_original = str(estado.get("id") or carpeta.name)
    titulo = str(estado.get("titulo") or id_original)[:200]
    avisos: list[str] = []
    fase_origen = _enum(Fase, estado.get("fase"))
    if fase_origen is None:
        avisos.append(f"Fase de origen {estado.get('fase')!r} desconocida: se retoma en spec.")
        fase_origen = Fase.spec
    fase, aprobados, borradores = repartir(fase_origen, textos)
    if fase != fase_origen:
        avisos.append(
            f"El origen estaba en {fase_origen.value} sin {fase.value}.md: se retoma en {fase.value}."
        )
    modo = _enum(Modo, estado.get("modo"))
    if modo in MODOS_CON_MANDATO:
        avisos.append(f"Modo {modo.value} exige mandato: la unidad entra interactiva y el modo lo fijas tú.")
        modo = None
    # El pedido es lo que el humano pidió: el problema del spec si lo hay; si no, el título.
    spec = textos.get(Artefacto.spec)
    pedido = ((_seccion(spec, "Problema") if spec else None) or titulo)[:MAX_PEDIDO]
    comando = estado.get("comando_validacion")
    paquete = PaqueteUnidad(
        origen=OrigenPaquete(tipo="sdd-kit", id_original=id_original[:200], repositorio=repositorio),
        titulo=titulo,
        pedido=pedido,
        artefactos=ArtefactosPaquete(**{t.value: artefacto(t, textos[t]) for t in aprobados}),
        fase_retomar=fase,
        modo=modo,
        riesgo=_enum(Riesgo, estado.get("riesgo")),
        perfil=_enum(Perfil, estado.get("perfil")),
        governance_refs=[str(r) for r in estado.get("governance_refs") or []],
        comando_validacion=str(comando)[:2000] if comando else None,
        depende_de_original=[str(d) for d in estado.get("depende_de") or []],
        historial_gates=_gates_kit(estado.get("gates")),
    )
    return Conversion(paquete, {t: textos[t] for t in borradores}, avisos)


def leer_origen(ruta: Path, repositorio: str | None = None) -> Conversion:
    """Un paquete en disco o una unidad del kit SDD, según lo que haya en la carpeta."""

    if (ruta / MANIFIESTO).is_file():
        return leer_paquete(ruta)
    if (ruta / "_estado.yaml").is_file():
        return desde_unidad_sdd(ruta, repositorio)
    raise ErrorRailspec(
        f"{ruta} no es ni un paquete {FORMATO_PAQUETE} ({MANIFIESTO}) "
        "ni una unidad del kit SDD (_estado.yaml)."
    )


# --- exportar e importar contra el servidor ---------------------------------------------------


def dir_artefactos(unidad: str) -> str:
    # Misma ruta que usa el motor en las órdenes de redactar (``ruta_artefacto``).
    return f".railspec/unidades/{unidad}"


async def exportar(proxy: Any, unidad: str, destino: Path) -> Json:
    alcance = AlcanceUnidad(org=proxy.config.repo.org, workspace=proxy.config.repo.workspace, unidad=unidad)
    locales = proxy.unidades_locales()
    if unidad in locales and Almacen(locales[unidad]).existe():
        alcance = Almacen(locales[unidad]).leer().unidad  # conserva el plan (mandato) si lo hay
    salida = await proxy.cliente.llamar("unit.export", UnitExportEntrada(unidad=alcance), UnitExportSalida)
    escribir_paquete(Conversion(salida.paquete), destino)
    return {
        "paquete": str(destino),
        "artefactos": [t.value for t in artefactos_del_paquete(salida.paquete)],
        "fase_retomar": salida.paquete.fase_retomar.value,
    }


def _sin_secretos(conversion: Conversion) -> None:
    textos = {f"{t.value}.md": a.contenido for t, a in artefactos_del_paquete(conversion.paquete).items()}
    textos |= {f"{DIR_BORRADORES}/{t.value}.md": texto for t, texto in conversion.borradores.items()}
    textos["pedido"] = conversion.paquete.pedido
    hallazgos = [str(h) for ruta, texto in textos.items() for h in escanear(ruta, texto.encode("utf-8"))]
    if hallazgos:
        raise SecretosDetectados(hallazgos)


async def importar(proxy: Any, conversion: Conversion) -> Json:
    """Registra el paquete con ``unit.import`` y abre la unidad en local con sus artefactos."""

    _sin_secretos(conversion)
    repositorio = proxy.repositorio_inicio()
    entrada = UnitImportEntrada(
        alcance=AlcanceWorkspace(org=proxy.config.repo.org, workspace=proxy.config.repo.workspace),
        repositorios=[repositorio],
        arnes=proxy.config.repo.arnes,
        version_contrato_cliente=VERSION_CONTRATO,
        paquete=conversion.paquete,
    )
    salida = await proxy.cliente.llamar("unit.import", entrada, UnitImportSalida)
    unidad = salida.estado.unidad.unidad
    origen = conversion.paquete.origen.model_dump(exclude_none=True)
    if salida.ya_existia:
        locales = proxy.unidades_locales()
        return {
            "unidad": unidad,
            "ya_existia": True,
            "importada_de": origen,
            "worktree": str(locales[unidad]) if unidad in locales else None,
            "fase": salida.estado.fase.value,
            "avisos": conversion.avisos
            + [f"Este origen ya estaba importado como {unidad}; retómala con /railspec {unidad}."],
        }
    resultado = proxy.abrir_unidad_local(
        salida.estado, salida.version_contrato_negociada, repositorio.base_commit
    )
    carpeta = Path(resultado["worktree"]) / dir_artefactos(unidad)
    carpeta.mkdir(parents=True, exist_ok=True)
    textos = {t: a.contenido for t, a in artefactos_del_paquete(conversion.paquete).items()}
    textos |= conversion.borradores
    for tipo, texto in textos.items():
        (carpeta / f"{tipo.value}.md").write_text(texto, encoding="utf-8")
    return {
        **resultado,
        "ya_existia": False,
        "importada_de": origen,
        "aprobados": [t.value for t in artefactos_del_paquete(conversion.paquete)],
        "borradores": [t.value for t in conversion.borradores],
        "avisos": resultado["avisos"] + conversion.avisos,
    }
