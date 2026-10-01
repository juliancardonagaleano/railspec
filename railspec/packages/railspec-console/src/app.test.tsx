import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

describe("aplicación", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("monta el router en /consola/login y ofrece los métodos de acceso", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url === "/consola/api/auth/config"
          ? new Response(JSON.stringify({ github: true, desarrollo: true }), { status: 200 })
          : new Response(JSON.stringify({ detalle: "no" }), { status: 404 }),
      ),
    );
    window.history.pushState({}, "", "/consola/login?volver=%2Facme%2Fws");
    const { router, clienteQuery } = await import("./router");
    render(
      <QueryClientProvider client={clienteQuery}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("button", { name: "Entrar con GitHub" })).toBeInTheDocument();
    expect(screen.getByLabelText("Token de desarrollo")).toBeInTheDocument();
  });
});
