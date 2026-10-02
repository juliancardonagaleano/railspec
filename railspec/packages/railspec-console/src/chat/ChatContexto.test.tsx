import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatContexto, PATRON_REPOSITORIO } from "./ChatContexto";
import type { Conversacion } from "./tipos";

const ID = "11111111-2222-4333-8444-555555555555";

const conversacion = (repositorios: string[]): Conversacion => ({
  id: ID,
  alcance: { org: "acme", workspace: "cert" },
  repositorios,
  autor: {},
  nivel_efectivo: "restringido",
  creada_en: "2026-10-02T10:00:00Z",
  expira_en: "2026-10-02T18:00:00Z",
  consumo_fuga: { caracteres: 0, tope: 1000 },
  consumo_fuga_usuario: { caracteres: 0, tope: 5000 },
  bloqueos: 0,
  limitada: false,
});

function servidor(extra: (url: string, init?: RequestInit) => Response | undefined = () => undefined) {
  const cuerpos: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const propia = extra(url, init);
      if (propia) return propia;
      if (url === "/v1/chat/conversaciones" && init?.method === "POST") {
        const cuerpo = JSON.parse(String(init.body)) as { repositorios: string[] };
        cuerpos.push(cuerpo);
        return new Response(JSON.stringify({ conversacion: conversacion(cuerpo.repositorios) }), { status: 201 });
      }
      return new Response(JSON.stringify({ detalle: "no" }), { status: 404 });
    }),
  );
  return cuerpos;
}

const base = { apiBase: "", token: "rsc1.abc", org: "acme", workspace: "cert" };

describe("selector de repositorios del chat", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("el patrón de repositorio rechaza «owner/repo» (lo que daba 422) y acepta el nombre vinculado", () => {
    expect(PATRON_REPOSITORIO.test("acme/api")).toBe(false);
    expect(PATRON_REPOSITORIO.test("api")).toBe(true);
    expect(PATRON_REPOSITORIO.test("Api")).toBe(false);
  });

  it("con los vínculos ofrece casillas (todas marcadas) y crea la conversación con las elegidas", async () => {
    const cuerpos = servidor();
    render(<ChatContexto {...base} repositoriosDisponibles={[{ id: "api", nivel: "restringido" }, { id: "web", nivel: "interno" }]} />);
    expect(screen.getByRole("checkbox", { name: /^api/ })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /^web/ })).toBeChecked();
    await userEvent.click(screen.getByRole("checkbox", { name: /^web/ }));
    await userEvent.click(screen.getByRole("button", { name: "Iniciar conversación" }));
    expect(await screen.findByText(/acme \/ cert · api/)).toBeInTheDocument();
    expect(cuerpos).toEqual([{ alcance: { org: "acme", workspace: "cert" }, repositorios: ["api"] }]);
  });

  it("no deja iniciar sin ningún repositorio marcado", async () => {
    servidor();
    render(<ChatContexto {...base} repositoriosDisponibles={[{ id: "api" }]} />);
    await userEvent.click(screen.getByRole("checkbox", { name: /^api/ }));
    expect(screen.getByRole("button", { name: "Iniciar conversación" })).toBeDisabled();
  });

  it("sin repositorios vinculados lo dice en vez de pedir texto", () => {
    servidor();
    render(<ChatContexto {...base} repositoriosDisponibles={[]} />);
    expect(screen.getByText(/no tiene repositorios vinculados/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Iniciar conversación" })).toBeNull();
  });

  it("sin lista de vínculos cae al texto, que valida el nombre antes de enviarlo", async () => {
    const cuerpos = servidor();
    render(<ChatContexto {...base} />);
    const campo = screen.getByLabelText(/Repositorios a consultar/);
    await userEvent.type(campo, "acme/api");
    expect(screen.getByRole("alert")).toHaveTextContent("acme/api");
    expect(screen.getByRole("button", { name: "Iniciar conversación" })).toBeDisabled();
    await userEvent.clear(campo);
    await userEvent.type(campo, "api, web");
    await userEvent.click(screen.getByRole("button", { name: "Iniciar conversación" }));
    await screen.findByText(/acme \/ cert/);
    expect(cuerpos).toEqual([{ alcance: { org: "acme", workspace: "cert" }, repositorios: ["api", "web"] }]);
  });
});

describe("conversación retomada", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("carga la conversación indicada sin crear otra", async () => {
    const cuerpos = servidor((url) =>
      url === `/v1/chat/conversaciones/${ID}`
        ? new Response(JSON.stringify({ conversacion: conversacion(["api"]), mensajes: [] }), { status: 200 })
        : undefined,
    );
    render(<ChatContexto {...base} conversacionId={ID} />);
    expect(await screen.findByText(/acme \/ cert · api/)).toBeInTheDocument();
    expect(cuerpos).toEqual([]);
  });

  it("si el servidor responde 404 avisa al shell para que la olvide", async () => {
    servidor((url) =>
      url === `/v1/chat/conversaciones/${ID}`
        ? new Response(JSON.stringify({ codigo: "no-encontrado", detalle: "conversación no encontrada" }), { status: 404 })
        : undefined,
    );
    const alNoDisponible = vi.fn();
    render(<ChatContexto {...base} conversacionId={ID} alConversacionNoDisponible={alNoDisponible} />);
    await waitFor(() => expect(alNoDisponible).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("button", { name: "Empezar otra conversación" })).toBeInTheDocument();
  });

  it("«Nueva conversación» avisa al shell y vuelve al selector de repositorios", async () => {
    servidor((url) =>
      url === `/v1/chat/conversaciones/${ID}`
        ? new Response(JSON.stringify({ conversacion: conversacion(["api"]), mensajes: [] }), { status: 200 })
        : undefined,
    );
    const alNueva = vi.fn();
    render(<ChatContexto {...base} conversacionId={ID} repositoriosDisponibles={[{ id: "api" }, { id: "web" }]} alNuevaConversacion={alNueva} />);
    await userEvent.click(await screen.findByRole("button", { name: "Nueva conversación" }));
    expect(alNueva).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("button", { name: "Iniciar conversación" })).toBeInTheDocument();
  });
});
