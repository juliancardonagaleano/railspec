import { describe, expect, it } from "vitest";
import { LectorSse, leerEventos } from "./sse";
import type { EventoSse } from "./sse";

function alimentarTodo(trozos: string[]): EventoSse[] {
  const lector = new LectorSse();
  return trozos.flatMap((t) => lector.alimentar(t));
}

const FLUJO =
  'event: pregunta\ndata: {"id":"m1"}\n\n' +
  'event: progreso\ndata: {"paso":1,"tool":"graph.query","texto":"Consultando graph.query"}\n\n' +
  "event: fin\ndata: {}\n\n";

describe("LectorSse", () => {
  it("parsea eventos completos en un solo trozo", () => {
    const evs = alimentarTodo([FLUJO]);
    expect(evs.map((e) => e.evento)).toEqual(["pregunta", "progreso", "fin"]);
    expect(JSON.parse(evs[1].datos)).toEqual({ paso: 1, tool: "graph.query", texto: "Consultando graph.query" });
  });

  it("tolera cortes en cualquier posición", () => {
    const esperado = alimentarTodo([FLUJO]);
    for (let i = 1; i < FLUJO.length; i++) {
      expect(alimentarTodo([FLUJO.slice(0, i), FLUJO.slice(i)])).toEqual(esperado);
    }
    expect(alimentarTodo(FLUJO.split(""))).toEqual(esperado);
  });

  it("acepta CRLF y CR, incluso partidos entre trozos", () => {
    const crlf = FLUJO.replace(/\n/g, "\r\n");
    const esperado = alimentarTodo([FLUJO]);
    expect(alimentarTodo([crlf])).toEqual(esperado);
    expect(alimentarTodo(crlf.split(""))).toEqual(esperado);
    expect(alimentarTodo([FLUJO.replace(/\n/g, "\r")])).toEqual(esperado);
  });

  it("une varias líneas data con salto de línea", () => {
    const evs = alimentarTodo(["data: uno\ndata:dos\ndata\n\n"]);
    expect(evs).toEqual([{ evento: "message", datos: "uno\ndos\n" }]);
  });

  it("ignora comentarios, campos desconocidos y bloques sin data", () => {
    const evs = alimentarTodo([": ping\n\nretry: 100\nfoo: bar\n\nevent: x\n\nevent: fin\n: nota\ndata: {}\n\n"]);
    expect(evs).toEqual([{ evento: "fin", datos: "{}" }]);
  });

  it("conserva sólo un espacio inicial del valor y quita BOM", () => {
    const evs = alimentarTodo(["﻿data:  dos espacios\n\n"]);
    expect(evs[0].datos).toBe(" dos espacios");
  });

  it("registra el último id", () => {
    const evs = alimentarTodo(["id: 7\ndata: a\n\ndata: b\n\n"]);
    expect(evs.map((e) => e.id)).toEqual(["7", "7"]);
  });

  it("no emite un evento sin línea en blanco final", () => {
    const lector = new LectorSse();
    expect(lector.alimentar("event: fin\ndata: {}\n")).toEqual([]);
    expect(lector.terminar()).toEqual([]);
  });
});

describe("leerEventos", () => {
  it("lee un Response en streaming con UTF-8 partido entre trozos", async () => {
    const bytes = new TextEncoder().encode('event: respuesta\ndata: {"texto":"señal ñ"}\n\nevent: fin\ndata: {}\n\n');
    const corte = bytes.indexOf(0xc3) + 1; // parte la "ñ" a la mitad
    const cuerpo = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(bytes.slice(0, corte));
        c.enqueue(bytes.slice(corte));
        c.close();
      },
    });
    const evs: EventoSse[] = [];
    for await (const ev of leerEventos(new Response(cuerpo))) evs.push(ev);
    expect(evs.map((e) => e.evento)).toEqual(["respuesta", "fin"]);
    expect(JSON.parse(evs[0].datos)).toEqual({ texto: "señal ñ" });
  });
});
