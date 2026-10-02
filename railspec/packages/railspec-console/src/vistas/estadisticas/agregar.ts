import type { ClaveTelemetria, FilaTelemetria } from "../../api/tipos";

export interface Total {
  clave: string;
  llamadas: number;
  tokens_entrada: number;
  tokens_salida: number;
  tokens_cache_lectura: number;
  costo_usd: number;
  duracion_ms: number;
}

/** Suma las filas por el valor de una clave (`null` → "sin dato"), ordenadas por costo descendente. */
export function totalesPor(filas: readonly FilaTelemetria[], clave: ClaveTelemetria): Total[] {
  const mapa = new Map<string, Total>();
  for (const f of filas) {
    const k = f.claves[clave] ?? "sin dato";
    const t = mapa.get(k) ?? { clave: k, llamadas: 0, tokens_entrada: 0, tokens_salida: 0, tokens_cache_lectura: 0, costo_usd: 0, duracion_ms: 0 };
    t.llamadas += f.llamadas;
    t.tokens_entrada += f.tokens_entrada;
    t.tokens_salida += f.tokens_salida;
    t.tokens_cache_lectura += f.tokens_cache_lectura;
    t.costo_usd += f.costo_usd;
    t.duracion_ms += f.duracion_ms;
    mapa.set(k, t);
  }
  return [...mapa.values()].sort((a, b) => b.costo_usd - a.costo_usd);
}

/** Rango [desde, hasta] de los últimos `dias` días, en ISO. */
export function rangoDias(dias: number, ahora = new Date()): { desde: string; hasta: string } {
  const desde = new Date(ahora.getTime() - dias * 24 * 60 * 60 * 1000);
  return { desde: desde.toISOString(), hasta: ahora.toISOString() };
}

/** Porcentaje del presupuesto mensual consumido y nivel de alerta (≥ 80 % = alerta, ≥ 100 % = excedido). */
export function estadoGasto(mes: number, presupuesto: number | null): { pct: number | null; nivel: "ok" | "alerta" | "excedido" } {
  if (!presupuesto || presupuesto <= 0) return { pct: null, nivel: "ok" };
  const pct = (mes / presupuesto) * 100;
  return { pct, nivel: pct >= 100 ? "excedido" : pct >= 80 ? "alerta" : "ok" };
}

/** Suma de todos los grupos (fila de totales). */
export function sumar(totales: readonly Total[]): Total {
  return totales.reduce<Total>(
    (a, t) => ({
      clave: a.clave,
      llamadas: a.llamadas + t.llamadas,
      tokens_entrada: a.tokens_entrada + t.tokens_entrada,
      tokens_salida: a.tokens_salida + t.tokens_salida,
      tokens_cache_lectura: a.tokens_cache_lectura + t.tokens_cache_lectura,
      costo_usd: a.costo_usd + t.costo_usd,
      duracion_ms: a.duracion_ms + t.duracion_ms,
    }),
    { clave: "Total", llamadas: 0, tokens_entrada: 0, tokens_salida: 0, tokens_cache_lectura: 0, costo_usd: 0, duracion_ms: 0 },
  );
}

/**
 * Parte de la entrada que salió de la caché de prompts, en porcentaje. `tokens_entrada` no incluye lo leído de
 * caché (los proveedores lo registran aparte), así que la entrada total es la suma de ambos. `null` sin entrada.
 */
export function tasaCache(t: Pick<Total, "tokens_entrada" | "tokens_cache_lectura">): number | null {
  const entradaTotal = t.tokens_entrada + t.tokens_cache_lectura;
  return entradaTotal > 0 ? (t.tokens_cache_lectura / entradaTotal) * 100 : null;
}

/** Duración media por llamada, en milisegundos; `null` sin llamadas. */
export function duracionMediaMs(t: Pick<Total, "llamadas" | "duracion_ms">): number | null {
  return t.llamadas > 0 ? t.duracion_ms / t.llamadas : null;
}
