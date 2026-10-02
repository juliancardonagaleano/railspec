import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { json, montarConQuery, servidorFalso } from "../../pruebas/servidor";
import { DialogoIntegrar } from "./DialogoIntegrar";

const alcance = { org: "acme", workspace: "cert", unidad: "0001-emitir-pdf" };
const COMMIT = "a1b2c3d4e5".repeat(4);

const servidor = (respuesta: () => Response | unknown = () => ({ estado: { unidad: alcance } })) =>
  servidorFalso({ "POST /tools/unit.integrate": respuesta });

function abrir() {
  const alCerrar = vi.fn();
  montarConQuery(<DialogoIntegrar alcance={alcance} abierto alCerrar={alCerrar} />);
  return alCerrar;
}

const campo = (nombre: string | RegExp) => screen.getByLabelText(nombre);
const integrar = () => screen.getByRole("button", { name: "Integrar" });
const enviados = (s: ReturnType<typeof servidor>) => s.de("POST", "/tools/unit.integrate").map((l) => l.cuerpo);

describe("diálogo de integrar", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("pide la especificación viva y no envía nada sin ella", async () => {
    const s = servidor();
    abrir();
    expect(await screen.findByRole("dialog", { name: "Integrar unidad" })).toBeInTheDocument();
    expect(integrar()).toBeDisabled();
    await userEvent.type(campo("Especificación viva"), "specs/pdf.md");
    expect(integrar()).toBeEnabled();
    expect(enviados(s)).toEqual([]);
  });

  it("solo con la especificación viva manda lo mínimo y cierra", async () => {
    const s = servidor();
    const alCerrar = abrir();
    await userEvent.type(campo("Especificación viva"), "  specs/pdf.md  ");
    await userEvent.click(integrar());
    await waitFor(() => expect(alCerrar).toHaveBeenCalledTimes(1));
    // Sin PR ni commit no viajan esos campos: el servidor descarta la superposición al integrar.
    expect(enviados(s)).toEqual([{ unidad: alcance, especificacion_viva: "specs/pdf.md" }]);
  });

  it("manda también el PR y el commit integrado (en minúsculas) cuando se dan", async () => {
    const s = servidor();
    const alCerrar = abrir();
    await userEvent.type(campo("Especificación viva"), "specs/pdf.md");
    await userEvent.type(campo(/URL del PR/), "https://github.com/acme/api/pull/9");
    await userEvent.type(campo(/Commit integrado/), COMMIT.toUpperCase());
    await userEvent.click(integrar());
    await waitFor(() => expect(alCerrar).toHaveBeenCalledTimes(1));
    expect(enviados(s)).toEqual([
      { unidad: alcance, especificacion_viva: "specs/pdf.md", pr_url: "https://github.com/acme/api/pull/9", commit_integrado: COMMIT },
    ]);
  });

  it("un commit que no es un sha completo bloquea el envío y lo explica", async () => {
    const s = servidor();
    abrir();
    await userEvent.type(campo("Especificación viva"), "specs/pdf.md");
    await userEvent.type(campo(/Commit integrado/), "abc123");
    expect(screen.getByText(/El commit debe ser un sha completo/)).toBeInTheDocument();
    expect(integrar()).toBeDisabled();
    await userEvent.clear(campo(/Commit integrado/));
    await waitFor(() => expect(integrar()).toBeEnabled());
    expect(screen.queryByText(/El commit debe ser un sha completo/)).toBeNull();
    expect(enviados(s)).toEqual([]);
  });

  it("una URL de PR que no es https bloquea el envío", async () => {
    servidor();
    abrir();
    await userEvent.type(campo("Especificación viva"), "specs/pdf.md");
    await userEvent.type(campo(/URL del PR/), "http://github.com/acme/api/pull/9");
    expect(screen.getByText("La URL debe empezar por https://")).toBeInTheDocument();
    expect(integrar()).toBeDisabled();
  });

  it("si el servidor rechaza la integración muestra el error y deja el diálogo abierto", async () => {
    servidor(() => json(409, { codigo: "estado-invalido", detalle: "la unidad no está cerrada" }));
    const alCerrar = abrir();
    await userEvent.type(campo("Especificación viva"), "specs/pdf.md");
    await userEvent.click(integrar());
    expect(await screen.findByText(/la unidad no está cerrada/)).toBeInTheDocument();
    expect(alCerrar).not.toHaveBeenCalled();
    expect(integrar()).toBeEnabled();
  });

  it("Cancelar cierra sin enviar", async () => {
    const s = servidor();
    const alCerrar = abrir();
    await userEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(alCerrar).toHaveBeenCalledTimes(1);
    expect(enviados(s)).toEqual([]);
  });
});
