import type { ClaveTelemetria, FilaTelemetria } from "../../api/tipos";

export interface Total {
  clave: string;
  llamadas: number;
  tokens_entrada: number;
  tokens_salida: number;
  tokens_cache_lectura: number;
  costo_usd: number;
}

/** Suma las filas por el valor de una clave (`null` → "sin dato"), ordenadas por costo descendente. */
export function totalesPor(filas: readonly FilaTelemetria[], clave: ClaveTelemetria): Total[] {
  const mapa = new Map<string, Total>();
  for (const f of filas) {
    const k = f.claves[clave] ?? "sin dato";
    const t = mapa.get(k) ?? { clave: k, llamadas: 0, tokens_entrada: 0, tokens_salida: 0, tokens_cache_lectura: 0, costo_usd: 0 };
    t.llamadas += f.llamadas;
    t.tokens_entrada += f.tokens_entrada;
    t.tokens_salida += f.tokens_salida;
    t.tokens_cache_lectura += f.tokens_cache_lectura;
    t.costo_usd += f.costo_usd;
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
