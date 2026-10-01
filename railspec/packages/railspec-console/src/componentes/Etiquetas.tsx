import type { EstadoFase, Fase, Modo, Riesgo, RiesgoImpacto, Severidad, Veredicto } from "../api/tipos";
import { Badge, type Tono } from "./ui/badge";

export const NOMBRE_FASE: Record<Fase, string> = {
  research: "Research",
  spec: "Spec",
  plan: "Plan",
  tasks: "Tareas",
  aprobacion: "Aprobación",
  implement: "Implementación",
  done: "Hecha",
};

const TONO_ESTADO: Record<EstadoFase, Tono> = { "en-progreso": "info", bloqueado: "peligro", completado: "exito" };
const TONO_RIESGO: Record<RiesgoImpacto, Tono> = { bajo: "exito", medio: "aviso", alto: "peligro", critico: "peligro" };
const TONO_SEVERIDAD: Record<Severidad, Tono> = { baja: "neutro", media: "aviso", alta: "peligro" };
const TONO_VEREDICTO: Record<Veredicto, Tono> = { aprobado: "exito", refinado: "info", escalado: "peligro" };

export function EtiquetaEstado({ estado }: { estado: EstadoFase }) {
  return <Badge tono={TONO_ESTADO[estado]}>{estado}</Badge>;
}

export function EtiquetaRiesgo({ riesgo }: { riesgo: Riesgo | RiesgoImpacto }) {
  return <Badge tono={TONO_RIESGO[riesgo]}>riesgo {riesgo}</Badge>;
}

export function EtiquetaModo({ modo }: { modo: Modo }) {
  return <Badge tono="violeta">{modo}</Badge>;
}

export function EtiquetaSeveridad({ severidad }: { severidad: Severidad }) {
  return <Badge tono={TONO_SEVERIDAD[severidad]}>{severidad}</Badge>;
}

export function EtiquetaVeredicto({ veredicto }: { veredicto: Veredicto }) {
  return <Badge tono={TONO_VEREDICTO[veredicto]}>{veredicto}</Badge>;
}
