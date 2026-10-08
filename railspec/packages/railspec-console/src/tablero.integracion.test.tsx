import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { INTERVALO_VIVO_MS } from "./vistas/tablero/Tablero";

const yo = {
  login: "ana",
  github_id: 1,
  plataforma_admin: false,
  organizaciones: [{ id: "acme", nombre: "ACME", rol: null, workspaces: [{ workspace: "cert", nombre: "Certificados", rol: "lector" }] }],
};

const unidad = (id: string, fase: string) => ({
  unidad: id,
  titulo: `Título ${id}`,
  fase,
  estado: "en-progreso",
  modo: "interactivo",
  riesgo: "bajo",
  repositorio_primario: "api",
  dueno_login: "ana",
  integrada: fase === "done",
  actualizado_en: "2026-09-30T10:00:00Z",
});

describe("tablero integrado", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("pinta el marco y las unidades en su columna de fase", async () => {
    const fetchFalso = vi.fn(async (url: string, init?: RequestInit) => {
      const json = (x: unknown) => new Response(JSON.stringify(x), { status: 200 });
      if (url === "/consola/api/yo") return json(yo);
      if (url === "/consola/api/orgs/acme/workspaces/cert/grafo/repositorios") return json([]);
      if (url === "/consola/api/tools/unit.list") {
        expect((init?.headers as Record<string, string>)["X-Railspec-Consola"]).toBe("1");
        return json({ unidades: [unidad("0001-a", "spec"), unidad("0002-b", "done"), unidad("0003-c", "spec")], cursor_siguiente: null });
      }
      return new Response(JSON.stringify({ detalle: "no" }), { status: 404 });
    });
    vi.stubGlobal("fetch", fetchFalso);
    window.history.pushState({}, "", "/consola/acme/cert");
    const { router, clienteQuery } = await import("./router");
    render(
      <QueryClientProvider client={clienteQuery}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("heading", { name: "Tablero de unidades" })).toBeInTheDocument();
    const spec = (await screen.findByRole("heading", { name: /^Spec/ })).closest("[role=group]") as HTMLElement;
    expect(within(spec).getAllByRole("listitem")).toHaveLength(2);
    const hecha = screen.getByRole("heading", { name: /^Hecha/ }).closest("[role=group]") as HTMLElement;
    expect(within(hecha).getByText("integrada")).toBeInTheDocument();
    // Un lector no ve los enlaces de administración.
    expect(screen.queryByRole("link", { name: "Configuración" })).toBeNull();
    expect(screen.getByRole("link", { name: "Chat" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Mandatos" })).toHaveAttribute("href", "/consola/acme/cert/mandatos");
  });
});

describe("tablero en vivo con carriles", () => {
  beforeEach(() => vi.resetModules());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const json = (x: unknown) => new Response(JSON.stringify(x), { status: 200 });
  const yoConRol = (rol: string) => ({
    ...yo,
    organizaciones: [{ ...yo.organizaciones[0]!, workspaces: [{ workspace: "cert", nombre: "Certificados", rol }] }],
  });

  async function abrir(ruta: string, rol: string, listas: unknown[][]) {
    let n = 0;
    const llamadas: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url === "/consola/api/yo") return json(yoConRol(rol));
        if (url === "/consola/api/orgs/acme/cert/grafo/repositorios" || url.endsWith("/grafo/repositorios")) return json([]);
        if (url === "/consola/api/tools/unit.list") {
          llamadas.push(JSON.parse(String(init?.body)));
          const lista = listas[Math.min(n++, listas.length - 1)]!;
          return json({ unidades: lista, cursor_siguiente: null });
        }
        return new Response(JSON.stringify({ detalle: "no" }), { status: 404 });
      }),
    );
    window.history.pushState({}, "", ruta);
    const { router, clienteQuery } = await import("./router");
    render(
      <QueryClientProvider client={clienteQuery}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    return llamadas;
  }

  const con = (id: string, repo: string, fase = "spec") => ({ ...unidad(id, fase), repositorio_primario: repo });

  it("reparte las unidades en carriles por repositorio, cada uno con sus columnas", async () => {
    await abrir("/consola/acme/cert?carriles=repositorio", "lector", [[con("0001-a", "web", "plan"), con("0002-b", "api"), con("0003-c", "web")]]);
    const web = await screen.findByRole("region", { name: "Carril web" });
    expect(within(web).getAllByRole("listitem")).toHaveLength(2);
    const api = screen.getByRole("region", { name: "Carril api" });
    expect(within(api).getAllByRole("listitem")).toHaveLength(1);
    // Cada carril trae su fila de columnas de fase.
    expect(within(web).getByRole("heading", { name: /^Plan/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Carriles")).toHaveValue("repositorio");
  });

  it("solo quien puede arrancar unidades ve «Nueva unidad»", async () => {
    await abrir("/consola/acme/cert", "lector", [[con("0001-a", "api")]]);
    await screen.findByRole("heading", { name: /^Spec/ });
    expect(screen.queryByRole("button", { name: "Nueva unidad" })).toBeNull();
    cleanup();
    vi.resetModules();
    await abrir("/consola/acme/cert", "desarrollador", [[con("0001-a", "api")]]);
    expect(await screen.findByRole("button", { name: "Nueva unidad" })).toBeInTheDocument();
  });

  it("en vivo vuelve a leer y resalta lo que cambió; apagado, no relee", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const llamadas = await abrir("/consola/acme/cert", "lector", [
      [con("0001-a", "api")],
      [con("0001-a", "api", "plan"), con("0002-b", "api")],
    ]);
    await screen.findByRole("heading", { name: /^Spec/ });
    expect(llamadas).toHaveLength(1);
    expect(screen.queryByText("actualizada")).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(INTERVALO_VIVO_MS + 100);
    });
    await waitFor(() => expect(llamadas).toHaveLength(2));
    const plan = (await screen.findByRole("heading", { name: /^Plan/ })).closest("[role=group]") as HTMLElement;
    expect(within(plan).getByText("Título 0001-a")).toBeInTheDocument();
    expect(screen.getAllByText("actualizada")).toHaveLength(2);

    await userEvent.click(screen.getByRole("checkbox", { name: "En vivo" }));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(INTERVALO_VIVO_MS * 3);
    });
    expect(llamadas).toHaveLength(2);
    expect(window.location.search).toContain("vivo=no");
  });
});
