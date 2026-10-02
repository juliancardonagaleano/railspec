import { describe, expect, it } from "vitest";
import type { FilaTelemetria } from "../../api/tipos";
import { duracion, porcentaje } from "../../lib/utiles";
import { duracionMediaMs, estadoGasto, sumar, tasaCache, totalesPor } from "./agregar";

const fila = (claves: FilaTelemetria["claves"], costo: number, llamadas = 1): FilaTelemetria => ({
  claves,
  llamadas,
  tokens_entrada: 100,
  tokens_salida: 10,
  tokens_cache_lectura: 0,
  costo_usd: costo,
  duracion_ms: 5,
});

describe("estadísticas", () => {
  it("suma por clave y ordena por costo", () => {
    const t = totalesPor([fila({ fase: "spec" }, 1), fila({ fase: "plan" }, 3), fila({ fase: "spec" }, 1.5, 2), fila({ fase: null }, 0.1)], "fase");
    expect(t.map((x) => x.clave)).toEqual(["plan", "spec", "sin dato"]);
    expect(t[1]).toMatchObject({ llamadas: 3, costo_usd: 2.5, tokens_entrada: 200 });
  });

  it("alerta al pasar el 80 % del presupuesto mensual", () => {
    expect(estadoGasto(50, 100).nivel).toBe("ok");
    expect(estadoGasto(80, 100).nivel).toBe("alerta");
    expect(estadoGasto(120, 100).nivel).toBe("excedido");
    expect(estadoGasto(10, null)).toEqual({ pct: null, nivel: "ok" });
  });
});

describe("duración y caché", () => {
  const f = (claves: FilaTelemetria["claves"], o: Partial<FilaTelemetria>): FilaTelemetria => ({ ...fila(claves, 1), ...o });

  it("suma la duración y da la media por llamada", () => {
    const [t] = totalesPor([f({ nodo: "gate" }, { llamadas: 2, duracion_ms: 3000 }), f({ nodo: "gate" }, { llamadas: 2, duracion_ms: 1000 })], "nodo");
    expect(t).toMatchObject({ llamadas: 4, duracion_ms: 4000 });
    expect(duracionMediaMs(t!)).toBe(1000);
    expect(duracionMediaMs({ llamadas: 0, duracion_ms: 0 })).toBeNull();
  });

  it("la tasa de caché es lo leído de caché sobre la entrada total (entrada + caché)", () => {
    expect(tasaCache({ tokens_entrada: 100, tokens_cache_lectura: 300 })).toBe(75);
    expect(tasaCache({ tokens_entrada: 200, tokens_cache_lectura: 0 })).toBe(0);
    expect(tasaCache({ tokens_entrada: 0, tokens_cache_lectura: 0 })).toBeNull();
  });

  it("suma todos los grupos en una fila de totales", () => {
    const total = sumar(totalesPor([f({ tier: "alto" }, { costo_usd: 2, duracion_ms: 10 }), f({ tier: "bajo" }, { costo_usd: 0.5, duracion_ms: 5 })], "tier"));
    expect(total).toMatchObject({ clave: "Total", llamadas: 2, costo_usd: 2.5, duracion_ms: 15 });
  });

  it("formatea duraciones y porcentajes", () => {
    expect(duracion(850)).toBe("850 ms");
    expect(duracion(12_400)).toBe("12,4 s");
    expect(duracion(185_000)).toBe("3 min 05 s");
    expect(duracion(null)).toBe("—");
    expect(porcentaje(74.6)).toBe("75 %");
    expect(porcentaje(null)).toBe("—");
  });
});
