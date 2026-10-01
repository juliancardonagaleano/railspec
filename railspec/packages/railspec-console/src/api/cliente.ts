import type { CodigoError, CuerpoError } from "./tipos";

/** Prefijo de la API de la consola (mismo origen que la SPA). */
export const BASE_API = "/consola/api";
/** Prefijo de la SPA (debe coincidir con `base` de Vite y el basepath del router). */
export const BASE_SPA = "/consola";

/** Error de la API: lleva el status HTTP, el detalle y, si es un ErrorTool, su código. */
export class ErrorApi extends Error {
  readonly status: number;
  readonly detalle: string;
  readonly codigo: CodigoError | undefined;
  readonly errores: { ruta: string; mensaje: string }[];
  readonly versionActual: number | undefined;

  constructor(status: number, cuerpo: CuerpoError | null) {
    const detalle = cuerpo?.detalle ?? mensajePorStatus(status);
    super(detalle);
    this.name = "ErrorApi";
    this.status = status;
    this.detalle = detalle;
    this.codigo = cuerpo?.codigo;
    this.errores = cuerpo?.errores ?? [];
    this.versionActual = cuerpo?.version_actual ?? cuerpo?.version_estado ?? undefined;
  }

  get esConflicto(): boolean {
    return this.status === 409;
  }
}

export function mensajePorStatus(status: number): string {
  switch (status) {
    case 400:
      return "La petición no es válida.";
    case 401:
      return "La sesión expiró; vuelve a entrar.";
    case 403:
      return "No tienes permiso para esta acción.";
    case 404:
      return "No se encontró el recurso.";
    case 409:
      return "Conflicto: alguien más cambió este dato.";
    case 422:
      return "Los datos enviados no son válidos.";
    case 501:
      return "El servidor aún no implementa esta operación.";
    default:
      return status >= 500 ? "Error del servidor." : `Error inesperado (${status}).`;
  }
}

/** Ruta de la SPA (sin `/consola`) a la que volver tras iniciar sesión. */
export function rutaActualSpa(): string {
  const { pathname, search } = window.location;
  const ruta = pathname.startsWith(BASE_SPA) ? pathname.slice(BASE_SPA.length) : pathname;
  return (ruta || "/") + search;
}

export function urlLogin(volver: string): string {
  return `${BASE_SPA}/login?volver=${encodeURIComponent(volver)}`;
}

let redirigiendo = false;

/** Lleva al login conservando la ruta actual. Se puede sustituir en pruebas. */
export let redirigirALogin = (): void => {
  if (redirigiendo) return;
  if (window.location.pathname.startsWith(`${BASE_SPA}/login`)) return;
  redirigiendo = true;
  window.location.assign(urlLogin(rutaActualSpa()));
};

export function fijarRedireccionLogin(fn: () => void): void {
  redirigirALogin = fn;
  redirigiendo = false;
}

export type Consulta = Record<string, string | number | boolean | null | undefined>;

export interface OpcionesPedir {
  metodo?: "GET" | "POST" | "PUT" | "DELETE";
  cuerpo?: unknown;
  consulta?: Consulta;
  /** No redirigir al login ante un 401 (p. ej. en la propia pantalla de login). */
  sinRedireccion?: boolean;
  senal?: AbortSignal;
}

export function construirUrl(ruta: string, consulta?: Consulta): string {
  const url = `${BASE_API}${ruta}`;
  if (!consulta) return url;
  const params = new URLSearchParams();
  for (const [clave, valor] of Object.entries(consulta)) {
    if (valor === undefined || valor === null || valor === "") continue;
    params.set(clave, String(valor));
  }
  const texto = params.toString();
  return texto ? `${url}?${texto}` : url;
}

async function leerCuerpo(respuesta: Response): Promise<unknown> {
  if (respuesta.status === 204) return null;
  const texto = await respuesta.text();
  if (!texto) return null;
  try {
    return JSON.parse(texto) as unknown;
  } catch {
    return { detalle: texto.slice(0, 500) };
  }
}

/**
 * Petición a la API de la consola: cookie de sesión (same-origin), JSON y
 * cabecera anti-CSRF `X-Railspec-Consola: 1` en todo lo que no sea GET.
 */
export async function pedir<T>(ruta: string, opciones: OpcionesPedir = {}): Promise<T> {
  const metodo = opciones.metodo ?? "GET";
  const cabeceras: Record<string, string> = { Accept: "application/json" };
  if (metodo !== "GET") cabeceras["X-Railspec-Consola"] = "1";
  let cuerpo: string | undefined;
  if (opciones.cuerpo !== undefined) {
    cabeceras["Content-Type"] = "application/json";
    cuerpo = JSON.stringify(opciones.cuerpo);
  }
  const init: RequestInit = { method: metodo, credentials: "same-origin", headers: cabeceras };
  if (cuerpo !== undefined) init.body = cuerpo;
  if (opciones.senal) init.signal = opciones.senal;

  const respuesta = await fetch(construirUrl(ruta, opciones.consulta), init);
  const datos = await leerCuerpo(respuesta);
  if (!respuesta.ok) {
    if (respuesta.status === 401 && !opciones.sinRedireccion) redirigirALogin();
    const cuerpoError = datos && typeof datos === "object" ? (datos as CuerpoError) : null;
    throw new ErrorApi(respuesta.status, cuerpoError);
  }
  return datos as T;
}

/** Invoca una tool del registro por la superficie HTTP (canal consola). */
export function invocarTool<S, E = unknown>(nombre: string, entrada: E, senal?: AbortSignal): Promise<S> {
  const opciones: OpcionesPedir = { metodo: "POST", cuerpo: entrada };
  if (senal) opciones.senal = senal;
  return pedir<S>(`/tools/${encodeURIComponent(nombre)}`, opciones);
}

/**
 * ¿El servidor aún no habla el contrato 1.4? (verbos `impact`/`trace` de graph.query).
 * Un servidor anterior rechaza el verbo desconocido con 422 y un único error en la
 * ruta `consulta` (la unión discriminada por `verbo`). No vale cualquier 422 ni un 404:
 * un servidor 1.4 responde 422 con rutas más profundas (`consulta.trace.criterio`) y 404
 * `no-encontrado` si no tiene grafo (la tool `graph.query` no se registra); ninguno de
 * los dos es falta de contrato y deben verse como error.
 */
export function esSinContrato14(error: unknown): boolean {
  return error instanceof ErrorApi && error.status === 422 && error.errores.some((e) => e.ruta === "consulta");
}

/** Texto legible de cualquier error para mostrarlo en la interfaz. */
export function textoError(error: unknown): string {
  if (error instanceof ErrorApi) {
    const extra = error.errores.length
      ? ` (${error.errores.map((e) => `${e.ruta}: ${e.mensaje}`).join("; ")})`
      : "";
    return `${error.detalle}${extra}`;
  }
  if (error instanceof Error) return error.message;
  return "Error desconocido.";
}
