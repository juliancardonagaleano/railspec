import { describe, expect, it } from "vitest";
import { FASES, type Fase, type ResumenUnidad } from "../../api/tipos";
import { agruparPorFase, unirPaginas } from "./agrupar";

const unidad = (id: string, fase: Fase): ResumenUnidad => ({
  unidad: id,
  titulo: `Unidad ${id}`,
  fase,
  estado: "en-progreso",
  modo: "interactivo",
  riesgo: "medio",
  repositorio_primario: "api",
  dueno_login: "ana",
  integrada: false,
  actualizado_en: "2026-09-01T00:00:00Z",
});

describe("tablero", () => {
  it("agrupa por fase con todas las columnas, en el orden de llegada", () => {
    const columnas = agruparPorFase([
      unidad("0001-a", "spec"),
      unidad("0002-b", "implement"),
      unidad("0003-c", "spec"),
      unidad("0004-d", "done"),
    ]);
    expect(Object.keys(columnas)).toEqual([...FASES]);
    expect(columnas.spec.map((u) => u.unidad)).toEqual(["0001-a", "0003-c"]);
    expect(columnas.implement).toHaveLength(1);
    expect(columnas.done).toHaveLength(1);
    expect(columnas.research).toEqual([]);
    expect(columnas.aprobacion).toEqual([]);
  });

  it("une páginas del cursor sin duplicar unidades", () => {
    const lista = unirPaginas([
      { unidades: [unidad("0001-a", "spec"), unidad("0002-b", "plan")] },
      { unidades: [unidad("0002-b", "plan"), unidad("0003-c", "tasks")] },
    ]);
    expect(lista.map((u) => u.unidad)).toEqual(["0001-a", "0002-b", "0003-c"]);
  });
});
