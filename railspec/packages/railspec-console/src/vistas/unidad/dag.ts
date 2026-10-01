import { FASES, type EstadoUnidad, type Fase, type GateFase, type ResultadoGate } from "../../api/tipos";

export type EstadoNodo = "hecho" | "actual" | "pendiente" | "escalado";

export interface NodoDag {
  id: string;
  clase: "fase" | "gate";
  /** Fase o gate al que corresponde. */
  clave: Fase | GateFase;
  etiqueta: string;
  estado: EstadoNodo;
  /** Para gates: veredicto y si fue rehabilitado. */
  veredicto?: ResultadoGate["veredicto"];
  rehabilitado?: boolean;
}

export interface AristaDag {
  id: string;
  origen: string;
  destino: string;
  activa: boolean;
}

/** Recorrido fijo del flujo SDD: cada gate va tras la fase que valida. */
export const SECUENCIA: readonly { clase: "fase" | "gate"; clave: Fase | GateFase }[] = [
  { clase: "fase", clave: "research" },
  { clase: "fase", clave: "spec" },
  { clase: "gate", clave: "spec" },
  { clase: "fase", clave: "plan" },
  { clase: "gate", clave: "plan" },
  { clase: "fase", clave: "tasks" },
  { clase: "gate", clave: "tasks" },
  { clase: "fase", clave: "aprobacion" },
  { clase: "fase", clave: "implement" },
  { clase: "gate", clave: "codigo" },
  { clase: "fase", clave: "done" },
];

/** Fase tras la que corre cada gate. */
export const FASE_DEL_GATE: Record<GateFase, Fase> = {
  spec: "spec",
  plan: "plan",
  tasks: "tasks",
  codigo: "implement",
};

const ETIQUETA_FASE: Record<Fase, string> = {
  research: "Research",
  spec: "Spec",
  plan: "Plan",
  tasks: "Tareas",
  aprobacion: "Aprobación",
  implement: "Implementación",
  done: "Hecha",
};

const indiceFase = (f: Fase) => FASES.indexOf(f);

type EstadoParaDag = Pick<EstadoUnidad, "fase" | "estado" | "gates">;

/** Estado visual de un gate según su resultado y la fase actual de la unidad. */
export function estadoGate(gate: GateFase, unidad: EstadoParaDag): EstadoNodo {
  const resultado = unidad.gates?.[gate];
  const faseGate = indiceFase(FASE_DEL_GATE[gate]);
  const actual = indiceFase(unidad.fase);
  if (resultado) {
    if (resultado.veredicto === "escalado" && !resultado.rehabilitado) return "escalado";
    return "hecho";
  }
  if (actual > faseGate) return "hecho";
  // Fase del gate completada pero sin resultado: el gate está corriendo.
  if (actual === faseGate && unidad.estado === "completado") return "actual";
  return "pendiente";
}

/** Estado visual de una fase. */
export function estadoFase(fase: Fase, unidad: EstadoParaDag): EstadoNodo {
  const i = indiceFase(fase);
  const actual = indiceFase(unidad.fase);
  if (i < actual) return "hecho";
  if (i > actual) return "pendiente";
  if (fase === "done") return "hecho";
  if (unidad.estado === "bloqueado") return "escalado";
  if (unidad.estado === "completado") return "hecho";
  return "actual";
}

/** Calcula nodos y aristas del DAG de fases y gates de una unidad (función pura). */
export function calcularDag(unidad: EstadoParaDag): { nodos: NodoDag[]; aristas: AristaDag[] } {
  const nodos: NodoDag[] = SECUENCIA.map(({ clase, clave }) => {
    if (clase === "gate") {
      const gate = clave as GateFase;
      const r = unidad.gates?.[gate];
      const nodo: NodoDag = {
        id: `gate-${gate}`,
        clase,
        clave: gate,
        etiqueta: `Gate ${gate === "codigo" ? "código" : gate}`,
        estado: estadoGate(gate, unidad),
      };
      if (r) {
        nodo.veredicto = r.veredicto;
        nodo.rehabilitado = Boolean(r.rehabilitado);
      }
      return nodo;
    }
    const fase = clave as Fase;
    return { id: `fase-${fase}`, clase, clave: fase, etiqueta: ETIQUETA_FASE[fase], estado: estadoFase(fase, unidad) };
  });
  const aristas: AristaDag[] = [];
  for (let i = 1; i < nodos.length; i++) {
    const a = nodos[i - 1]!;
    const b = nodos[i]!;
    aristas.push({ id: `${a.id}->${b.id}`, origen: a.id, destino: b.id, activa: a.estado === "hecho" && b.estado !== "pendiente" });
  }
  return { nodos, aristas };
}

export const COLOR_ESTADO: Record<EstadoNodo, { fondo: string; borde: string; texto: string }> = {
  hecho: { fondo: "#d1fae5", borde: "#059669", texto: "#064e3b" },
  actual: { fondo: "#dbeafe", borde: "#2563eb", texto: "#1e3a8a" },
  pendiente: { fondo: "#f1f5f9", borde: "#94a3b8", texto: "#475569" },
  escalado: { fondo: "#ffe4e6", borde: "#e11d48", texto: "#881337" },
};

export const ETIQUETA_ESTADO_NODO: Record<EstadoNodo, string> = {
  hecho: "hecho",
  actual: "en curso",
  pendiente: "pendiente",
  escalado: "escalado / bloqueado",
};
