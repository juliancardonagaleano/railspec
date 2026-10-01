// Parser incremental de Server-Sent Events (https://html.spec.whatwg.org/#event-stream-interpretation).
// Se usa con fetch + ReadableStream porque el endpoint de preguntas es POST
// (EventSource sólo admite GET).

export interface EventoSse {
  /** Nombre del evento (`event:`); "message" si no se indicó. */
  evento: string;
  /** Líneas `data:` unidas con "\n". */
  datos: string;
  /** Último `id:` visto, si lo hubo. */
  id?: string;
}

export class LectorSse {
  private resto = "";
  private evento = "";
  private datos: string[] = [];
  private id: string | undefined;
  private ultimoId: string | undefined;
  private crPendiente = false;
  private primerTrozo = true;

  /** Alimenta un trozo de texto arbitrario y devuelve los eventos completos que cierra. */
  alimentar(trozo: string): EventoSse[] {
    if (this.primerTrozo && trozo.length > 0) {
      this.primerTrozo = false;
      if (trozo.charCodeAt(0) === 0xfeff) trozo = trozo.slice(1);
    }
    // Un "\r" al final del trozo anterior ya cerró la línea; si este empieza
    // con "\n" forma parte del mismo CRLF y se descarta.
    if (this.crPendiente && trozo.startsWith("\n")) trozo = trozo.slice(1);
    this.crPendiente = false;

    const eventos: EventoSse[] = [];
    let texto = this.resto + trozo;
    let inicio = 0;
    for (let i = 0; i < texto.length; i++) {
      const c = texto[i];
      if (c !== "\n" && c !== "\r") continue;
      const linea = texto.slice(inicio, i);
      if (c === "\r") {
        if (i + 1 < texto.length) {
          if (texto[i + 1] === "\n") i++;
        } else {
          this.crPendiente = true;
        }
      }
      inicio = i + 1;
      const ev = this.procesarLinea(linea);
      if (ev) eventos.push(ev);
    }
    texto = texto.slice(inicio);
    this.resto = texto;
    return eventos;
  }

  /** Cierra el flujo: per especificación, un evento sin línea en blanco final se descarta. */
  terminar(): EventoSse[] {
    this.resto = "";
    this.reiniciarEvento();
    return [];
  }

  private procesarLinea(linea: string): EventoSse | null {
    if (linea === "") return this.despachar();
    if (linea.startsWith(":")) return null; // comentario
    const dosPuntos = linea.indexOf(":");
    let campo: string;
    let valor: string;
    if (dosPuntos === -1) {
      campo = linea;
      valor = "";
    } else {
      campo = linea.slice(0, dosPuntos);
      valor = linea.slice(dosPuntos + 1);
      if (valor.startsWith(" ")) valor = valor.slice(1);
    }
    switch (campo) {
      case "event":
        this.evento = valor;
        break;
      case "data":
        this.datos.push(valor);
        break;
      case "id":
        if (!valor.includes("\0")) this.id = valor;
        break;
      default:
        // "retry" y campos desconocidos se ignoran.
        break;
    }
    return null;
  }

  private despachar(): EventoSse | null {
    if (this.id !== undefined) this.ultimoId = this.id;
    if (this.datos.length === 0) {
      this.reiniciarEvento();
      return null;
    }
    const ev: EventoSse = {
      evento: this.evento || "message",
      datos: this.datos.join("\n"),
    };
    if (this.ultimoId !== undefined) ev.id = this.ultimoId;
    this.reiniciarEvento();
    return ev;
  }

  private reiniciarEvento(): void {
    this.evento = "";
    this.datos = [];
    this.id = undefined;
  }
}

/** Lee el cuerpo de una respuesta `text/event-stream` y emite eventos SSE a medida que llegan. */
export async function* leerEventos(respuesta: Response): AsyncGenerator<EventoSse> {
  if (!respuesta.body) return;
  const lector = respuesta.body.getReader();
  const decodificador = new TextDecoder("utf-8");
  const sse = new LectorSse();
  try {
    for (;;) {
      const { done, value } = await lector.read();
      if (done) break;
      const texto = decodificador.decode(value, { stream: true });
      for (const ev of sse.alimentar(texto)) yield ev;
    }
    const cola = decodificador.decode();
    if (cola) for (const ev of sse.alimentar(cola)) yield ev;
    sse.terminar();
  } finally {
    try {
      await lector.cancel();
    } catch {
      // ya cerrado
    }
    lector.releaseLock();
  }
}
