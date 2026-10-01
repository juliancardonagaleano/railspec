import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { claves, unidades } from "../../api/endpoints";

export type EstadoConexion = "conectando" | "en-vivo" | "desconectado";

/**
 * Se suscribe al SSE de la unidad (la cookie viaja sola con EventSource) y, ante
 * cada `sync` o `estado`, invalida las queries de la unidad para refrescarlas.
 */
export function useEventosUnidad(org: string, ws: string, unidad: string): EstadoConexion {
  const clienteQuery = useQueryClient();
  const [conexion, setConexion] = useState<EstadoConexion>("conectando");

  useEffect(() => {
    if (typeof EventSource === "undefined") {
      setConexion("desconectado");
      return;
    }
    const fuente = new EventSource(unidades.urlEventos(org, ws, unidad), { withCredentials: true });
    let pendiente: ReturnType<typeof setTimeout> | null = null;
    const invalidar = () => {
      // Agrupa ráfagas de eventos en una sola recarga.
      if (pendiente) return;
      pendiente = setTimeout(() => {
        pendiente = null;
        void clienteQuery.invalidateQueries({ queryKey: claves.unidad(org, ws, unidad) });
        void clienteQuery.invalidateQueries({ queryKey: claves.tablero(org, ws) });
      }, 250);
    };
    fuente.onopen = () => setConexion("en-vivo");
    fuente.onerror = () => setConexion(fuente.readyState === EventSource.CLOSED ? "desconectado" : "conectando");
    fuente.addEventListener("sync", invalidar);
    fuente.addEventListener("estado", invalidar);
    return () => {
      if (pendiente) clearTimeout(pendiente);
      fuente.close();
    };
  }, [clienteQuery, org, ws, unidad]);

  return conexion;
}
