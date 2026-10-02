import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...entradas: ClassValue[]): string {
  return twMerge(clsx(entradas));
}

const formatoFecha = new Intl.DateTimeFormat("es", { dateStyle: "medium", timeStyle: "short" });
const formatoNumero = new Intl.NumberFormat("es");
const formatoUsd = new Intl.NumberFormat("es", { style: "currency", currency: "USD", maximumFractionDigits: 2 });

export function fecha(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : formatoFecha.format(d);
}

export function numero(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : formatoNumero.format(n);
}

export function usd(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : formatoUsd.format(n);
}

export function commitCorto(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 7) : "sin grafo";
}

/** Nombre legible de un actor del contrato. */
export function nombreActor(actor: { login?: string | null; agente?: string | null; tipo: string } | null | undefined): string {
  if (!actor) return "—";
  return actor.login ?? actor.agente ?? actor.tipo;
}

/** Convierte un `<input type="date">` (AAAA-MM-DD) a ISO; `fin` = final del día. */
export function fechaAIso(valor: string, fin = false): string | undefined {
  if (!valor) return undefined;
  return new Date(`${valor}T${fin ? "23:59:59.999" : "00:00:00.000"}Z`).toISOString();
}

/** Duración legible a partir de milisegundos: «850 ms», «12,4 s», «3 min 05 s». */
export function duracion(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1).replace(".", ",")} s`;
  const min = Math.floor(s / 60);
  return `${min} min ${String(Math.round(s - min * 60)).padStart(2, "0")} s`;
}

export function porcentaje(n: number | null | undefined): string {
  return n === null || n === undefined ? "—" : `${n.toFixed(0)} %`;
}
