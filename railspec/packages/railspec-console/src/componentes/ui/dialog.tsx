import { useEffect, useId, useRef, type ReactNode } from "react";
import { cn } from "../../lib/utiles";
import { Button } from "./button";

export interface DialogProps {
  abierto: boolean;
  alCerrar: () => void;
  titulo: string;
  descripcion?: ReactNode;
  children: ReactNode;
  pie?: ReactNode;
  className?: string;
}

/** Diálogo modal sencillo: capa, Escape cierra, foco inicial en el panel. */
export function Dialog({ abierto, alCerrar, titulo, descripcion, children, pie, className }: DialogProps) {
  const idTitulo = useId();
  const idDesc = useId();
  const panel = useRef<HTMLDivElement>(null);
  const cerrar = useRef(alCerrar);
  cerrar.current = alCerrar;

  useEffect(() => {
    if (!abierto) return;
    const previo = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    const alTeclear = (e: KeyboardEvent) => {
      if (e.key === "Escape") cerrar.current();
    };
    document.addEventListener("keydown", alTeclear);
    return () => {
      document.removeEventListener("keydown", alTeclear);
      previo?.focus?.();
    };
  }, [abierto]);

  if (!abierto) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/40" aria-hidden="true" onClick={alCerrar} />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={idTitulo}
        aria-describedby={descripcion ? idDesc : undefined}
        tabIndex={-1}
        className={cn(
          "relative z-10 flex max-h-[90vh] w-full max-w-lg flex-col rounded-lg border border-borde bg-superficie shadow-xl outline-none",
          className,
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-borde p-4">
          <div>
            <h2 id={idTitulo} className="text-lg font-semibold">
              {titulo}
            </h2>
            {descripcion ? (
              <div id={idDesc} className="mt-1 text-sm text-suave">
                {descripcion}
              </div>
            ) : null}
          </div>
          <Button variante="fantasma" tamano="icono" aria-label="Cerrar" onClick={alCerrar}>
            ✕
          </Button>
        </div>
        <div className="overflow-y-auto p-4">{children}</div>
        {pie ? <div className="flex justify-end gap-2 border-t border-borde p-4">{pie}</div> : null}
      </div>
    </div>
  );
}
