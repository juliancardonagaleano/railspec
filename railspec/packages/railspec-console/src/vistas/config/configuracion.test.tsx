import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Rol } from "../../api/tipos";
import { json, montarEnRuta, perfilConfig, servidorFalso, yo } from "../../pruebas/servidor";
import { ConfiguracionOrg, ConfiguracionWorkspace } from "./Configuracion";

// Páginas de configuración (org y workspace): qué pestañas hay, qué pide cada una al servidor y qué se
// puede editar según el rol. Autoriza el servidor; aquí se comprueba que la SPA no ofrece lo que no toca.

const PESTANAS = ["Perfiles", "Presupuestos", "Proveedores de contexto", "Suscripciones", "Catálogo de modelos"];

function servidor(rolOrg: Rol | null, rolWs?: Rol) {
  return servidorFalso({
    "GET /yo": () => yo(rolOrg, rolWs),
    "GET /orgs/acme/perfiles": () => [perfilConfig("estandar")],
    "GET /orgs/acme/presupuestos": () => [],
    "GET /orgs/acme/proveedores-contexto": () => [],
    "GET /orgs/acme/suscripciones": () => ({
      cifrado: { disponible: true, variable: "RAILSPEC_CLAVE_MAESTRA" },
      suscripciones: [],
    }),
    "GET /orgs/acme/catalogo": () => [],
    "GET /orgs/acme/catalogo/estado": () => ({ sincronizable: true, proveedores: [] }),
  });
}

const abrir = async (nombre: string) => userEvent.click(await screen.findByRole("tab", { name: nombre }));

describe("Configuración de la organización", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("un admin. de organización ve las cinco pestañas, Perfiles primero, y puede editar", async () => {
    const s = servidor("org-admin");
    montarEnRuta(ConfiguracionOrg, "/$org/configuracion", "/acme/configuracion");

    expect(await screen.findByText("Valores por defecto para todos los workspaces.")).toBeInTheDocument();
    const pestanas = within(screen.getByRole("tablist", { name: "Configuración" })).getAllByRole("tab");
    expect(pestanas.map((p) => p.textContent)).toEqual(PESTANAS);
    expect(screen.getByRole("tab", { name: "Perfiles" })).toHaveAttribute("aria-selected", "true");

    await abrir("Suscripciones");
    expect(await screen.findByRole("button", { name: "Nueva suscripción" })).toBeInTheDocument();
    expect(await screen.findByText("Sin suscripciones")).toBeInTheDocument();

    await abrir("Catálogo de modelos");
    expect(await screen.findByRole("button", { name: "Sincronizar todos" })).toBeInTheDocument();
    expect(s.noSimuladas).toEqual([]);
  });

  it("una persona sin rol de admin. ve todo en solo lectura y no puede crear suscripciones ni sincronizar", async () => {
    const s = servidor("desarrollador");
    montarEnRuta(ConfiguracionOrg, "/$org/configuracion", "/acme/configuracion");

    expect(await screen.findByText("Solo lectura: necesitas ser admin. de organización para cambiarla.")).toBeInTheDocument();
    await abrir("Suscripciones");
    expect(await screen.findByText("Sin suscripciones")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Nueva suscripción" })).not.toBeInTheDocument();

    await abrir("Catálogo de modelos");
    expect(await screen.findByText("El catálogo está vacío")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sincronizar todos" })).not.toBeInTheDocument();
    expect(s.de("PUT", "/orgs/acme/perfiles/estandar")).toHaveLength(0);
  });

  it("solo la pestaña abierta pide sus datos (presupuestos y proveedores esperan a abrirse)", async () => {
    const s = servidor("org-admin");
    montarEnRuta(ConfiguracionOrg, "/$org/configuracion", "/acme/configuracion");
    await screen.findByText("Valores por defecto para todos los workspaces.");
    await vi.waitFor(() => expect(s.de("GET", "/orgs/acme/perfiles")).toHaveLength(1));
    // Perfiles lee también suscripciones y catálogo (el editor asocia una y elige modelos); presupuestos no.
    expect(s.de("GET", "/orgs/acme/presupuestos")).toHaveLength(0);
    expect(s.de("GET", "/orgs/acme/proveedores-contexto")).toHaveLength(0);
    await abrir("Presupuestos");
    await vi.waitFor(() => expect(s.de("GET", "/orgs/acme/presupuestos")).toHaveLength(1));
  });

  it("si una pestaña falla muestra el error ahí y las demás siguen sirviendo", async () => {
    servidorFalso({
      "GET /yo": () => yo("org-admin"),
      "GET /orgs/acme/perfiles": () => [perfilConfig("estandar")],
      "GET /orgs/acme/suscripciones": () => json(500, { detalle: "base caída" }),
    });
    montarEnRuta(ConfiguracionOrg, "/$org/configuracion", "/acme/configuracion");
    await abrir("Suscripciones");
    expect(await screen.findByText(/base caída/)).toBeInTheDocument();
    await abrir("Perfiles");
    expect(screen.queryByText(/base caída/)).not.toBeInTheDocument();
  });
});

describe("Configuración del workspace", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("pide los ajustes del workspace y un admin. de workspace edita perfiles pero no suscripciones ni catálogo", async () => {
    const s = servidor("desarrollador", "workspace-admin");
    montarEnRuta(ConfiguracionWorkspace, "/$org/$ws/configuracion", "/acme/cert/configuracion");

    expect(await screen.findByText("Ajustes propios de cert; lo demás se hereda de acme.")).toBeInTheDocument();
    await vi.waitFor(() => expect(s.de("GET", "/orgs/acme/perfiles")).toHaveLength(1));
    expect(s.de("GET", "/orgs/acme/perfiles")[0]?.consulta.get("workspace")).toBe("cert");

    await abrir("Suscripciones");
    expect(await screen.findByText("Sin suscripciones")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Nueva suscripción" })).not.toBeInTheDocument();
    // Las suscripciones son de la organización: la consulta no lleva workspace.
    expect(s.de("GET", "/orgs/acme/suscripciones")[0]?.consulta.has("workspace")).toBe(false);

    await abrir("Catálogo de modelos");
    expect(await screen.findByText("El catálogo está vacío")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sincronizar todos" })).not.toBeInTheDocument();
  });

  it("un admin. de organización sí gestiona suscripciones desde la vista de un workspace", async () => {
    servidor("org-admin");
    montarEnRuta(ConfiguracionWorkspace, "/$org/$ws/configuracion", "/acme/cert/configuracion");
    await abrir("Suscripciones");
    expect(await screen.findByRole("button", { name: "Nueva suscripción" })).toBeInTheDocument();
  });
});
