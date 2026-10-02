import { describe, expect, it } from "vitest";
import type { RefSimbolo, ResultadoGrafo } from "../../api/tipos";
import { separarImpacto } from "../unidad/PestanaImpacto";
import { fusionarComparacion, fusionarVecindario, MODELO_VACIO, nodoDeRef, riesgoMaximo, type Vecindario } from "./modelo";

const sha = (c: string) => c.repeat(64);
const ref = (c: string, nombre: string): RefSimbolo => ({
  tipo: "simbolo",
  repositorio: "api",
  commit: "a".repeat(40),
  simbolo: sha(c),
  nombre,
  tipo_simbolo: "funcion",
  ruta: `src/${nombre}.py`,
});

describe("modelo del grafo", () => {
  it("upstream apunta al centro, downstream sale del centro y calcula el impacto", () => {
    const centro = nodoDeRef(ref("1", "centro"));
    const up: ResultadoGrafo[] = [
      { ref: ref("2", "llamador"), relacion: "llama", distancia: 1, riesgo: "medio" },
      { ref: ref("3", "lejano"), relacion: "llama", distancia: 2, riesgo: "alto" },
    ];
    const down: ResultadoGrafo[] = [{ ref: ref("4", "llamado"), relacion: "importa", distancia: 1 }];
    const m = fusionarVecindario(MODELO_VACIO, centro, up, down);
    expect(Object.keys(m.nodos)).toHaveLength(4);
    const aristas = Object.values(m.aristas);
    expect(aristas.find((a) => a.origen === sha("2"))?.destino).toBe(sha("1"));
    expect(aristas.find((a) => a.destino === sha("4"))?.origen).toBe(sha("1"));
    expect(aristas.find((a) => a.origen === sha("3"))?.indirecta).toBe(true);
    expect(m.impacto[sha("1")]).toEqual({ total: 2, riesgoMax: "alto" });
    expect(m.expandidos).toEqual([sha("1")]);
  });

  it("riesgo máximo respeta bajo < medio < alto < crítico", () => {
    expect(riesgoMaximo(["bajo", null, "critico", "alto"])).toBe("critico");
    expect(riesgoMaximo([])).toBeNull();
  });

  it("el impacto separa tocados (distancia 0) de afectados", () => {
    const { tocados, afectados } = separarImpacto([
      { ref: ref("1", "a"), distancia: 0, riesgo: "alto" },
      { ref: ref("2", "b"), distancia: 2, relacion: "llama", riesgo: "alto" },
      { ref: ref("3", "c"), distancia: 1, relacion: "importa", riesgo: "alto" },
      { ref: { tipo: "criterio", workspace: "w", unidad: "0001-x", criterio: "CA-01" } },
    ]);
    expect(tocados.map((r) => r.ref.nombre)).toEqual(["a"]);
    expect(afectados.map((r) => r.ref.nombre)).toEqual(["c", "b"]);
  });
});

describe("base contra snapshot de una unidad", () => {
  const centro = nodoDeRef(ref("1", "centro"));
  const res = (c: string, nombre: string, relacion: ResultadoGrafo["relacion"] = "llama", extra: Partial<ResultadoGrafo> = {}): ResultadoGrafo => ({
    ref: ref(c, nombre),
    relacion,
    distancia: 1,
    ...extra,
  });
  const vacio: Vecindario = { up: [], down: [], rel: [] };

  // Base: la llaman «viejo» y «comun», y llama a «borrado». Snapshot: ya no llama a «borrado», lo llama «nuevo».
  const base: Vecindario = { ...vacio, up: [res("2", "viejo"), res("3", "comun")], down: [res("4", "borrado", "importa")] };
  const snapshot: Vecindario = { ...vacio, up: [res("3", "comun"), res("5", "nuevo", "llama", { riesgo: "alto" })], down: [] };

  it("marca lo que solo está en el snapshot como nuevo y lo que solo está en la base como eliminado", () => {
    const m = fusionarComparacion(MODELO_VACIO, centro, base, snapshot);
    expect(Object.keys(m.nodos)).toHaveLength(5);
    expect(m.nodos[sha("5")]?.cambio).toBe("nuevo");
    expect(m.nodos[sha("2")]?.cambio).toBe("eliminado");
    expect(m.nodos[sha("4")]?.cambio).toBe("eliminado");
    expect(m.nodos[sha("3")]?.cambio).toBeUndefined();
  });

  it("marca como tocados los símbolos que la unidad cambia y que existen en ambos lados", () => {
    const m = fusionarComparacion(MODELO_VACIO, centro, base, snapshot, new Set([sha("3"), sha("1")]));
    expect(m.nodos[sha("3")]?.cambio).toBe("tocado");
    expect(m.nodos[sha("1")]?.cambio).toBe("tocado");
    // Lo nuevo sigue siendo nuevo aunque la unidad también lo toque.
    expect(fusionarComparacion(MODELO_VACIO, centro, base, snapshot, new Set([sha("5")])).nodos[sha("5")]?.cambio).toBe("nuevo");
  });

  it("marca las aristas nuevas y eliminadas y deja las comunes sin marca", () => {
    const m = fusionarComparacion(MODELO_VACIO, centro, base, snapshot);
    const aristas = Object.values(m.aristas);
    expect(aristas).toHaveLength(4);
    expect(aristas.find((a) => a.origen === sha("5"))?.cambio).toBe("nueva");
    expect(aristas.find((a) => a.origen === sha("2"))?.cambio).toBe("eliminada");
    expect(aristas.find((a) => a.destino === sha("4"))?.cambio).toBe("eliminada");
    expect(aristas.find((a) => a.origen === sha("3"))?.cambio).toBeUndefined();
  });

  it("el impacto del centro es el del snapshot, no el de la unión", () => {
    const m = fusionarComparacion(MODELO_VACIO, centro, base, snapshot);
    expect(m.impacto[sha("1")]).toEqual({ total: 2, riesgoMax: "alto" });
    expect(m.expandidos).toEqual([sha("1")]);
  });

  it("una segunda expansión conserva las marcas de la primera", () => {
    const una = fusionarComparacion(MODELO_VACIO, centro, base, snapshot);
    const otro = nodoDeRef(ref("3", "comun"));
    const dos = fusionarComparacion(una, otro, vacio, { ...vacio, down: [res("6", "otro-nuevo")] });
    expect(dos.nodos[sha("5")]?.cambio).toBe("nuevo");
    expect(dos.nodos[sha("6")]?.cambio).toBe("nuevo");
  });

  it("sin diferencias no marca nada", () => {
    const m = fusionarComparacion(MODELO_VACIO, centro, base, base);
    expect(Object.values(m.nodos).every((n) => n.cambio === undefined)).toBe(true);
    expect(Object.values(m.aristas).every((a) => a.cambio === undefined)).toBe(true);
  });
});
