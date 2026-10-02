import { useId } from "react";
import type { Conversacion } from "../../chat/tipos";
import { Select, type Opcion } from "../../componentes/ui/select";
import { fecha } from "../../lib/utiles";

/** «2 oct 2026, 10:00 · api, web»: cuándo se creó y qué repositorios consulta. */
export const etiquetaConversacion = (c: Conversacion) => `${fecha(c.creada_en)} · ${c.repositorios.join(", ")}`;

/**
 * Las conversaciones de la persona en el workspace, para retomar cualquiera y no solo la última.
 * Sin elegir ninguna (`actual` indefinida: se están eligiendo repositorios) queda en «Nueva conversación»;
 * volver a esa opción empieza otra.
 */
export function SelectorConversaciones({
  conversaciones,
  actual,
  alElegir,
  alNueva,
}: {
  conversaciones: Conversacion[];
  actual: string | undefined;
  alElegir: (id: string) => void;
  alNueva: () => void;
}) {
  const id = useId();
  const opciones: Opcion[] = conversaciones.map((c) => ({ valor: c.id, etiqueta: etiquetaConversacion(c) }));
  // La recién creada puede no estar en la lista aún (se recarga al crearla): no dejar el selector en blanco.
  if (actual && !conversaciones.some((c) => c.id === actual)) opciones.unshift({ valor: actual, etiqueta: "Conversación actual" });
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <label htmlFor={id} className="text-sm font-medium">
        Conversación
      </label>
      <Select
        id={id}
        className="max-w-md"
        value={actual ?? ""}
        onChange={(e) => (e.target.value ? alElegir(e.target.value) : alNueva())}
        vacio="Nueva conversación"
        opciones={opciones}
      />
    </div>
  );
}
