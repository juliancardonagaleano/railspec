import { useCallback, useEffect, useRef, useState } from "react";
import { auth } from "../../api/endpoints";

/** Se renueva el token unos 5 minutos antes de que expire. */
export const MARGEN_RENOVACION_MS = 5 * 60 * 1000;
/** Mínimo entre renovaciones para no entrar en bucle con tokens de vida corta. */
export const MINIMO_RENOVACION_MS = 30 * 1000;

/**
 * Milisegundos hasta renovar un token que expira en `expiraEn` (ISO).
 * 0 si ya expiró o la fecha no es válida; si la vida restante es menor que el
 * margen, se renueva a mitad de camino (sin bajar de MINIMO_RENOVACION_MS).
 */
export function msHastaRenovar(expiraEn: string, ahora: number = Date.now(), margen: number = MARGEN_RENOVACION_MS): number {
  const expira = Date.parse(expiraEn);
  if (Number.isNaN(expira)) return 0;
  const restante = expira - ahora;
  if (restante <= 0) return 0;
  const conMargen = restante - margen;
  if (conMargen > 0) return conMargen;
  return Math.min(restante, Math.max(MINIMO_RENOVACION_MS, Math.floor(restante / 2)));
}

export type EstadoToken = { token: string | null; error: unknown; cargando: boolean; reintentar: () => void };

/** Token Bearer del chat: solo en memoria (nunca en localStorage), pedido al montar y renovado antes de expirar. */
export function useTokenChat(): EstadoToken {
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [cargando, setCargando] = useState(true);
  const temporizador = useRef<ReturnType<typeof setTimeout> | null>(null);
  const vivo = useRef(true);

  const pedirToken = useCallback(async () => {
    if (temporizador.current) clearTimeout(temporizador.current);
    setCargando(true);
    try {
      const t = await auth.token();
      if (!vivo.current) return;
      setToken(t.token);
      setError(null);
      temporizador.current = setTimeout(() => void pedirToken(), msHastaRenovar(t.expira_en));
    } catch (e) {
      if (vivo.current) setError(e);
    } finally {
      if (vivo.current) setCargando(false);
    }
  }, []);

  useEffect(() => {
    vivo.current = true;
    void pedirToken();
    return () => {
      vivo.current = false;
      if (temporizador.current) clearTimeout(temporizador.current);
    };
  }, [pedirToken]);

  return { token, error, cargando, reintentar: () => void pedirToken() };
}
