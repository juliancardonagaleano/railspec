import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { resolverCargadorChat, type PropsChat } from "./cargador";
import { ContenedorChat } from "./RutaChat";
import { MARGEN_RENOVACION_MS, MINIMO_RENOVACION_MS, msHastaRenovar } from "./token";

describe("cargador del chat", () => {
  it("sin módulo src/chat/index.tsx no hay cargador", () => {
    expect(resolverCargadorChat({})).toBeNull();
  });

  it("sin módulo muestra la tarjeta de no disponible", () => {
    render(<ContenedorChat Chat={null} org="acme" ws="cert" />);
    expect(screen.getByText("El chat de contexto aún no está disponible")).toBeInTheDocument();
  });

  it("prefiere el export ChatContexto y si no, el default", async () => {
    const A = (_: PropsChat) => null;
    const B = (_: PropsChat) => null;
    const conNombre = resolverCargadorChat({ "../../chat/index.tsx": () => Promise.resolve({ ChatContexto: A, default: B }) });
    const soloDefault = resolverCargadorChat({ "../../chat/index.tsx": () => Promise.resolve({ default: B }) });
    expect((await conNombre!()).default).toBe(A);
    expect((await soloDefault!()).default).toBe(B);
  });

  it("un módulo sin componente falla con un error claro", async () => {
    const cargar = resolverCargadorChat({ "../../chat/index.tsx": () => Promise.resolve({}) });
    await expect(cargar!()).rejects.toThrow(/ChatContexto/);
  });
});

describe("renovación del token del chat", () => {
  const ahora = Date.parse("2026-10-01T10:00:00Z");

  it("renueva 5 minutos antes de expirar", () => {
    expect(msHastaRenovar("2026-10-01T11:00:00Z", ahora)).toBe(60 * 60 * 1000 - MARGEN_RENOVACION_MS);
  });

  it("si ya expiró o la fecha no es válida, renueva ya", () => {
    expect(msHastaRenovar("2026-10-01T09:59:00Z", ahora)).toBe(0);
    expect(msHastaRenovar("no-es-fecha", ahora)).toBe(0);
  });

  it("con vida menor que el margen renueva a mitad de camino sin bajar del mínimo", () => {
    expect(msHastaRenovar("2026-10-01T10:04:00Z", ahora)).toBe(2 * 60 * 1000);
    expect(msHastaRenovar("2026-10-01T10:00:40Z", ahora)).toBe(MINIMO_RENOVACION_MS);
    expect(msHastaRenovar("2026-10-01T10:00:10Z", ahora)).toBe(10 * 1000);
  });
});
