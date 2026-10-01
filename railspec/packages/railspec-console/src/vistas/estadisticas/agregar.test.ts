import { describe, expect, it } from "vitest";
import type { FilaTelemetria } from "../../api/tipos";
import { estadoGasto, totalesPor } from "./agregar";

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
