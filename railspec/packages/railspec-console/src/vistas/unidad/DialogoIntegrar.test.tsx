import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CommitIntegrable } from "../../api/tipos";
import { json, montarConQuery, servidorFalso } from "../../pruebas/servidor";
import { DialogoIntegrar } from "./DialogoIntegrar";

const alcance = { org: "acme", workspace: "cert", unidad: "0001-emitir-pdf" };
const COMMIT = "a1b2c3d4e5".repeat(4);
const PUNTA = "f".repeat(40);
const RUTA_SUGERENCIA = "/orgs/acme/workspaces/cert/unidades/0001-emitir-pdf/commit-integrable";

const CON_CLON: CommitIntegrable = {
  repositorio: "certificados-api",
  rama: "main",
  commit: PUNTA,
  commit_indexado: null,
  motivo: null,
};
const SIN_CLON: CommitIntegrable = { ...CON_CLON, commit: null, motivo: "sin-clones" };

const servidor = (
  respuesta: () => Response | unknown = () => ({ estado: { unidad: alcance } }),
  sugerencia: () => Response | unknown = () => SIN_CLON,
) => servidorFalso({ "POST /tools/unit.integrate": respuesta, [`GET ${RUTA_SUGERENCIA}`]: sugerencia });

function abrir() {
  const alCerrar = vi.fn();
  montarConQuery(<DialogoIntegrar alcance={alcance} abierto alCerrar={alCerrar} />);
  return alCerrar;
}

const campo = (nombre: string | RegExp) => screen.getByLabelText(nombre);
const integrar = () => screen.getByRole("button", { name: "Integrar" });
const sinGrafo = () => screen.getByRole("checkbox", { name: /Integrar sin conservar el grafo/ });
const enviados = (s: ReturnType<typeof servidor>) => s.de("POST", "/tools/unit.integrate").map((l) => l.cuerpo);
/** Espera a que llegue la sugerencia del servidor (el campo del commit la muestra o la ayuda cambia). */
const sugerenciaLista = () => waitFor(() => expect(screen.queryByText(/Consultando la punta/)).toBeNull());

