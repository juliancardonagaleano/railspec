import type { RefNodoGrafo, RefSimbolo, Relacion, ResultadoGrafo, RiesgoImpacto, TipoSimbolo } from "../../api/tipos";

/** Nodo del navegador: solo metadatos (nombre, tipo, ruta); nunca código. */
export interface NodoModelo {
  id: string;
  nombre: string;
  tipo_simbolo: TipoSimbolo | "desconocido";
  repositorio: string;
  ruta: string;
  commit: string | null;
}

export interface AristaModelo {
  id: string;
  origen: string;
  destino: string;
  relacion: Relacion | null;
  /** Distancia > 1: el contrato no da los intermedios, así que se une al centro como relación indirecta. */
  indirecta: boolean;
}

export interface ImpactoUpstream {
  total: number;
  riesgoMax: RiesgoImpacto | null;
}

export interface ModeloGrafo {
  nodos: Record<string, NodoModelo>;
  aristas: Record<string, AristaModelo>;
  impacto: Record<string, ImpactoUpstream>;
  relacionados: Record<string, RefNodoGrafo[]>;
  expandidos: string[];
}

export const MODELO_VACIO: ModeloGrafo = { nodos: {}, aristas: {}, impacto: {}, relacionados: {}, expandidos: [] };

export function nodoDeRef(ref: RefSimbolo): NodoModelo {
  return {
    id: ref.simbolo,
    nombre: ref.nombre,
    tipo_simbolo: ref.tipo_simbolo,
    repositorio: ref.repositorio,
    ruta: ref.ruta,
    commit: ref.commit,
  };
}

const ORDEN_RIESGO: RiesgoImpacto[] = ["bajo", "medio", "alto", "critico"];

export function riesgoMaximo(riesgos: (RiesgoImpacto | null | undefined)[]): RiesgoImpacto | null {
  let max: RiesgoImpacto | null = null;
  for (const r of riesgos) if (r && (max === null || ORDEN_RIESGO.indexOf(r) > ORDEN_RIESGO.indexOf(max))) max = r;
  return max;
}

const esSimbolo = (r: ResultadoGrafo): r is ResultadoGrafo & { ref: RefSimbolo } => r.ref.tipo === "simbolo";

/**
 * Funde en el modelo el vecindario de `centro`: los resultados upstream apuntan
 * al centro (dependen de él) y los downstream salen del centro.
 */
export function fusionarVecindario(
  modelo: ModeloGrafo,
  centro: NodoModelo,
  upstream: ResultadoGrafo[],
  downstream: ResultadoGrafo[],
  relacionados: ResultadoGrafo[] = [],
): ModeloGrafo {
  const nodos = { ...modelo.nodos };
  const aristas = { ...modelo.aristas };
  nodos[centro.id] = { ...nodos[centro.id], ...centro, commit: centro.commit ?? nodos[centro.id]?.commit ?? null };

  const agregar = (r: ResultadoGrafo & { ref: RefSimbolo }, sentido: "up" | "down") => {
    const n = nodoDeRef(r.ref);
    if (n.id === centro.id) return;
    nodos[n.id] = nodos[n.id] ?? n;
    const [origen, destino] = sentido === "up" ? [n.id, centro.id] : [centro.id, n.id];
    const id = `${origen}->${destino}:${r.relacion ?? "?"}`;
    aristas[id] = { id, origen, destino, relacion: r.relacion ?? null, indirecta: (r.distancia ?? 1) > 1 };
  };
  const ups = upstream.filter(esSimbolo);
  ups.forEach((r) => agregar(r, "up"));
  downstream.filter(esSimbolo).forEach((r) => agregar(r, "down"));

  return {
    nodos,
    aristas,
    impacto: { ...modelo.impacto, [centro.id]: { total: ups.length, riesgoMax: riesgoMaximo(ups.map((r) => r.riesgo)) } },
    relacionados: {
      ...modelo.relacionados,
      [centro.id]: relacionados.map((r) => r.ref).filter((ref): ref is RefNodoGrafo => ref.tipo === "nodo-grafo"),
    },
    expandidos: modelo.expandidos.includes(centro.id) ? modelo.expandidos : [...modelo.expandidos, centro.id],
  };
}

export const COLOR_TIPO: Record<NodoModelo["tipo_simbolo"], string> = {
  modulo: "#6366f1",
  clase: "#0ea5e9",
  interfaz: "#14b8a6",
  funcion: "#22c55e",
  metodo: "#84cc16",
  variable: "#f59e0b",
  otro: "#94a3b8",
  desconocido: "#64748b",
};

export const COLOR_RELACION: Record<Relacion, string> = {
  llama: "#64748b",
  importa: "#8b5cf6",
  hereda: "#ef4444",
  implementa: "#f97316",
  define: "#0ea5e9",
  prueba: "#22c55e",
};

const PALETA_REPOS = ["#2563eb", "#db2777", "#16a34a", "#ea580c", "#7c3aed", "#0891b2", "#ca8a04", "#dc2626"];

/** Color estable por repositorio (hash del nombre). */
export function colorRepositorio(repo: string): string {
  let h = 0;
  for (let i = 0; i < repo.length; i++) h = (h * 31 + repo.charCodeAt(i)) >>> 0;
  return PALETA_REPOS[h % PALETA_REPOS.length]!;
}
