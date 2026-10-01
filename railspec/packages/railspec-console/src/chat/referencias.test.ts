import { describe, expect, it } from "vitest";
import { comandoPull, etiquetaReferencia, explicarRegla, EXPLICACION_REGLA, shaCorto } from "./referencias";
import type { Insumo, ReglaGate } from "./tipos";

describe("etiquetaReferencia", () => {
  it("archivo con rango de líneas", () => {
    expect(
      etiquetaReferencia({
        tipo: "archivo",
        repositorio: "acme/api",
        commit: "abc",
        ruta: "src/x.py",
        linea_inicio: 10,
        linea_fin: 20,
      }),
    ).toBe("acme/api · src/x.py:10-20");
  });

  it("archivo con una línea o sin líneas", () => {
    const base = { tipo: "archivo" as const, repositorio: "acme/api", commit: "abc", ruta: "src/x.py" };
    expect(etiquetaReferencia({ ...base, linea_inicio: 5 })).toBe("acme/api · src/x.py:5");
    expect(etiquetaReferencia({ ...base, linea_inicio: 5, linea_fin: 5 })).toBe("acme/api · src/x.py:5");
    expect(etiquetaReferencia(base)).toBe("acme/api · src/x.py");
    expect(etiquetaReferencia({ ...base, linea_inicio: null, linea_fin: null })).toBe("acme/api · src/x.py");
  });

  it("símbolo", () => {
    expect(
      etiquetaReferencia({
        tipo: "simbolo",
        repositorio: "acme/api",
        commit: "abc",
        simbolo: "src/x.py::calcular_total",
        nombre: "calcular_total",
        tipo_simbolo: "function",
        ruta: "src/x.py",
      }),
    ).toBe("símbolo calcular_total");
  });

  it("nodo del grafo", () => {
    expect(
      etiquetaReferencia({
        tipo: "nodo-grafo",
        repositorio: "acme/api",
        commit: "abc",
        clase: "proceso",
        id: "p-1",
        nombre: "Checkout",
      }),
    ).toBe("proceso Checkout");
  });

  it("unidad, criterio, gobernanza y decisión", () => {
    expect(etiquetaReferencia({ tipo: "unidad", workspace: "w", unidad: "0003-chat-contexto" })).toBe(
      "unidad 0003-chat-contexto",
    );
    expect(etiquetaReferencia({ tipo: "criterio", workspace: "w", unidad: "0003", criterio: "CA-02" })).toBe("CA-02");
    expect(etiquetaReferencia({ tipo: "gobernanza", id: "GOB-1", proveedor: "pce" })).toBe("gobernanza GOB-1");
    expect(etiquetaReferencia({ tipo: "decision", workspace: "w", id: "D-4" })).toBe("decisión D-4");
  });
});

describe("comandoPull", () => {
  it("arma el comando del CLI con el id del insumo", () => {
    const insumo = { id: "ins_01HZX" } as Pick<Insumo, "id">;
    expect(comandoPull(insumo)).toBe("railspec insumo pull ins_01HZX");
  });
});

describe("reglas del gate", () => {
  it("explica todas las reglas conocidas y tolera desconocidas", () => {
    const reglas: ReglaGate[] = [
      "esquema",
      "huella-contexto",
      "normalizacion",
      "forma-codigo",
      "secretos",
      "alcance",
      "presupuesto-fuga",
    ];
    for (const r of reglas) expect(explicarRegla(r)).toBe(EXPLICACION_REGLA[r]);
    expect(explicarRegla("nueva")).toContain("nueva");
  });

  it("abrevia sha256", () => {
    expect(shaCorto("0123456789abcdef0123")).toBe("0123456789ab");
  });
});
