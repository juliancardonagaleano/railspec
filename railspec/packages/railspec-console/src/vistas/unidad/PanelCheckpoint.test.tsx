import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Checkpoint } from "../../api/tipos";
import { json, montarConQuery, servidorFalso } from "../../pruebas/servidor";
import { exigeComentario, PanelCheckpoint } from "./PanelCheckpoint";

// Resolver un checkpoint desde la consola es opcional y gana el primer canal: aprobar no exige
// comentario, pedir cambios o rechazar sí, y un «ya resuelto» no se presenta como fallo.

const alcance = { org: "acme", workspace: "cert", unidad: "0001-emitir-pdf" };
const checkpoint: Checkpoint = {
  id: "cp-1",
  tipo: "aprobar-spec",
  fase: "spec",
  pregunta: "¿La spec cubre la firma digital?",
  abierto_en: "2026-09-30T10:00:00Z",
};
const aprobar = () => screen.getByRole("button", { name: "Aprobar" });
const pedirCambios = () => screen.getByRole("button", { name: "Pedir cambios" });
const rechazar = () => screen.getByRole("button", { name: "Rechazar" });
const enviados = (s: ReturnType<typeof servidorFalso>) => s.de("POST", "/tools/unit.approve").map((l) => l.cuerpo);

describe("panel de checkpoint", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("solo aprobar puede ir sin comentario", () => {
    expect(exigeComentario("aprobado")).toBe(false);
    expect(exigeComentario("cambios-solicitados")).toBe(true);
    expect(exigeComentario("rechazado")).toBe(true);
  });

  it("aprueba sin comentario y no manda el campo", async () => {
    const s = servidorFalso({ "POST /tools/unit.approve": () => ({ estado: {} }) });
    montarConQuery(<PanelCheckpoint alcance={alcance} checkpoint={checkpoint} puedeResolver />);

    expect(screen.getByText("Checkpoint pendiente: Aprobar spec")).toBeInTheDocument();
    expect(screen.getByText("¿La spec cubre la firma digital?")).toBeInTheDocument();
    await userEvent.click(aprobar());
    await waitFor(() => expect(enviados(s)).toHaveLength(1));
    expect(enviados(s)[0]).toEqual({ unidad: alcance, checkpoint: "cp-1", decision: "aprobado" });
  });

  it("pedir cambios y rechazar quedan deshabilitados sin comentario y lo envían recortado con él", async () => {
    const s = servidorFalso({ "POST /tools/unit.approve": () => ({ estado: {} }) });
    montarConQuery(<PanelCheckpoint alcance={alcance} checkpoint={checkpoint} puedeResolver />);

    expect(pedirCambios()).toBeDisabled();
    expect(rechazar()).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Comentario"), "   ");
    expect(pedirCambios()).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Comentario"), "  falta el caso de error  ");
    expect(pedirCambios()).toBeEnabled();
    await userEvent.click(pedirCambios());

    await waitFor(() => expect(enviados(s)).toHaveLength(1));
    expect(enviados(s)[0]).toEqual({
      unidad: alcance,
      checkpoint: "cp-1",
      decision: "cambios-solicitados",
      comentario: "falta el caso de error",
    });
    // Tras resolver se vacía el comentario.
    await waitFor(() => expect(screen.getByLabelText("Comentario")).toHaveValue(""));
  });

  it("«checkpoint-ya-resuelto» avisa que ganó otro canal y no lo muestra como error", async () => {
    servidorFalso({
      "POST /tools/unit.approve": () => json(409, { detalle: "El checkpoint ya está resuelto.", codigo: "checkpoint-ya-resuelto" }),
    });
    montarConQuery(<PanelCheckpoint alcance={alcance} checkpoint={checkpoint} puedeResolver />);

    await userEvent.click(aprobar());
    expect(await screen.findByText(/ya lo resolvió otro canal/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("otro error del servidor se muestra tal cual", async () => {
    servidorFalso({ "POST /tools/unit.approve": () => json(422, { detalle: "Decisión no válida para esta fase." }) });
    montarConQuery(<PanelCheckpoint alcance={alcance} checkpoint={checkpoint} puedeResolver />);

    await userEvent.click(aprobar());
    expect(await screen.findByRole("alert")).toHaveTextContent("Decisión no válida para esta fase.");
    expect(screen.queryByText(/ya lo resolvió otro canal/)).toBeNull();
  });

  it("con un rol de solo lectura no ofrece ninguna decisión", () => {
    montarConQuery(<PanelCheckpoint alcance={alcance} checkpoint={checkpoint} puedeResolver={false} />);

    expect(screen.getByText("Tu rol solo permite consultar este checkpoint.")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByLabelText("Comentario")).toBeNull();
  });
});
