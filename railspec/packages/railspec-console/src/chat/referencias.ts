// Etiquetas humanas cortas para referencias y utilidades de insumo.

import type { Insumo, Referencia, ReglaGate } from "./tipos";

function rango(inicio?: number | null, fin?: number | null): string {
  if (inicio == null) return "";
  if (fin == null || fin === inicio) return `:${inicio}`;
  return `:${inicio}-${fin}`;
}

/** Etiqueta corta de una referencia, p. ej. "acme/api · src/x.py:10-20". */
export function etiquetaReferencia(ref: Referencia): string {
  switch (ref.tipo) {
    case "simbolo":
      return `símbolo ${ref.nombre}`;
    case "archivo":
      return `${ref.repositorio} · ${ref.ruta}${rango(ref.linea_inicio, ref.linea_fin)}`;
    case "nodo-grafo":
      return `${ref.clase} ${ref.nombre}`;
    case "unidad":
      return `unidad ${ref.unidad}`;
    case "criterio":
      return ref.criterio;
    case "gobernanza":
      return `gobernanza ${ref.id}`;
    case "decision":
      return `decisión ${ref.id}`;
  }
}

/** Descripción larga (para `title`), con commit abreviado cuando aplica. */
export function detalleReferencia(ref: Referencia): string {
  const c = (commit: string) => commit.slice(0, 12);
  switch (ref.tipo) {
    case "simbolo":
      return `${ref.tipo_simbolo} ${ref.simbolo} en ${ref.repositorio}/${ref.ruta} @ ${c(ref.commit)}`;
    case "archivo":
      return `${ref.repositorio}/${ref.ruta}${rango(ref.linea_inicio, ref.linea_fin)} @ ${c(ref.commit)}`;
    case "nodo-grafo":
      return `${ref.clase} ${ref.id} (${ref.nombre}) en ${ref.repositorio} @ ${c(ref.commit)}`;
    case "unidad":
      return `unidad ${ref.unidad} del workspace ${ref.workspace}`;
    case "criterio":
      return `criterio ${ref.criterio} de la unidad ${ref.unidad} (${ref.workspace})`;
    case "gobernanza":
      return `gobernanza ${ref.id} (${ref.proveedor}${ref.hash_version ? `, ${ref.hash_version}` : ""})`;
    case "decision":
      return `decisión ${ref.id} del workspace ${ref.workspace}`;
  }
}

/** Comando del CLI local para traer el insumo exportado. */
export function comandoPull(insumo: Pick<Insumo, "id">): string {
  return `railspec insumo pull ${insumo.id}`;
}

/** Explicación en castellano de cada regla del gate de salida. */
export const EXPLICACION_REGLA: Record<ReglaGate, string> = {
  esquema: "La respuesta no cumplía el esquema estructurado esperado (afirmaciones con referencias).",
  "huella-contexto":
    "La respuesta reproducía fragmentos literales del contexto leído (huella coincidente con el código fuente).",
  normalizacion: "Tras normalizar el texto se detectó contenido que intentaba eludir los controles.",
  "forma-codigo": "La respuesta tenía forma de código fuente; el chat sólo devuelve explicaciones y referencias.",
  secretos: "Se detectó algo con aspecto de secreto o credencial.",
  alcance: "La respuesta citaba repositorios o recursos fuera del alcance de la conversación.",
  "presupuesto-fuga": "Se agotó el presupuesto de caracteres derivados del código para esta conversación o usuario.",
};

export function explicarRegla(regla: string): string {
  return (EXPLICACION_REGLA as Record<string, string>)[regla] ?? `Regla «${regla}» del gate de salida.`;
}

/** Abrevia un sha256 para mostrarlo. */
export function shaCorto(sha: string, largo = 12): string {
  return sha.slice(0, largo);
}
