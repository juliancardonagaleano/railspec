import { QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useParams } from "@tanstack/react-router";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Yo } from "../../api/tipos";
import { clienteDePrueba, json, servidorFalso } from "../../pruebas/servidor";
import { Inicio, InicioOrg } from "./Inicio";

// Inicio (`/`) lleva al primer workspace visible; InicioOrg (`/$org`) lista los de la organización.
// El router es de prueba: las rutas de destino son marcadores que dicen dónde se aterrizó.

type Rol = NonNullable<Yo["organizaciones"][number]["rol"]>;
const ws = (workspace: string, rol: Rol = "desarrollador") => ({
  workspace,
  nombre: workspace.toUpperCase(),
  rol,
});
const org = (id: string, workspaces: ReturnType<typeof ws>[] = [], rol: Rol | null = "desarrollador") => ({
  id,
  nombre: `Org ${id}`,
  rol,
  workspaces,
});
const yo = (organizaciones: Yo["organizaciones"], plataforma_admin = false): Yo => ({
  login: "ana",
  github_id: 1,
  plataforma_admin,
  organizaciones,
});

function Aterrizaje({ etiqueta }: { etiqueta: string }) {
  const p = useParams({ strict: false }) as Record<string, string>;
  return (
    <p data-testid="destino">
      {etiqueta}:{Object.values(p).join("/")}
    </p>
  );
}

function montar(datos: Yo, url: string) {
  servidorFalso({ "GET /yo": () => json(200, datos) });
  const raiz = createRootRoute({ component: Outlet });
  const hoja = (path: string, component: () => React.ReactNode) => createRoute({ getParentRoute: () => raiz, path, component });
  const router = createRouter({
    routeTree: raiz.addChildren([
      hoja("/", Inicio),
      hoja("/organizaciones", () => <Aterrizaje etiqueta="organizaciones" />),
      hoja("/$org", InicioOrg),
      hoja("/$org/administracion", () => <Aterrizaje etiqueta="administracion" />),
      hoja("/$org/$ws", () => <Aterrizaje etiqueta="workspace" />),
    ]),
    history: createMemoryHistory({ initialEntries: [url] }),
  });
  render(
    <QueryClientProvider client={clienteDePrueba()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

describe("Inicio", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("redirige al primer workspace de la primera organización que tenga alguno", async () => {
    const router = montar(yo([org("vacia"), org("acme", [ws("cert"), ws("pagos")]), org("otra", [ws("x")])]), "/");
    expect(await screen.findByTestId("destino")).toHaveTextContent("workspace:acme/cert");
    expect(router.state.location.pathname).toBe("/acme/cert");
  });

  it("sin workspaces visibles entra a la primera organización y ofrece ir a administración", async () => {
    const router = montar(yo([org("acme")]), "/");
    expect(await screen.findByText("Esta organización aún no tiene workspaces visibles para ti")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/acme");
    expect(screen.getByRole("link", { name: "Ir a administración" })).toHaveAttribute("href", "/acme/administracion");
  });

  it("sin organizaciones: una persona normal recibe la indicación de pedir un rol", async () => {
    montar(yo([]), "/");
    expect(await screen.findByText("Hola, ana")).toBeInTheDocument();
    expect(screen.getByText("Aún no perteneces a ninguna organización")).toBeInTheDocument();
    expect(screen.getByText("Pide a un administrador que te asigne un rol.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Crear una organización" })).not.toBeInTheDocument();
  });

  it("sin organizaciones: el admin de plataforma puede crear una", async () => {
    montar(yo([], true), "/");
    expect(await screen.findByRole("link", { name: "Crear una organización" })).toHaveAttribute("href", "/organizaciones");
    expect(screen.queryByText("Pide a un administrador que te asigne un rol.")).not.toBeInTheDocument();
  });

  it("no pinta nada hasta conocer la sesión", () => {
    servidorFalso({ "GET /yo": () => new Promise<Response>(() => undefined) as unknown as Response });
    const { container } = render(
      <QueryClientProvider client={clienteDePrueba()}>
        <Inicio />
      </QueryClientProvider>,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("InicioOrg", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lista los workspaces con su rol y enlaza a cada uno", async () => {
    montar(yo([org("acme", [ws("cert", "workspace-admin"), ws("pagos", "lector")])]), "/acme");
    expect(await screen.findByRole("heading", { name: "Org acme" })).toBeInTheDocument();
    const cert = screen.getByRole("link", { name: /CERT/ });
    expect(cert).toHaveAttribute("href", "/acme/cert");
    expect(cert).toHaveTextContent("cert · workspace-admin");
    expect(screen.getByRole("link", { name: /PAGOS/ })).toHaveTextContent("pagos · lector");
  });

  it("una organización ajena o inexistente dice que no hay acceso", async () => {
    montar(yo([org("acme", [ws("cert")])]), "/otra");
    expect(await screen.findByText("No tienes acceso a esta organización")).toBeInTheDocument();
  });
});
