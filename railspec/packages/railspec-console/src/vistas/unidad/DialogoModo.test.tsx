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

  it("sin mandato (plan) deshabilita supervisado y desatendido y explica por qué", async () => {
    abrir();
    const supervisado = await screen.findByRole("option", { name: "supervisado (requiere un mandato)" });
    expect(supervisado).toBeDisabled();
    expect(screen.getByRole("option", { name: "desatendido (requiere un mandato)" })).toBeDisabled();
    expect(screen.getByRole("option", { name: "semi-autonomo" })).toBeEnabled();
    expect(screen.getByText(/no tiene mandato/)).toBeInTheDocument();
  });

  it("con mandato avisa que exige uno aprobado, vigente y del mismo modo, y deja enviarlo", async () => {
    const conPlan: EstadoUnidad = { ...estado, unidad: { ...estado.unidad, plan: "pdf-a" } };
    const s = servidorFalso({ "POST /tools/unit.set_mode": () => ({ estado: conPlan }) });
    const alCerrar = vi.fn();
    montarConQuery(<DialogoModo estado={conPlan} abierto alCerrar={alCerrar} />);

    expect(screen.queryByText(/Si no existe o no está vigente/)).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText("Modo"), "desatendido");
    expect(screen.getByText(/exige que el mandato/)).toHaveTextContent("pdf-a");
    expect(screen.getByText(/Si no existe o no está vigente, el servidor rechazará el cambio/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Motivo"), "limpieza masiva");
    await userEvent.click(cambiar());

    await waitFor(() => expect(alCerrar).toHaveBeenCalledTimes(1));
    expect(enviados(s)).toEqual([{ unidad: conPlan.unidad, modo: "desatendido", motivo: "limpieza masiva", version_vista: 7 }]);
  });

  it("si el servidor rechaza el cambio por el mandato, muestra el motivo", async () => {
    const conPlan: EstadoUnidad = { ...estado, unidad: { ...estado.unidad, plan: "pdf-a" } };
    servidorFalso({ "POST /tools/unit.set_mode": () => json(422, { codigo: "mandato-no-vigente", detalle: "el mandato pdf-a no está aprobado" }) });
    const alCerrar = vi.fn();
    montarConQuery(<DialogoModo estado={conPlan} abierto alCerrar={alCerrar} />);

    await userEvent.selectOptions(screen.getByLabelText("Modo"), "supervisado");
    await userEvent.type(screen.getByLabelText("Motivo"), "probar");
    await userEvent.click(cambiar());
    expect(await screen.findByText(/el mandato pdf-a no está aprobado/)).toBeInTheDocument();
    expect(alCerrar).not.toHaveBeenCalled();
  });
});
