import { describe, expect, it } from "vitest";
import { FASES, type ResumenUnidad } from "../../api/tipos";
import { agruparEnCarriles, cambiadas, huellas } from "./agrupar";

const unidad = (id: string, parcial: Partial<ResumenUnidad> = {}): ResumenUnidad => ({
  unidad: id,
  titulo: `Unidad ${id}`,
  fase: "spec",
  estado: "en-progreso",
  modo: "interactivo",
  riesgo: "bajo",
  repositorio_primario: "api",
  dueno_login: "ana",
  integrada: false,
  actualizado_en: "2026-10-02T10:00:00Z",
  ...parcial,
});

describe("carriles del tablero", () => {
  const lista = [
    unidad("0001-a", { repositorio_primario: "web", fase: "plan" }),
    unidad("0002-b", { repositorio_primario: "api" }),
    unidad("0003-c", { repositorio_primario: "web", fase: "done" }),
  ];

  it("sin criterio hay un único carril sin título con todas las columnas", () => {
    const [unico, ...resto] = agruparEnCarriles(lista);
    expect(resto).toEqual([]);
    expect(unico?.etiqueta).toBe("");
    expect(unico?.total).toBe(3);
    expect(Object.keys(unico!.columnas)).toEqual([...FASES]);
  });

  it("reparte por repositorio, ordenado por nombre y con todas las columnas en cada carril", () => {
    const carriles = agruparEnCarriles(lista, "repositorio");
    expect(carriles.map((c) => [c.etiqueta, c.total])).toEqual([["api", 1], ["web", 2]]);
    expect(carriles[1]!.columnas.plan.map((u) => u.unidad)).toEqual(["0001-a"]);
    expect(carriles[1]!.columnas.done.map((u) => u.unidad)).toEqual(["0003-c"]);
    expect(Object.keys(carriles[0]!.columnas)).toEqual([...FASES]);
  });

  it("por dueño deja «sin dueño» al final", () => {
    const carriles = agruparEnCarriles(
      [unidad("0001-a", { dueno_login: null }), unidad("0002-b", { dueno_login: "zoe" }), unidad("0003-c", { dueno_login: "ana" })],
      "dueno",
    );
    expect(carriles.map((c) => c.etiqueta)).toEqual(["ana", "zoe", "sin dueño"]);
  });

  it("por riesgo pone primero el más grave", () => {
    const carriles = agruparEnCarriles(
      [unidad("0001-a", { riesgo: "bajo" }), unidad("0002-b", { riesgo: "alto" }), unidad("0003-c", { riesgo: "medio" })],
      "riesgo",
    );
    expect(carriles.map((c) => c.etiqueta)).toEqual(["alto", "medio", "bajo"]);
  });
});

describe("cambios entre lecturas del tablero", () => {
  const antes = [unidad("0001-a"), unidad("0002-b", { fase: "plan" })];

  it("en la primera lectura no marca nada", () => {
    expect(cambiadas(null, antes).size).toBe(0);
  });

  it("marca las unidades nuevas y las que cambiaron de fase, estado o actualización", () => {
    const despues = [
      unidad("0001-a"),
      unidad("0002-b", { fase: "tasks" }),
      unidad("0003-c"),
      unidad("0004-d", { actualizado_en: "2026-10-02T11:00:00Z" }),
    ];
    const previa = huellas([...antes, unidad("0004-d")]);
    expect([...cambiadas(previa, despues)].sort()).toEqual(["0002-b", "0003-c", "0004-d"]);
  });

  it("sin cambios no marca nada", () => {
    expect(cambiadas(huellas(antes), antes).size).toBe(0);
  });
});
