import { forwardRef, type SelectHTMLAttributes } from "react";
import { cn } from "../../lib/utiles";

export interface Opcion {
  valor: string;
  etiqueta: string;
  deshabilitada?: boolean;
}

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  opciones?: Opcion[];
  vacio?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, opciones, vacio, children, ...props },
  ref,
) {
  return (
    <select
      ref={ref}
      className={cn(
        "h-9 w-full rounded-md border border-borde bg-superficie px-2 text-sm focus-visible:outline-2 focus-visible:outline-primario disabled:opacity-50",
        className,
      )}
      {...props}
    >
      {vacio !== undefined ? <option value="">{vacio}</option> : null}
      {opciones?.map((o) => (
        <option key={o.valor} value={o.valor} disabled={o.deshabilitada}>
          {o.etiqueta}
        </option>
      ))}
      {children}
    </select>
  );
});

export const opcionesDe = (valores: readonly string[]): Opcion[] => valores.map((v) => ({ valor: v, etiqueta: v }));
