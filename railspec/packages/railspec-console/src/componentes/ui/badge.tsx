import type { HTMLAttributes } from "react";
import { cn } from "../../lib/utiles";

export type Tono = "neutro" | "info" | "exito" | "aviso" | "peligro" | "violeta";

const TONOS: Record<Tono, string> = {
  neutro: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200",
  info: "bg-sky-100 text-sky-800 dark:bg-sky-900/50 dark:text-sky-200",
  exito: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-200",
  aviso: "bg-amber-100 text-amber-800 dark:bg-amber-900/50 dark:text-amber-200",
  peligro: "bg-rose-100 text-rose-800 dark:bg-rose-900/50 dark:text-rose-200",
  violeta: "bg-violet-100 text-violet-800 dark:bg-violet-900/50 dark:text-violet-200",
};

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tono?: Tono;
}

export function Badge({ className, tono = "neutro", ...props }: BadgeProps) {
  return (
    <span
      className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap", TONOS[tono], className)}
      {...props}
    />
  );
}
