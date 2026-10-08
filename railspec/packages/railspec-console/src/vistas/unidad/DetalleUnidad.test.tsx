import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EstadoUnidad } from "../../api/tipos";
import { decision, estadoUnidad } from "../../pruebas/mandatos";
import { montarEnRuta, servidorFalso, yo } from "../../pruebas/servidor";
import { DetalleUnidad, estaDiferida } from "./DetalleUnidad";

// Detalle de la unidad con mandato (contrato 1.11): la insignia con enlace al mandato, «Diferida
// (desatendido)», la causa de parada del checkpoint y las decisiones delegadas con su revisión.

// El DAG de fases usa xyflow, que necesita ResizeObserver (jsdom no lo trae) y aquí no se prueba.
vi.mock("./DagFases", () => ({ DagFases: () => null }));

const RAIZ = "/orgs/acme/workspaces/cert";
const URL = "/acme/cert/unidades/0001-emitir-pdfa";
const gateDiferido = {
  veredicto: "escalado" as const,
  causa: "sin-convergencia",
  iteraciones: 2,
  gobernanza_consultada: "si" as const,
  cerrado_en: "2026-09-30T12:00:00Z",
  diferido: true,
};

function servidor(estado: EstadoUnidad, rol: "lector" | "desarrollador" = "desarrollador", extra: Parameters<typeof servidorFalso>[0] = {}) {
  return servidorFalso({
    "GET /yo": () => yo(null, rol),
    [`GET ${RAIZ}/unidades/0001-emitir-pdfa`]: () => ({ estado, orden_vigente: null }),
    ...extra,
  });
}
const montar = () => montarEnRuta(() => <DetalleUnidad />, "/$org/$ws/unidades/$unidad", URL);

describe("estaDiferida", () => {
  it("solo cuenta un gate diferido que nadie ha rehabilitado", () => {
    expect(estaDiferida(estadoUnidad())).toBe(false);
    expect(estaDiferida(estadoUnidad({ gates: { plan: gateDiferido } }))).toBe(true);
    const rehabilitado = { ...gateDiferido, rehabilitado: { actor: { tipo: "humano" as const, canal: "consola" as const, login: "ana" }, en: "2026-09-30T13:00:00Z", motivo: "ok" } };
    expect(estaDiferida(estadoUnidad({ gates: { plan: rehabilitado } }))).toBe(false);
  });
});

describe("detalle de la unidad con mandato", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("muestra la insignia del mandato con enlace y «Diferida (desatendido)» si el gate está diferido", async () => {
    servidor(estadoUnidad({ gates: { plan: gateDiferido } }));
    montar();

    const insignia = await screen.findByRole("link", { name: "mandato pdf-a" });
    expect(insignia).toHaveAttribute("href", "/acme/cert/mandatos/pdf-a");
    expect(screen.getByText("Diferida (desatendido)")).toBeInTheDocument();
  });

  it("una unidad sin mandato ni gate diferido no muestra ni insignia ni aviso", async () => {
    servidor(estadoUnidad({ unidad: { org: "acme", workspace: "cert", unidad: "0001-emitir-pdfa" }, modo: "interactivo" }));
    montar();

    await screen.findByRole("heading", { name: "Emitir PDF/A" });
    expect(screen.queryByRole("link", { name: /^mandato / })).toBeNull();
    expect(screen.queryByText("Diferida (desatendido)")).toBeNull();
    expect(screen.queryByText(/Decisiones delegadas/)).toBeNull();
  });

  it("el checkpoint pendiente con causa de parada la explica", async () => {
    servidor(
      estadoUnidad({
        checkpoint_pendiente: {
          id: "cp-1",
          tipo: "parada",
          fase: "implement",
          pregunta: "La orden falló y no quedan reintentos.",
          abierto_en: "2026-09-30T12:00:00Z",
          causa_parada: "reintentos-agotados",
        },
      }),
    );
    montar();

    expect(await screen.findByText("Parada bajo mandato:")).toBeInTheDocument();
    expect(screen.getByText("reintentos-agotados")).toBeInTheDocument();
    expect(screen.getByText(/no quedan reintentos delegados/)).toBeInTheDocument();
  });

  it("lista las decisiones con su estado de revisión y deja revertir con comentario, con la versión de la unidad", async () => {
    const s = servidor(
      estadoUnidad({
        decisiones: [
          decision("DD-1"),
          decision("DD-2", {
            delegacion: "reintento",
            revision: { resultado: "aceptada", actor: { tipo: "humano", canal: "consola", login: "ana" }, en: "2026-10-01T09:00:00Z" },
          }),
        ],
      }),
      "desarrollador",
      { "POST /tools/mandate.review": () => ({ estado: estadoUnidad() }) },
    );
    const user = userEvent.setup();
    montar();

    expect(await screen.findByText("Decisiones delegadas (2)")).toBeInTheDocument();
    expect(screen.getByText("pendiente de revisión")).toBeInTheDocument();
    expect(screen.getByText("aceptada")).toBeInTheDocument();
    expect(screen.getByText("reintento (automático)")).toBeInTheDocument();
    // Solo la pendiente tiene acciones.
    expect(screen.queryByRole("button", { name: "Aceptar DD-2" })).toBeNull();
    const revertir = screen.getByRole("button", { name: "Revertir DD-1" });
    expect(revertir).toBeDisabled();
    await user.type(screen.getByLabelText("Comentario de DD-1"), "no cumple el criterio");
    await user.click(revertir);

    await waitFor(() => expect(s.de("POST", "/tools/mandate.review")).toHaveLength(1));
    expect(s.de("POST", "/tools/mandate.review")[0]!.cuerpo).toEqual({
      unidad: { org: "acme", workspace: "cert", unidad: "0001-emitir-pdfa" },
      decision: "DD-1",
      resultado: "revertida",
      comentario: "no cumple el criterio",
      version_vista: 7,
    });
  });

  it("un lector ve las decisiones pero no puede revisarlas", async () => {
    servidor(estadoUnidad({ decisiones: [decision("DD-1")] }), "lector");
    montar();

    const tarjeta = (await screen.findByText("Decisiones delegadas (1)")).closest("div")!.parentElement as HTMLElement;
    expect(within(tarjeta).getByText("pendiente de revisión")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Aceptar DD-1" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Revertir DD-1" })).toBeNull();
  });
});
