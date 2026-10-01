import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "../../lib/utiles";

type Variante = "primario" | "secundario" | "fantasma" | "peligro" | "enlace";
type Tamano = "normal" | "pequeno" | "icono";

const VARIANTES: Record<Variante, string> = {
  primario: "bg-primario text-primario-texto hover:opacity-90",
  secundario: "border border-borde bg-superficie hover:bg-fondo",
  fantasma: "hover:bg-fondo",
  peligro: "bg-peligro text-white hover:opacity-90",
  enlace: "text-primario underline-offset-4 hover:underline px-0",
};

const TAMANOS: Record<Tamano, string> = {
  normal: "h-9 px-4 text-sm",
  pequeno: "h-8 px-3 text-xs",
  icono: "h-8 w-8 text-sm",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variante?: Variante;
  tamano?: Tamano;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variante = "primario", tamano = "normal", type = "button", ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primario",
        "disabled:pointer-events-none disabled:opacity-50",
        VARIANTES[variante],
        TAMANOS[tamano],
        className,
      )}
      {...props}
    />
  );
});
