import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RepositorioGrafo } from "../../api/tipos";
import type { Insumo } from "../../chat/tipos";
import { json, montarEnRuta, servidorFalso, vinculo } from "../../pruebas/servidor";
import { DialogoNuevaUnidad } from "./DialogoNuevaUnidad";

// «Nueva unidad» completa rama y commit con los del vínculo y el grafo, valida antes de enviar y
// con un insumo del chat llega precargada.

const SHA = "a1b2c3d4e5".repeat(4);
const UUID = "0b8f6c1e-2d3a-4b5c-8d7e-9f0a1b2c3d4e";
const RAIZ = "/orgs/acme/workspaces/cert";
const grafos: RepositorioGrafo[] = [{ repositorio: "api", nivel_codigo: "restringido", rol: "primario", commit: SHA }];

const servidor = (extra: Parameters<typeof servidorFalso>[0] = {}) =>
  servidorFalso({
    [`GET ${RAIZ}/repositorios`]: () => [vinculo("api", { rama_por_defecto: "trunk" }), vinculo("lib")],
    [`GET ${RAIZ}/grafo/repositorios`]: () => grafos,
    ...extra,
  });

function abrir(desdeInsumo?: Insumo) {
  const alCerrar = vi.fn();
  montarEnRuta(
    () => <DialogoNuevaUnidad org="acme" ws="cert" desdeInsumo={desdeInsumo} alCerrar={alCerrar} />,
    "/$org/$ws/tablero",
    "/acme/cert/tablero",
  );
  return alCerrar;
}

describe("diálogo de nueva unidad", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("al elegir el repositorio completa la rama del vínculo y el commit del grafo, y arranca la unidad", async () => {
    const user = userEvent.setup();
    const s = servidor({
      [`POST /tools/unit.start`]: () => ({ estado: { unidad: { org: "acme", workspace: "cert", unidad: "0001-emitir-pdf" } } }),
    });
    const alCerrar = abrir();

    const dialogo = await screen.findByRole("dialog", { name: "Nueva unidad" });
    await user.type(await within(dialogo).findByLabelText("Título"), "Emitir PDF");
    await user.type(within(dialogo).getByLabelText("Pedido"), "Firmar los certificados");
    await user.selectOptions(within(dialogo).getByLabelText("Repositorio 1"), "api");
    expect(within(dialogo).getByLabelText("Rama 1")).toHaveValue("trunk");
    expect(within(dialogo).getByLabelText("Commit base 1")).toHaveValue(SHA);
    await user.click(within(dialogo).getByRole("button", { name: "Arrancar unidad" }));

    await waitFor(() => expect(alCerrar).toHaveBeenCalledTimes(1));
    expect(s.de("POST", "/tools/unit.start")[0]!.cuerpo).toMatchObject({
      alcance: { org: "acme", workspace: "cert" },
      titulo: "Emitir PDF",
      pedido: "Firmar los certificados",
      repositorios: [{ repositorio: "api", rama: "trunk", base_commit: SHA }],
    });
  });

  it("con datos incompletos lista los errores y no llama al servidor", async () => {
    const user = userEvent.setup();
    const s = servidor();
    const alCerrar = abrir();

    const dialogo = await screen.findByRole("dialog", { name: "Nueva unidad" });
    await user.click(await within(dialogo).findByRole("button", { name: "Arrancar unidad" }));

    const alerta = await within(dialogo).findByRole("alert");
    expect(alerta).toHaveTextContent("El título es obligatorio.");
    expect(alerta).toHaveTextContent("Elige el repositorio 1.");
    expect(s.de("POST", "/tools/unit.start")).toEqual([]);
    expect(alCerrar).not.toHaveBeenCalled();
  });

  it("añade y quita filas de repositorio; con una sola no ofrece quitar", async () => {
    const user = userEvent.setup();
    servidor();
    abrir();

    const dialogo = await screen.findByRole("dialog", { name: "Nueva unidad" });
    await within(dialogo).findByLabelText("Repositorio 1");
    expect(within(dialogo).queryByRole("button", { name: /Quitar repositorio/ })).toBeNull();
    await user.click(within(dialogo).getByRole("button", { name: "Añadir repositorio" }));
    expect(within(dialogo).getByLabelText("Repositorio 2")).toBeInTheDocument();
    await user.click(within(dialogo).getByRole("button", { name: "Quitar repositorio 2" }));
    expect(within(dialogo).queryByLabelText("Repositorio 2")).toBeNull();
  });

  it("precargada con un insumo trae el pedido, el id y el commit exportado; la rama sale del vínculo", async () => {
    const insumo = {
      id: UUID,
      objetivo: "Firmar certificados",
      restricciones: ["con pruebas"],
      repositorios: [{ repositorio: "api", rol: "primario", base_commit: "c".repeat(40) }],
    } as Insumo;
    servidor();
    abrir(insumo);

    const dialogo = await screen.findByRole("dialog", { name: "Nueva unidad" });
    expect(within(dialogo).getByText(/Precargada con el insumo exportado del chat/)).toBeInTheDocument();
    await waitFor(() => expect(within(dialogo).getByLabelText("Rama 1")).toHaveValue("trunk"));
    expect(within(dialogo).getByLabelText("Título")).toHaveValue("Firmar certificados");
    expect(within(dialogo).getByLabelText("Pedido")).toHaveValue("Firmar certificados\n\nRestricciones:\n- con pruebas");
    expect(within(dialogo).getByLabelText("Insumos del chat (ids)")).toHaveValue(UUID);
    // El commit es el de la exportación, no el del grafo.
    expect(within(dialogo).getByLabelText("Commit base 1")).toHaveValue("c".repeat(40));
  });

  it("un error del servidor al arrancar se muestra y deja el diálogo abierto", async () => {
    const user = userEvent.setup();
    servidor({ [`POST /tools/unit.start`]: () => json(422, { detalle: "El commit base no existe en el clon." }) });
    const alCerrar = abrir();

    const dialogo = await screen.findByRole("dialog", { name: "Nueva unidad" });
    await user.type(await within(dialogo).findByLabelText("Título"), "Emitir PDF");
    await user.type(within(dialogo).getByLabelText("Pedido"), "Firmar");
    await user.selectOptions(within(dialogo).getByLabelText("Repositorio 1"), "api");
    await user.click(within(dialogo).getByRole("button", { name: "Arrancar unidad" }));

    expect(await within(dialogo).findByText(/El commit base no existe en el clon/)).toBeInTheDocument();
    expect(alCerrar).not.toHaveBeenCalled();
  });
});
