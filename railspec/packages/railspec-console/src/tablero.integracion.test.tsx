import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

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
    const spec = (await screen.findByRole("heading", { name: /^Spec/ })).closest("section")!;
    expect(within(spec).getAllByRole("listitem")).toHaveLength(2);
    const hecha = screen.getByRole("heading", { name: /^Hecha/ }).closest("section")!;
    expect(within(hecha).getByText("integrada")).toBeInTheDocument();
    // Un lector no ve los enlaces de administración.
    expect(screen.queryByRole("link", { name: "Configuración" })).toBeNull();
    expect(screen.getByRole("link", { name: "Chat" })).toBeInTheDocument();
  });
});
