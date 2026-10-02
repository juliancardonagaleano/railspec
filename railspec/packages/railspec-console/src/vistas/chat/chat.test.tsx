import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Insumo } from "../../chat/tipos";
import { guardarUltimaConversacion, leerUltimaConversacion } from "../../lib/conversacionChat";
import type { PropsChat } from "./cargador";
import { MARGEN_RENOVACION_MS, MINIMO_RENOVACION_MS, msHastaRenovar } from "./token";

// El chat real habla con /v1/chat; aquí se sustituye por uno que expone las props que recibe del shell.
const recibidas: PropsChat[] = [];
const INSUMO: Insumo = {
  formato: "railspec.insumo/v1",
  id: "5f0f3c52-7d9b-4a4b-8f6e-0c1d2e3f4a5b",
  alcance: { org: "acme", workspace: "cert" },
  repositorios: [
    { repositorio: "web", rol: "transversal", base_commit: "b".repeat(40) },
    { repositorio: "api", rol: "primario", base_commit: "a".repeat(40) },
  ],
  autor: {},
  creado_en: "2026-10-02T10:00:00Z",
  nivel_efectivo: "restringido",
  objetivo: "Emitir certificados en PDF",
  hallazgos: [],
  preguntas_abiertas: [],
  restricciones: ["sin tocar el módulo de firma"],
  veredicto_gate: { version_gate: "1", permitido: true, reglas: [], evaluado_en: "2026-10-02T10:00:00Z" },
  sha256: "0".repeat(64),
};

vi.mock("./cargador", () => ({
  ChatPerezoso: (props: PropsChat) => {
    recibidas.push(props);
    return (
      <div>
        <p>chat falso</p>
        <button onClick={() => props.alCrearConversacion?.("11111111-2222-4333-8444-555555555555")}>crear</button>
        <button onClick={() => props.alConversacionNoDisponible?.()}>no disponible</button>
        <button onClick={() => props.alNuevaConversacion?.()}>nueva</button>
        {props.alCrearUnidad ? <button onClick={() => props.alCrearUnidad?.(INSUMO)}>unidad desde insumo</button> : null}
      </div>
    );
  },
}));

const yo = (rol: string) => ({
  login: "ana",
  github_id: 1,
  plataforma_admin: false,
  organizaciones: [{ id: "acme", nombre: "ACME", rol: null, workspaces: [{ workspace: "cert", nombre: "Certificados", rol }] }],
});

const vinculo = (repositorio: string, nivel: string) => ({
  alcance: { org: "acme", workspace: "cert", repositorio },
  url: `https://github.com/acme/${repositorio}`,
  rol: "primario",
  rama_por_defecto: "main",
  nivel_codigo: nivel,
});

function simular(rol: string, extra: (url: string, init?: RequestInit) => Response | undefined = () => undefined) {
  const llamadas: { url: string; init?: RequestInit }[] = [];
  const json = (x: unknown, status = 200) => new Response(JSON.stringify(x), { status });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      llamadas.push({ url, ...(init ? { init } : {}) });
      const propia = extra(url, init);
      if (propia) return propia;
      if (url === "/consola/api/yo") return json(yo(rol));
      if (url === "/consola/api/auth/token") return json({ token: "rsc1.abc", expira_en: new Date(Date.now() + 60 * 60 * 1000).toISOString() });
      if (url === "/consola/api/orgs/acme/workspaces/cert/repositorios") return json([vinculo("api", "restringido"), vinculo("web", "interno")]);
      if (url === "/consola/api/orgs/acme/workspaces/cert/grafo/repositorios")
        return json([
          { repositorio: "api", nivel_codigo: "restringido", rol: "primario", commit: "c".repeat(40) },
          { repositorio: "web", nivel_codigo: "interno", rol: "transversal", commit: null },
        ]);
      return json({ detalle: "no" }, 404);
    }),
  );
  return llamadas;
}

async function abrirChat() {
  window.history.pushState({}, "", "/consola/acme/cert/chat");
  const { router, clienteQuery } = await import("../../router");
  render(
    <QueryClientProvider client={clienteQuery}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("chat falso")).toBeInTheDocument();
}

const ultimas = () => recibidas[recibidas.length - 1]!;

