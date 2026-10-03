import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProveedorContexto } from "../../api/tipos";
import { auditoria, json, montarConQuery, servidorFalso } from "../../pruebas/servidor";
import { Proveedores } from "./Proveedores";

// Proveedores de contexto: el alta se hace en un diálogo que se cierra al guardar; la tarjeta dice «Guardado».

const pce: ProveedorContexto = {
  org: "acme",
  workspace: null,
  rol: "gobernanza",
  nombre: "pce",
  url: "https://pce.example.com/mcp",
  credencial_configurada: true,
  politica_fallo: "blanda",
  version: 1,
  auditoria,
};

describe("proveedores de contexto", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("tras guardar cierra el diálogo y la tarjeta anuncia el guardado con la versión nueva", async () => {
    const user = userEvent.setup();
    const lista: ProveedorContexto[] = [];
    const s = servidorFalso({
      "GET /orgs/acme/proveedores-contexto": () => lista,
      "PUT /orgs/acme/proveedores-contexto/gobernanza/pce": (l) => {
        lista.push({ ...pce, url: l.cuerpo.url, version: 1 });
        return json(201, lista[0]);
      },
    });
    montarConQuery(<Proveedores org="acme" editable />);

    await user.click(await screen.findByRole("button", { name: "Nuevo proveedor" }));
    const dialogo = await screen.findByRole("dialog", { name: "Nuevo proveedor de contexto" });
    await user.type(within(dialogo).getByLabelText("Nombre"), "pce");
    const url = within(dialogo).getByLabelText("URL");
    await user.clear(url);
    await user.type(url, "https://pce.example.com/mcp");
    await user.click(within(dialogo).getByRole("button", { name: "Guardar" }));

    expect(await screen.findByText(/^Guardado · versión 1\b/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(s.de("PUT", "/orgs/acme/proveedores-contexto/gobernanza/pce")).toHaveLength(1);
    expect(await screen.findByText("https://pce.example.com/mcp")).toBeInTheDocument();
  });

  it("un error del servidor no deja un «Guardado» y mantiene el diálogo abierto", async () => {
    const user = userEvent.setup();
    servidorFalso({
      "GET /orgs/acme/proveedores-contexto": () => [pce],
      "PUT /orgs/acme/proveedores-contexto/gobernanza/pce": () => json(422, { detalle: "URL no permitida" }),
    });
    montarConQuery(<Proveedores org="acme" editable />);

    await user.click(await screen.findByRole("button", { name: "Editar" }));
    const dialogo = await screen.findByRole("dialog", { name: "Editar pce" });
    await user.click(within(dialogo).getByRole("button", { name: "Guardar" }));

    expect(await within(dialogo).findByRole("alert")).toHaveTextContent("URL no permitida");
    expect(screen.queryByText(/^Guardado ·/)).toBeNull();
  });

  it("tras un 409 el formulario se reabre con la versión vigente y el siguiente guardado la envía", async () => {
    const user = userEvent.setup();
    let vigente: ProveedorContexto = { ...pce, version: 1 };
    const s = servidorFalso({
      "GET /orgs/acme/proveedores-contexto": () => [vigente],
      "PUT /orgs/acme/proveedores-contexto/gobernanza/pce": (l) => {
        if (l.cuerpo.version !== vigente.version) return json(409, { detalle: "versión desactualizada", version_actual: vigente.version });
        vigente = { ...vigente, url: l.cuerpo.url, version: vigente.version + 1 };
        return vigente;
      },
    });
    montarConQuery(<Proveedores org="acme" editable />);

    await user.click(await screen.findByRole("button", { name: "Editar" }));
    await screen.findByRole("dialog", { name: "Editar pce" });
    vigente = { ...pce, url: "https://otra.example.com/mcp", version: 3 };
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Guardar" }));

    await waitFor(() => expect(within(screen.getByRole("dialog")).getByLabelText("URL")).toHaveValue("https://otra.example.com/mcp"));
    expect(within(screen.getByRole("dialog")).getByText(/Otra persona modificó este registro/)).toHaveTextContent("(versión actual 3)");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(s.de("PUT", "/orgs/acme/proveedores-contexto/gobernanza/pce").map((l) => l.cuerpo.version)).toEqual([1, 3]);
  });
});
