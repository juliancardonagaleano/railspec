import type { ReactNode } from "react";
import { ErrorApi, textoError } from "../api/cliente";
import { cn } from "../lib/utiles";
import { Button } from "./ui/button";

export function Cargando({ texto = "Cargando…", className }: { texto?: string; className?: string }) {
  return (
    <div role="status" aria-live="polite" className={cn("flex items-center gap-2 p-4 text-sm text-suave", className)}>
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-borde border-t-primario" aria-hidden="true" />
      {texto}
    </div>
  );
}

export function Vacio({ titulo, children, className }: { titulo: string; children?: ReactNode; className?: string }) {
  return (
    <div className={cn("rounded-lg border border-dashed border-borde p-6 text-center", className)}>
      <p className="font-medium">{titulo}</p>
      {children ? <div className="mt-1 text-sm text-suave">{children}</div> : null}
    </div>
  );
}

function tituloError(error: unknown): string {
  if (error instanceof ErrorApi) {
    if (error.status === 403) return "Sin permiso";
    if (error.status === 404) return "No encontrado";
    if (error.status === 409) return "Conflicto";
    if (error.status === 501) return "Aún no disponible";
  }
  return "Algo salió mal";
}

/** Error de una vista o acción; muestra el `detalle` del servidor (403 incluidos). */
export function ErrorVista({
  error,
  reintentar,
  className,
}: {
  error: unknown;
  reintentar?: () => void;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "rounded-lg border border-rose-300 bg-rose-50 p-4 text-sm text-rose-900 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-100",
        className,
      )}
    >
      <p className="font-semibold">{tituloError(error)}</p>
      <p className="mt-1">{textoError(error)}</p>
      {reintentar ? (
        <Button className="mt-3" variante="secundario" tamano="pequeno" onClick={reintentar}>
          Reintentar
        </Button>
      ) : null}
    </div>
  );
}

export function Aviso({ children, tono = "info", className }: { children: ReactNode; tono?: "info" | "aviso" | "exito"; className?: string }) {
  const estilos = {
    info: "border-sky-300 bg-sky-50 text-sky-900 dark:border-sky-800 dark:bg-sky-950/40 dark:text-sky-100",
    aviso: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100",
    exito: "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-100",
  }[tono];
  return (
    <div role="status" className={cn("rounded-lg border p-3 text-sm", estilos, className)}>
      {children}
    </div>
  );
}

export function Encabezado({ titulo, descripcion, acciones }: { titulo: string; descripcion?: ReactNode; acciones?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold">{titulo}</h1>
        {descripcion ? <p className="mt-1 text-sm text-suave">{descripcion}</p> : null}
      </div>
      {acciones ? <div className="flex flex-wrap gap-2">{acciones}</div> : null}
    </div>
  );
}
