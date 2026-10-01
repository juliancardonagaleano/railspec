import { describe, expect, it } from "vitest";
import type { ResultadoGate } from "../../api/tipos";
import { calcularDag, estadoFase, estadoGate } from "./dag";

const gate = (veredicto: ResultadoGate["veredicto"], rehabilitado = false): ResultadoGate => ({
  veredicto,
  iteraciones: 1,
  gobernanza_consultada: "si",
  cerrado_en: "2026-09-01T00:00:00Z",
  ...(rehabilitado
    ? { rehabilitado: { actor: { tipo: "humano", canal: "consola", login: "ana" }, en: "2026-09-02T00:00:00Z", motivo: "ok" } }
    : {}),
});

const estados = (dag: ReturnType<typeof calcularDag>) => Object.fromEntries(dag.nodos.map((n) => [n.id, n.estado]));

describe("DAG de fases", () => {
  it("sigue el orden spec → gate spec → plan → … → gate código → done", () => {
    const { nodos, aristas } = calcularDag({ fase: "spec", estado: "en-progreso", gates: {} });
    expect(nodos.map((n) => n.id)).toEqual([
      "fase-research",
      "fase-spec",
      "gate-spec",
      "fase-plan",
      "gate-plan",
      "fase-tasks",
      "gate-tasks",
      "fase-aprobacion",
      "fase-implement",
      "gate-codigo",
      "fase-done",
    ]);
    expect(aristas).toHaveLength(nodos.length - 1);
  });

  it("marca hecho lo anterior, actual la fase en curso y pendiente lo posterior", () => {
    const e = estados(calcularDag({ fase: "plan", estado: "en-progreso", gates: { spec: gate("aprobado") } }));
    expect(e["fase-spec"]).toBe("hecho");
    expect(e["gate-spec"]).toBe("hecho");
    expect(e["fase-plan"]).toBe("actual");
    expect(e["gate-plan"]).toBe("pendiente");
    expect(e["fase-done"]).toBe("pendiente");
  });

  it("un gate escalado sin rehabilitar se pinta escalado; rehabilitado, hecho", () => {
    expect(estadoGate("plan", { fase: "plan", estado: "bloqueado", gates: { plan: gate("escalado") } })).toBe("escalado");
    expect(estadoGate("plan", { fase: "tasks", estado: "en-progreso", gates: { plan: gate("escalado", true) } })).toBe("hecho");
    expect(estadoGate("spec", { fase: "plan", estado: "en-progreso", gates: { spec: gate("refinado") } })).toBe("hecho");
  });

  it("una fase bloqueada se pinta escalada", () => {
    expect(estadoFase("implement", { fase: "implement", estado: "bloqueado", gates: {} })).toBe("escalado");
  });

  it("una fase completada sin resultado de gate deja el gate en curso", () => {
    const e = estados(calcularDag({ fase: "tasks", estado: "completado", gates: { spec: gate("aprobado"), plan: gate("aprobado") } }));
    expect(e["fase-tasks"]).toBe("hecho");
    expect(e["gate-tasks"]).toBe("actual");
  });

  it("los gates de fases ya superadas sin resultado se dan por hechos", () => {
    expect(estadoGate("spec", { fase: "implement", estado: "en-progreso", gates: {} })).toBe("hecho");
  });

  it("en done todo está hecho y el veredicto del gate se conserva", () => {
    const dag = calcularDag({
      fase: "done",
      estado: "completado",
      gates: { spec: gate("aprobado"), plan: gate("refinado"), tasks: gate("aprobado"), codigo: gate("aprobado") },
    });
    expect(dag.nodos.every((n) => n.estado === "hecho")).toBe(true);
    expect(dag.nodos.find((n) => n.id === "gate-plan")?.veredicto).toBe("refinado");
  });
});
