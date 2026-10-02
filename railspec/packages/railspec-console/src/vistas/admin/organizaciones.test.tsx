import { screen, within } from "@testing-library/react";
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
});