describe("ruta del chat", () => {
  beforeEach(() => {
    vi.resetModules();
    recibidas.length = 0;
    window.localStorage.clear();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("ofrece al chat los repositorios vinculados y no retoma nada si no hay conversación guardada", async () => {
    simular("lector");
    await abrirChat();
    expect(ultimas().repositoriosDisponibles).toEqual([
      { id: "api", nivel: "restringido" },
      { id: "web", nivel: "interno" },
    ]);
    expect(ultimas().conversacionId).toBeUndefined();
    expect(ultimas().token).toBe("rsc1.abc");
    // Sin tarjeta de «no disponible»: el chat es parte de la consola.
    expect(screen.queryByText(/aún no está disponible/)).toBeNull();
  });

  it("guarda la conversación creada y la retoma al volver", async () => {
    simular("lector");
    await abrirChat();
    await userEvent.click(screen.getByRole("button", { name: "crear" }));
    expect(leerUltimaConversacion("acme", "cert")).toBe("11111111-2222-4333-8444-555555555555");
  });

  it("retoma la última conversación guardada", async () => {
    guardarUltimaConversacion("acme", "cert", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee");
    simular("lector");
    await abrirChat();
    expect(ultimas().conversacionId).toBe("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee");
  });

  it("si la conversación ya no existe la olvida, avisa y vuelve a pedir repositorios", async () => {
    guardarUltimaConversacion("acme", "cert", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee");
    simular("lector");
    await abrirChat();
    await userEvent.click(screen.getByRole("button", { name: "no disponible" }));
    expect(leerUltimaConversacion("acme", "cert")).toBeUndefined();
    expect(await screen.findByText(/La conversación anterior ya no está disponible/)).toBeInTheDocument();
    await waitFor(() => expect(ultimas().conversacionId).toBeUndefined());
  });

  it("«nueva conversación» olvida la guardada", async () => {
    guardarUltimaConversacion("acme", "cert", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee");
    simular("lector");
    await abrirChat();
    await userEvent.click(screen.getByRole("button", { name: "nueva" }));
    expect(leerUltimaConversacion("acme", "cert")).toBeUndefined();
    await waitFor(() => expect(ultimas().conversacionId).toBeUndefined());
  });

  it("un lector no puede crear unidades desde el insumo", async () => {
    simular("lector");
    await abrirChat();
    expect(screen.queryByRole("button", { name: "unidad desde insumo" })).toBeNull();
    expect(ultimas().alCrearUnidad).toBeUndefined();
  });

  it("un desarrollador arranca la unidad con el insumo precargado y va a su página", async () => {
    let cuerpo: unknown;
    const llamadas = simular("desarrollador", (url, init) => {
      if (url === "/consola/api/tools/unit.start") {
        cuerpo = JSON.parse(String(init?.body));
        return new Response(
          JSON.stringify({ estado: { unidad: { org: "acme", workspace: "cert", unidad: "0007-emitir-certificados" } } }),
          { status: 200 },
        );
      }
      return undefined;
    });
    await abrirChat();
    await userEvent.click(screen.getByRole("button", { name: "unidad desde insumo" }));

    const dialogo = await screen.findByRole("dialog", { name: "Nueva unidad" });
    expect(dialogo).toBeInTheDocument();
    expect(screen.getByLabelText("Título")).toHaveValue("Emitir certificados en PDF");
    expect(screen.getByLabelText("Pedido")).toHaveValue("Emitir certificados en PDF\n\nRestricciones:\n- sin tocar el módulo de firma");
    expect(screen.getByLabelText("Insumos del chat (ids)")).toHaveValue(INSUMO.id);
    // El repositorio primario va primero aunque el insumo lo traiga después; la rama sale del vínculo.
    expect(screen.getByLabelText("Repositorio 1")).toHaveValue("api");
    expect(screen.getByLabelText("Commit base 1")).toHaveValue("a".repeat(40));
    await waitFor(() => expect(screen.getByLabelText("Rama 1")).toHaveValue("main"));
    expect(screen.getByLabelText("Repositorio 2")).toHaveValue("web");

    await userEvent.click(screen.getByRole("button", { name: "Arrancar unidad" }));
    await waitFor(() => expect(cuerpo).toBeDefined());
    expect(cuerpo).toEqual({
      alcance: { org: "acme", workspace: "cert" },
      repositorios: [
        { repositorio: "api", rama: "main", base_commit: "a".repeat(40) },
        { repositorio: "web", rama: "main", base_commit: "b".repeat(40) },
      ],
      titulo: "Emitir certificados en PDF",
      pedido: "Emitir certificados en PDF\n\nRestricciones:\n- sin tocar el módulo de firma",
      insumos: [INSUMO.id],
      version_contrato_cliente: "1.4",
    });
    expect(llamadas.find((l) => l.url === "/consola/api/tools/unit.start")?.init?.method).toBe("POST");
    await waitFor(() => expect(window.location.pathname).toBe("/consola/acme/cert/unidades/0007-emitir-certificados"));
  });

  it("no envía la unidad si falta algo y lo explica", async () => {
    simular("desarrollador");
    await abrirChat();
    await userEvent.click(screen.getByRole("button", { name: "unidad desde insumo" }));
    await screen.findByRole("dialog", { name: "Nueva unidad" });
    await userEvent.clear(screen.getByLabelText("Commit base 2"));
    await userEvent.type(screen.getByLabelText("Commit base 2"), "abc123");
    await userEvent.click(screen.getByRole("button", { name: "Arrancar unidad" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("El commit base de web debe ser un sha completo");
    expect(vi.mocked(fetch).mock.calls.some(([u]) => u === "/consola/api/tools/unit.start")).toBe(false);
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
