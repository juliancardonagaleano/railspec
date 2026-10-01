import { createContext, useContext, useId, type ReactNode } from "react";
import { cn } from "../../lib/utiles";

interface ContextoTabs {
  valor: string;
  cambiar: (v: string) => void;
  base: string;
}

const Contexto = createContext<ContextoTabs | null>(null);

function useTabs(): ContextoTabs {
  const ctx = useContext(Contexto);
  if (!ctx) throw new Error("Tabs: componente fuera de <Tabs>");
  return ctx;
}

export function Tabs({
  valor,
  alCambiar,
  children,
  className,
}: {
  valor: string;
  alCambiar: (v: string) => void;
  children: ReactNode;
  className?: string;
}) {
  const base = useId();
  return (
    <Contexto.Provider value={{ valor, cambiar: alCambiar, base }}>
      <div className={className}>{children}</div>
    </Contexto.Provider>
  );
}

export function TabsList({ children, className, etiqueta }: { children: ReactNode; className?: string; etiqueta: string }) {
  return (
    <div role="tablist" aria-label={etiqueta} className={cn("flex flex-wrap gap-1 border-b border-borde", className)}>
      {children}
    </div>
  );
}

export function TabsTrigger({ valor, children }: { valor: string; children: ReactNode }) {
  const ctx = useTabs();
  const activo = ctx.valor === valor;
  return (
    <button
      type="button"
      role="tab"
      id={`${ctx.base}-tab-${valor}`}
      aria-selected={activo}
      aria-controls={`${ctx.base}-panel-${valor}`}
      tabIndex={activo ? 0 : -1}
      onClick={() => ctx.cambiar(valor)}
      className={cn(
        "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors",
        activo ? "border-primario text-primario" : "border-transparent text-suave hover:text-texto",
      )}
    >
      {children}
    </button>
  );
}

export function TabsContent({ valor, children, className }: { valor: string; children: ReactNode; className?: string }) {
  const ctx = useTabs();
  if (ctx.valor !== valor) return null;
  return (
    <div
      role="tabpanel"
      id={`${ctx.base}-panel-${valor}`}
      aria-labelledby={`${ctx.base}-tab-${valor}`}
      className={cn("pt-4", className)}
    >
      {children}
    </div>
  );
}
