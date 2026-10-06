import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { clienteDePrueba, json, servidorFalso } from "../../pruebas/servidor";
import { Login, volverSeguro } from "./Login";

// Login: métodos de acceso que ofrece el servidor, redirección tras entrar y rechazo de `volver` externo.

function montar(url: string) {
  const raiz = createRootRoute();
  const ruta = createRoute({
    getParentRoute: () => raiz,
    path: "login",
    validateSearch: (s: Record<string, unknown>): { volver?: string } => (typeof s.volver === "string" ? { volver: s.volver } : {}),
    component: Login,
  });
  const router = createRouter({ routeTree: raiz.addChildren([ruta]), history: createMemoryHistory({ initialEntries: [url] }) });
  render(
    <QueryClientProvider client={clienteDePrueba()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

/** `window.location.assign` no navega en jsdom: se sustituye `location` para ver el destino. */
function espiarNavegacion() {
  const assign = vi.fn();
  vi.stubGlobal("location", { ...window.location, assign });
  return assign;
}

describe("volverSeguro", () => {
  it.each([
    [undefined, "/"],
    ["", "/"],
    ["/acme/cert", "/acme/cert"],
    ["/acme/cert?estado=abierta", "/acme/cert?estado=abierta"],
    ["//evil.example/x", "/"],
    ["https://evil.example", "/"],
    ["javascript:alert(1)", "/"],
    ["acme/cert", "/"],
  ])("%j → %j", (entrada, esperado) => {
    expect(volverSeguro(entrada)).toBe(esperado);
  });
});

describe("Login", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("con GitHub: el botón va al inicio OAuth del servidor llevando el destino", async () => {
    servidorFalso({ "GET /auth/config": () => ({ github: true, desarrollo: false }) });
    const assign = espiarNavegacion();
    montar("/login?volver=%2Facme%2Fcert");
    await userEvent.click(await screen.findByRole("button", { name: "Entrar con GitHub" }));
    expect(assign).toHaveBeenCalledWith("/consola/api/auth/github/inicio?volver=%2Facme%2Fcert");
    expect(screen.queryByLabelText("Token de desarrollo")).not.toBeInTheDocument();
  });

  it("un `volver` hacia otro sitio se descarta antes de ir a GitHub", async () => {
    servidorFalso({ "GET /auth/config": () => ({ github: true, desarrollo: false }) });
    const assign = espiarNavegacion();
    montar("/login?volver=%2F%2Fevil.example");
    await userEvent.click(await screen.findByRole("button", { name: "Entrar con GitHub" }));
    expect(assign).toHaveBeenCalledWith("/consola/api/auth/github/inicio?volver=%2F");
  });

  it("token de desarrollo: se envía una vez, el campo es de contraseña y se vuelve a la ruta pedida", async () => {
    const s = servidorFalso({
      "GET /auth/config": () => ({ github: false, desarrollo: true }),
      "POST /auth/desarrollo": () => null,
    });
    const assign = espiarNavegacion();
    montar("/login?volver=%2Facme%2Fcert");
    const campo = await screen.findByLabelText("Token de desarrollo");
    expect(campo).toHaveAttribute("type", "password");
    expect(screen.queryByRole("button", { name: "Entrar con GitHub" })).not.toBeInTheDocument();
    const boton = screen.getByRole("button", { name: "Entrar con token" });
    expect(boton).toBeDisabled();
    await userEvent.type(campo, "  tok-dev  ");
    await userEvent.click(boton);
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/consola/acme/cert"));
    expect(s.de("POST", "/auth/desarrollo")).toHaveLength(1);
    expect(s.de("POST", "/auth/desarrollo")[0]?.cuerpo).toEqual({ token: "tok-dev" });
  });

  it("un token rechazado muestra el error y no navega", async () => {
    servidorFalso({
      "GET /auth/config": () => ({ github: false, desarrollo: true }),
      "POST /auth/desarrollo": () => json(401, { detalle: "token de desarrollo desconocido" }),
    });
    const assign = espiarNavegacion();
    montar("/login");
    await userEvent.type(await screen.findByLabelText("Token de desarrollo"), "malo");
    await userEvent.click(screen.getByRole("button", { name: "Entrar con token" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("token de desarrollo desconocido");
    expect(assign).not.toHaveBeenCalled();
  });

  it("sin métodos configurados lo dice, y si la consulta falla ofrece reintentar", async () => {
    servidorFalso({ "GET /auth/config": () => ({ github: false, desarrollo: false }) });
    montar("/login");
    expect(await screen.findByText("No hay métodos de acceso configurados")).toBeInTheDocument();
  });

  it("si /auth/config falla muestra el error", async () => {
    servidorFalso({ "GET /auth/config": () => json(500, { detalle: "caído" }) });
    montar("/login");
    expect(await screen.findByRole("button", { name: /reintentar/i })).toBeInTheDocument();
  });
});
