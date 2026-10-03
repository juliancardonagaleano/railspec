import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Organizacion } from "../../api/tipos";
import { json, montarEnRuta, organizacion, servidorFalso, yo } from "../../pruebas/servidor";
import { Organizaciones } from "./Organizaciones";

// Lista de organizaciones: un admin de plataforma crea una; el diálogo se cierra y la página dice «Guardado».

describe("organizaciones", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("crear una organización cierra el diálogo y anuncia el guardado con su versión", async () => {
    const user = userEvent.setup();
    const lista: Organizacion[] = [organizacion()];
    const s = servidorFalso({
      "GET /yo": () => ({ ...yo("org-admin"), plataforma_admin: true }),
      "GET /orgs": () => lista,
      "POST /orgs": (l) => {
        lista.push(organizacion({ id: l.cuerpo.id, nombre: l.cuerpo.nombre, region_datos: l.cuerpo.region_datos, version: 1 }));
        return json(201, lista.at(-1));
      },
    });
    montarEnRuta(() => <Organizaciones />, "/organizaciones", "/organizaciones");

    await user.click(await screen.findByRole("button", { name: "Nueva organización" }));
    const dialogo = await screen.findByRole("dialog", { name: "Nueva organización" });
    await user.type(within(dialogo).getByLabelText("Identificador"), "globex");
    await user.type(within(dialogo).getByLabelText("Nombre"), "Globex");
    await user.type(within(dialogo).getByLabelText("Región de datos"), "eu");
    await user.click(within(dialogo).getByRole("button", { name: "Guardar" }));

    expect(await screen.findByText(/^Guardado · versión 1\b/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(s.de("POST", "/orgs")[0]!.cuerpo).toMatchObject({ id: "globex", nombre: "Globex", region_datos: "eu" });
    expect(await screen.findByText("Globex")).toBeInTheDocument();
  });

  it("tras un 409 el formulario se reabre con la versión vigente y el siguiente guardado la envía", async () => {
    const user = userEvent.setup();
    let vigente = organizacion({ version: 2 });
    const s = servidorFalso({
      "GET /yo": () => yo("org-admin"),
      "GET /orgs": () => [vigente],
      "PUT /orgs/acme": (l) => {
        if (l.cuerpo.version !== vigente.version) return json(409, { detalle: "versión desactualizada", version_actual: vigente.version });
        vigente = { ...vigente, nombre: l.cuerpo.nombre, version: vigente.version + 1 };
        return vigente;
      },
    });
    montarEnRuta(() => <Organizaciones />, "/organizaciones", "/organizaciones");

    await user.click(await screen.findByRole("button", { name: "Editar" }));
    await screen.findByRole("dialog", { name: "Editar Acme Corp" });
    // Otra persona guarda mientras el diálogo está abierto.
    vigente = organizacion({ nombre: "Acme (otra persona)", version: 5 });
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Guardar" }));

    await waitFor(() => expect(within(screen.getByRole("dialog")).getByLabelText("Nombre")).toHaveValue("Acme (otra persona)"));
    expect(within(screen.getByRole("dialog")).getByText(/Otra persona modificó este registro/)).toHaveTextContent("(versión actual 5)");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(s.de("PUT", "/orgs/acme").map((l) => l.cuerpo.version)).toEqual([2, 5]);
  });
});