describe("diálogo de integrar", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("pide la especificación viva y no envía nada sin ella", async () => {
    const s = servidor(undefined, () => CON_CLON);
    abrir();
    expect(await screen.findByRole("dialog", { name: "Integrar unidad" })).toBeInTheDocument();
    await sugerenciaLista();
    expect(integrar()).toBeDisabled();
    await userEvent.type(campo("Especificación viva"), "specs/pdf.md");
    expect(integrar()).toBeEnabled();
    expect(enviados(s)).toEqual([]);
  });

  it("prellena el commit con la punta de la rama por defecto del servidor y lo manda sin tocarlo", async () => {
    const s = servidor(undefined, () => CON_CLON);
    const alCerrar = abrir();
    await waitFor(() => expect(campo(/Commit integrado/)).toHaveValue(PUNTA));
    expect(screen.getByText(/Sugerido: la punta de main en el clon del servidor/)).toBeInTheDocument();
    // Con commit no hace falta (ni se ofrece) renunciar al grafo.
    expect(screen.queryByRole("checkbox")).toBeNull();
    await userEvent.type(campo("Especificación viva"), "  specs/pdf.md  ");
    await userEvent.click(integrar());
    await waitFor(() => expect(alCerrar).toHaveBeenCalledTimes(1));
    expect(enviados(s)).toEqual([{ unidad: alcance, especificacion_viva: "specs/pdf.md", commit_integrado: PUNTA }]);
  });

  it("avisa cuando el índice del grafo ya está en el commit sugerido", async () => {
    servidor(undefined, () => ({ ...CON_CLON, commit_indexado: PUNTA }));
    abrir();
    expect(await screen.findByText(/El índice del grafo ya está en este commit/)).toBeInTheDocument();
  });

  it("sin sugerencia del servidor obliga a pegar el commit o a renunciar al grafo a propósito", async () => {
    const s = servidor();
    const alCerrar = abrir();
    expect(await screen.findByText(/El servidor no tiene clon de este repositorio/)).toBeInTheDocument();
    await userEvent.type(campo("Especificación viva"), "specs/pdf.md");
    // Sin commit y sin renunciar: el envío está bloqueado (omitirlo en silencio descartaría el grafo).
    expect(integrar()).toBeDisabled();
    expect(sinGrafo()).not.toBeChecked();
    await userEvent.click(sinGrafo());
    expect(integrar()).toBeEnabled();
    await userEvent.click(integrar());
    await waitFor(() => expect(alCerrar).toHaveBeenCalledTimes(1));
    // Renunciar no manda commit: el servidor descarta la superposición al integrar.
    expect(enviados(s)).toEqual([{ unidad: alcance, especificacion_viva: "specs/pdf.md" }]);
  });

  it("el commit que se escribe reemplaza la sugerencia, va en minúsculas y viaja con el PR", async () => {
    const s = servidor(undefined, () => CON_CLON);
    const alCerrar = abrir();
    await waitFor(() => expect(campo(/Commit integrado/)).toHaveValue(PUNTA));
    await userEvent.type(campo("Especificación viva"), "specs/pdf.md");
    await userEvent.type(campo(/URL del PR/), "https://github.com/acme/api/pull/9");
    await userEvent.clear(campo(/Commit integrado/));
    await userEvent.type(campo(/Commit integrado/), COMMIT.toUpperCase());
    await userEvent.click(integrar());
    await waitFor(() => expect(alCerrar).toHaveBeenCalledTimes(1));
    expect(enviados(s)).toEqual([
      { unidad: alcance, especificacion_viva: "specs/pdf.md", pr_url: "https://github.com/acme/api/pull/9", commit_integrado: COMMIT },
    ]);
  });

  it("borrar la sugerencia vuelve a exigir elegir: otro commit o renunciar al grafo", async () => {
    const s = servidor(undefined, () => CON_CLON);
    const alCerrar = abrir();
    await waitFor(() => expect(campo(/Commit integrado/)).toHaveValue(PUNTA));
    await userEvent.type(campo("Especificación viva"), "specs/pdf.md");
    await userEvent.clear(campo(/Commit integrado/));
    // La sugerencia no reaparece sola ni se envía: el campo vacío es una decisión de la persona.
    expect(campo(/Commit integrado/)).toHaveValue("");
    expect(integrar()).toBeDisabled();
    await userEvent.click(sinGrafo());
    await userEvent.click(integrar());
    await waitFor(() => expect(alCerrar).toHaveBeenCalledTimes(1));
    expect(enviados(s)).toEqual([{ unidad: alcance, especificacion_viva: "specs/pdf.md" }]);
  });

  it("si falla la consulta de la sugerencia lo dice y deja pegar el commit", async () => {
    const s = servidor(undefined, () => json(500, { detalle: "grafo caído" }));
    const alCerrar = abrir();
    expect(await screen.findByText(/No se pudo consultar la sugerencia del servidor/)).toBeInTheDocument();
    await userEvent.type(campo("Especificación viva"), "specs/pdf.md");
    await userEvent.type(campo(/Commit integrado/), COMMIT);
    await userEvent.click(integrar());
    await waitFor(() => expect(alCerrar).toHaveBeenCalledTimes(1));
    expect(enviados(s)).toEqual([{ unidad: alcance, especificacion_viva: "specs/pdf.md", commit_integrado: COMMIT }]);
  });

  it("un commit que no es un sha completo bloquea el envío y lo explica", async () => {
    const s = servidor();
    abrir();
    await sugerenciaLista();
    await userEvent.type(campo("Especificación viva"), "specs/pdf.md");
    await userEvent.type(campo(/Commit integrado/), "abc123");
    expect(screen.getByText(/El commit debe ser un sha completo/)).toBeInTheDocument();
    expect(integrar()).toBeDisabled();
    await userEvent.clear(campo(/Commit integrado/));
    await userEvent.click(sinGrafo());
    await waitFor(() => expect(integrar()).toBeEnabled());
    expect(screen.queryByText(/El commit debe ser un sha completo/)).toBeNull();
    expect(enviados(s)).toEqual([]);
  });

  it("una URL de PR que no es https bloquea el envío", async () => {
    servidor(undefined, () => CON_CLON);
    abrir();
    await waitFor(() => expect(campo(/Commit integrado/)).toHaveValue(PUNTA));
    await userEvent.type(campo("Especificación viva"), "specs/pdf.md");
    await userEvent.type(campo(/URL del PR/), "http://github.com/acme/api/pull/9");
    expect(screen.getByText("La URL debe empezar por https://")).toBeInTheDocument();
    expect(integrar()).toBeDisabled();
  });

  it("si el servidor rechaza la integración muestra el error y deja el diálogo abierto", async () => {
    servidor(() => json(409, { codigo: "estado-invalido", detalle: "la unidad no está cerrada" }), () => CON_CLON);
    const alCerrar = abrir();
    await waitFor(() => expect(campo(/Commit integrado/)).toHaveValue(PUNTA));
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
