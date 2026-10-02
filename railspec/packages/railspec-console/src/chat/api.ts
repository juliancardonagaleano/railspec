// Cliente HTTP del chat de contexto (railspec-server /v1/chat/...).

import { leerEventos } from "./sse";
import type {
  Alcance,
  Conversacion,
  DatosErrorStream,
  DatosProgreso,
  EventoChat,
  Insumo,
  MensajeChat,
  PeticionExportarInsumo,
} from "./tipos";

export interface OpcionesClienteChat {
  /** Base del API, p. ej. "https://railspec.example.com" (sin "/" final). */
  apiBase: string;
  obtenerToken: () => string | Promise<string>;
  fetch?: typeof fetch;
}

export class ErrorChat extends Error {
  readonly estado: number;
  readonly codigo: string | undefined;
  readonly detalle: string;
  /** Sólo en 422 de exportación de insumo. */
  readonly reglasFallidas: string[];

  constructor(estado: number, detalle: string, codigo?: string, reglasFallidas: string[] = []) {
    super(codigo ? `${estado} ${codigo}: ${detalle}` : `${estado}: ${detalle}`);
    this.name = "ErrorChat";
    this.estado = estado;
    this.codigo = codigo;
    this.detalle = detalle;
    this.reglasFallidas = reglasFallidas;
  }
}

export interface ClienteChat {
  /** Las conversaciones vigentes de la persona en el workspace, recientes primero (tope 50). Nunca las de otras personas. */
  listarConversaciones(alcance: Alcance): Promise<Conversacion[]>;
  crearConversacion(alcance: Alcance, repositorios: string[]): Promise<Conversacion>;
  obtenerConversacion(id: string): Promise<{ conversacion: Conversacion; mensajes: MensajeChat[] }>;
  preguntar(id: string, pregunta: string, senal?: AbortSignal): AsyncGenerator<EventoChat>;
  marcarConservar(id: string, mensajeId: string, conservar: boolean): Promise<MensajeChat>;
  exportarInsumo(id: string, peticion: PeticionExportarInsumo): Promise<Insumo>;
}

async function leerError(respuesta: Response): Promise<ErrorChat> {
  let detalle = respuesta.statusText || `HTTP ${respuesta.status}`;
  let codigo: string | undefined;
  let reglas: string[] = [];
  try {
    const cuerpo: unknown = await respuesta.json();
    if (cuerpo && typeof cuerpo === "object") {
      const c = cuerpo as Record<string, unknown>;
      if (typeof c.detalle === "string") detalle = c.detalle;
      if (typeof c.codigo === "string") codigo = c.codigo;
      if (Array.isArray(c.reglas_fallidas)) reglas = c.reglas_fallidas.map(String);
    }
  } catch {
    // cuerpo no JSON
  }
  return new ErrorChat(respuesta.status, detalle, codigo, reglas);
}

export function crearClienteChat(opciones: OpcionesClienteChat): ClienteChat {
  const base = opciones.apiBase.replace(/\/+$/, "");
  const hacerFetch: typeof fetch = opciones.fetch ?? ((...a) => globalThis.fetch(...a));

  async function pedir(
    metodo: string,
    ruta: string,
    cuerpo?: unknown,
    extra: { aceptar?: string; senal?: AbortSignal } = {},
  ): Promise<Response> {
    const token = await opciones.obtenerToken();
    const cabeceras: Record<string, string> = {
      Authorization: `Bearer ${token}`,
      Accept: extra.aceptar ?? "application/json",
    };
    if (cuerpo !== undefined) cabeceras["Content-Type"] = "application/json";
    let respuesta: Response;
    try {
      respuesta = await hacerFetch(`${base}${ruta}`, {
        method: metodo,
        headers: cabeceras,
        body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
        signal: extra.senal,
      });
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") throw e;
      throw new ErrorChat(0, `No se pudo contactar al servidor: ${String(e)}`, "red");
    }
    if (!respuesta.ok) throw await leerError(respuesta);
    return respuesta;
  }

  const ruta = (id: string) => `/v1/chat/conversaciones/${encodeURIComponent(id)}`;

  return {
    async listarConversaciones(alcance) {
      const consulta = new URLSearchParams({ org: alcance.org, workspace: alcance.workspace });
      const r = await pedir("GET", `/v1/chat/conversaciones?${consulta}`);
      const j = (await r.json()) as { conversaciones: Conversacion[] };
      return j.conversaciones;
    },

    async crearConversacion(alcance, repositorios) {
      const r = await pedir("POST", "/v1/chat/conversaciones", { alcance, repositorios });
      const j = (await r.json()) as { conversacion: Conversacion };
      return j.conversacion;
    },

    async obtenerConversacion(id) {
      const r = await pedir("GET", ruta(id));
      return (await r.json()) as { conversacion: Conversacion; mensajes: MensajeChat[] };
    },

    async *preguntar(id, pregunta, senal) {
      const r = await pedir("POST", `${ruta(id)}/mensajes`, { pregunta }, {
        aceptar: "text/event-stream",
        senal,
      });
      for await (const ev of leerEventos(r)) {
        let datos: unknown = {};
        if (ev.datos.trim() !== "") {
          try {
            datos = JSON.parse(ev.datos);
          } catch {
            throw new ErrorChat(r.status, `Evento SSE con JSON inválido (${ev.evento})`, "sse-invalido");
          }
        }
        switch (ev.evento) {
          case "pregunta":
            yield { tipo: "pregunta", mensaje: datos as MensajeChat };
            break;
          case "progreso":
            yield { tipo: "progreso", progreso: datos as DatosProgreso };
            break;
          case "respuesta":
            yield { tipo: "respuesta", mensaje: datos as MensajeChat };
            break;
          case "error":
            yield { tipo: "error", error: datos as DatosErrorStream };
            break;
          case "fin":
            yield { tipo: "fin" };
            return;
          default:
            // eventos desconocidos se ignoran (compatibilidad hacia adelante)
            break;
        }
      }
    },

    async marcarConservar(id, mensajeId, conservar) {
      const r = await pedir("PATCH", `${ruta(id)}/mensajes/${encodeURIComponent(mensajeId)}`, {
        conservar_en_insumo: conservar,
      });
      const j = (await r.json()) as { mensaje: MensajeChat };
      return j.mensaje;
    },

    async exportarInsumo(id, peticion) {
      const r = await pedir("POST", `${ruta(id)}/insumo`, peticion);
      const j = (await r.json()) as { insumo: Insumo };
      return j.insumo;
    },
  };
}
