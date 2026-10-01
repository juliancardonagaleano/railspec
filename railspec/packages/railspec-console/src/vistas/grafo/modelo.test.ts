import { describe, expect, it } from "vitest";
import type { RefSimbolo, ResultadoGrafo } from "../../api/tipos";
import { separarImpacto } from "../unidad/PestanaImpacto";
import { fusionarVecindario, MODELO_VACIO, nodoDeRef, riesgoMaximo } from "./modelo";

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
