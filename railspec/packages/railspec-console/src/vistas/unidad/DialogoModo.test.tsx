import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Actor, EstadoUnidad } from "../../api/tipos";
import { json, montarConQuery, servidorFalso } from "../../pruebas/servidor";
import { DialogoModo } from "./DialogoModo";

// Cambiar el modo exige un modo distinto y un motivo, viaja con la versión vista y ante un 409
// avisa que el estado cambió.

const julian: Actor = { tipo: "humano", canal: "consola", github_id: 1, login: "julian" };
const estado: EstadoUnidad = {
  unidad: { org: "acme", workspace: "cert", unidad: "0001-emitir-pdf" },
  version: 7,
  titulo: "Emitir PDF firmado",
  dueno: julian,
  repositorios: [],
  fase: "plan",
  estado: "en-progreso",
  modo: "interactivo",
  riesgo: "medio",
  perfil: "estandar",
  creado_en: "2026-09-30T10:00:00Z",
  actualizado_en: "2026-09-30T11:00:00Z",
  actualizado_por: julian,
};
const cambiar = () => screen.getByRole("button", { name: "Cambiar modo" });
const enviados = (s: ReturnType<typeof servidorFalso>) => s.de("POST", "/tools/unit.set_mode").map((l) => l.cuerpo);

function abrir() {
  const alCerrar = vi.fn();
  montarConQuery(<DialogoModo estado={estado} abierto alCerrar={alCerrar} />);
  return alCerrar;
}

describe("diálogo de cambio de modo", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("marca el modo actual y no deja enviar sin motivo ni sin cambiar de modo", async () => {
    abrir();
    expect(await screen.findByRole("option", { name: "interactivo (actual)" })).toBeInTheDocument();
    expect(cambiar()).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Motivo"), "porque sí");
    // Mismo modo que el actual: sigue deshabilitado.
    expect(cambiar()).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("Modo"), "semi-autonomo");
    expect(cambiar()).toBeEnabled();
    await userEvent.clear(screen.getByLabelText("Motivo"));
    await userEvent.type(screen.getByLabelText("Motivo"), "   ");
    expect(cambiar()).toBeDisabled();
  });

  it("envía el modo nuevo con el motivo recortado y la versión vista, y cierra", async () => {
    const s = servidorFalso({ "POST /tools/unit.set_mode": () => ({ estado }) });
    const alCerrar = abrir();

    await userEvent.selectOptions(screen.getByLabelText("Modo"), "semi-autonomo");
    await userEvent.type(screen.getByLabelText("Motivo"), "  el riesgo es bajo  ");
    await userEvent.click(cambiar());

    await waitFor(() => expect(alCerrar).toHaveBeenCalledTimes(1));
    expect(enviados(s)).toEqual([{ unidad: estado.unidad, modo: "semi-autonomo", motivo: "el riesgo es bajo", version_vista: 7 }]);
  });

  it("un 409 avisa que el estado cambió y no cierra el diálogo", async () => {
    servidorFalso({ "POST /tools/unit.set_mode": () => json(409, { detalle: "versión desactualizada", version_estado: 8 }) });
    const alCerrar = abrir();

    await userEvent.selectOptions(screen.getByLabelText("Modo"), "semi-autonomo");
    await userEvent.type(screen.getByLabelText("Motivo"), "otro intento");
    await userEvent.click(cambiar());

    expect(await screen.findByText(/El estado cambió mientras editabas; ya se recargó/)).toBeInTheDocument();
    expect(screen.getByText(/versión desactualizada/)).toBeInTheDocument();
    expect(alCerrar).not.toHaveBeenCalled();
    expect(cambiar()).toBeEnabled();
  });
});
