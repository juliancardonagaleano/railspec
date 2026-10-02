import type { RefNodoGrafo, RefSimbolo, Relacion, ResultadoGrafo, RiesgoImpacto, TipoSimbolo } from "../../api/tipos";

/** Diferencia de un nodo en la vista base contra snapshot de una unidad. */
export type CambioNodo = "nuevo" | "eliminado" | "tocado";
export type CambioArista = "nueva" | "eliminada";

/** Nodo del navegador: solo metadatos (nombre, tipo, ruta); nunca código. */
export interface NodoModelo {
  id: string;
  nombre: string;
  tipo_simbolo: TipoSimbolo | "desconocido";
  repositorio: string;
  ruta: string;
  commit: string | null;
  /** Solo al comparar con una unidad: ausente = igual en la base y en el snapshot. */
  cambio?: CambioNodo;
}

export interface AristaModelo {
  id: string;
  origen: string;
  destino: string;
  relacion: Relacion | null;
  /** Distancia > 1: el contrato no da los intermedios, así que se une al centro como relación indirecta. */
  indirecta: boolean;
  cambio?: CambioArista;
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

/** Resultados de un vecindario: quién depende del centro (`up`), de qué depende (`down`) y sus clusters y procesos. */
export interface Vecindario {
  up: ResultadoGrafo[];
  down: ResultadoGrafo[];
  rel: ResultadoGrafo[];
}

const idArista = (origen: string, destino: string, relacion: Relacion | null | undefined) => `${origen}->${destino}:${relacion ?? "?"}`;

/** Ids de símbolo y de arista que un vecindario aporta alrededor de `centro`. */
function elementosDe(centro: string, v: Vecindario): { nodos: Set<string>; aristas: Set<string> } {
  const nodos = new Set<string>();
  const aristas = new Set<string>();
  for (const r of v.up.filter(esSimbolo)) {
    nodos.add(r.ref.simbolo);
    aristas.add(idArista(r.ref.simbolo, centro, r.relacion));
  }
  for (const r of v.down.filter(esSimbolo)) {
    nodos.add(r.ref.simbolo);
    aristas.add(idArista(centro, r.ref.simbolo, r.relacion));
  }
  nodos.delete(centro);
  return { nodos, aristas };
}

const sinDuplicados = (resultados: ResultadoGrafo[]): ResultadoGrafo[] => {
  const vistos = new Set<string>();
  return resultados.filter((r) => {
    const k = r.ref.tipo === "simbolo" ? `${r.ref.simbolo}|${r.relacion ?? ""}|${r.distancia ?? ""}` : JSON.stringify(r.ref);
    if (vistos.has(k)) return false;
    vistos.add(k);
    return true;
  });
};

/**
 * Funde el vecindario de `centro` visto en la base (canónico) y en el snapshot de una unidad
 * (canónico + superposición). Lo que solo está en el snapshot es `nuevo`; lo que solo está en la base,
 * `eliminado` (la superposición lo oculta con una lápida); lo que están en ambos y el impacto de la unidad
 * marca como tocado (`tocados`), `tocado`. Las aristas se marcan igual (`nueva`, `eliminada`). El impacto
 * del centro es el del snapshot: es lo que verá quien trabaje con la unidad.
 */
export function fusionarComparacion(
  modelo: ModeloGrafo,
  centro: NodoModelo,
  base: Vecindario,
  snapshot: Vecindario,
  tocados: ReadonlySet<string> = new Set(),
): ModeloGrafo {
  const union = fusionarVecindario(
    modelo,
    centro,
    sinDuplicados([...snapshot.up, ...base.up]),
    sinDuplicados([...snapshot.down, ...base.down]),
    sinDuplicados([...snapshot.rel, ...base.rel]),
  );
  const enBase = elementosDe(centro.id, base);
  const enSnapshot = elementosDe(centro.id, snapshot);

  const nodos = { ...union.nodos };
  const marcar = (id: string, cambio: CambioNodo | undefined) => {
    const n = nodos[id];
    if (n && cambio) nodos[id] = { ...n, cambio };
  };
  for (const id of new Set([...enBase.nodos, ...enSnapshot.nodos])) {
    const nuevo = enSnapshot.nodos.has(id) && !enBase.nodos.has(id);
    const eliminado = enBase.nodos.has(id) && !enSnapshot.nodos.has(id);
    marcar(id, nuevo ? "nuevo" : eliminado ? "eliminado" : tocados.has(id) ? "tocado" : undefined);
  }
  if (tocados.has(centro.id)) marcar(centro.id, "tocado");

  const aristas = { ...union.aristas };
  for (const id of new Set([...enBase.aristas, ...enSnapshot.aristas])) {
    const a = aristas[id];
    if (!a) continue;
    if (enSnapshot.aristas.has(id) && !enBase.aristas.has(id)) aristas[id] = { ...a, cambio: "nueva" };
    else if (enBase.aristas.has(id) && !enSnapshot.aristas.has(id)) aristas[id] = { ...a, cambio: "eliminada" };
  }

  const ups = snapshot.up.filter(esSimbolo);
  return {
    ...union,
    nodos,
    aristas,
    impacto: { ...union.impacto, [centro.id]: { total: ups.length, riesgoMax: riesgoMaximo(ups.map((r) => r.riesgo)) } },
  };
}

export const COLOR_CAMBIO: Record<CambioNodo | "igual", string> = {
  nuevo: "#16a34a",
  eliminado: "#dc2626",
  tocado: "#f59e0b",
  igual: "#94a3b8",
};

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
