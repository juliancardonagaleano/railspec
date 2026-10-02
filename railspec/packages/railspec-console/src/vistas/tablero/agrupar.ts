import { FASES, type Fase, type ResumenUnidad } from "../../api/tipos";

/** Agrupa las unidades por fase, conservando el orden de llegada y todas las columnas (aunque vacías). */
export function agruparPorFase(unidades: readonly ResumenUnidad[]): Record<Fase, ResumenUnidad[]> {
  const columnas = Object.fromEntries(FASES.map((f) => [f, [] as ResumenUnidad[]])) as Record<Fase, ResumenUnidad[]>;
  for (const u of unidades) {
    const col = columnas[u.fase];
    if (col) col.push(u);
  }
  return columnas;
}

/** Quita duplicados (por id de unidad) al concatenar páginas por cursor. */
export function unirPaginas(paginas: readonly { unidades: ResumenUnidad[] }[]): ResumenUnidad[] {
  const vistas = new Set<string>();
  const salida: ResumenUnidad[] = [];
  for (const p of paginas)
    for (const u of p.unidades) {
      if (vistas.has(u.unidad)) continue;
      vistas.add(u.unidad);
      salida.push(u);
    }
  return salida;
}

/** Criterios por los que el tablero reparte sus unidades en carriles horizontales. */
export const CRITERIOS_CARRIL = ["repositorio", "dueno", "modo", "riesgo"] as const;
export type CriterioCarril = (typeof CRITERIOS_CARRIL)[number];

export const NOMBRE_CRITERIO_CARRIL: Record<CriterioCarril, string> = {
  repositorio: "Repositorio",
  dueno: "Dueño",
  modo: "Modo",
  riesgo: "Riesgo",
};

export interface Carril {
  /** Valor del criterio; "" en el carril único cuando no se reparte. */
  clave: string;
  etiqueta: string;
  total: number;
  columnas: Record<Fase, ResumenUnidad[]>;
}

const SIN_DUENO = "sin dueño";

function claveDe(u: ResumenUnidad, criterio: CriterioCarril): string {
  switch (criterio) {
    case "repositorio":
      return u.repositorio_primario;
    case "dueno":
      return u.dueno_login ?? SIN_DUENO;
    case "modo":
      return u.modo;
    case "riesgo":
      return u.riesgo;
  }
}

const ORDEN_RIESGO: Record<string, number> = { alto: 0, medio: 1, bajo: 2 };

/**
 * Reparte las unidades en carriles por `criterio` (cada carril con todas las columnas de fase). Sin
 * criterio hay un único carril sin título. Los carriles salen por nombre (por gravedad en riesgo);
 * dentro de cada uno se conserva el orden de llegada.
 */
export function agruparEnCarriles(unidades: readonly ResumenUnidad[], criterio?: CriterioCarril): Carril[] {
  if (!criterio) return [{ clave: "", etiqueta: "", total: unidades.length, columnas: agruparPorFase(unidades) }];
  const porClave = new Map<string, ResumenUnidad[]>();
  for (const u of unidades) {
    const k = claveDe(u, criterio);
    porClave.set(k, [...(porClave.get(k) ?? []), u]);
  }
  const claves = [...porClave.keys()].sort((a, b) =>
    criterio === "riesgo" ? (ORDEN_RIESGO[a] ?? 9) - (ORDEN_RIESGO[b] ?? 9) : a === SIN_DUENO ? 1 : b === SIN_DUENO ? -1 : a.localeCompare(b),
  );
  return claves.map((clave) => {
    const lista = porClave.get(clave) ?? [];
    return { clave, etiqueta: clave, total: lista.length, columnas: agruparPorFase(lista) };
  });
}

/** Lo que identifica un cambio visible de una unidad entre dos lecturas del tablero. */
const huella = (u: ResumenUnidad): string =>
  [u.fase, u.estado, u.modo, u.riesgo, u.integrada, u.checkpoint_pendiente ?? "", u.actualizado_en].join("|");

export function huellas(unidades: readonly ResumenUnidad[]): Map<string, string> {
  return new Map(unidades.map((u) => [u.unidad, huella(u)]));
}

/** Ids de las unidades nuevas o cambiadas respecto de la lectura anterior (vacío si no había anterior). */
export function cambiadas(anterior: ReadonlyMap<string, string> | null, unidades: readonly ResumenUnidad[]): Set<string> {
  if (!anterior) return new Set();
  return new Set(unidades.filter((u) => anterior.get(u.unidad) !== huella(u)).map((u) => u.unidad));
}
