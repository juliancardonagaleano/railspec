import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RetenidaGrafo } from "../../api/tipos";
import { json, montarEnRuta, servidorFalso } from "../../pruebas/servidor";
import { estadoRetenida, ordenarRetenidas, Retenidas } from "./Retenidas";

// Retenidas del grafo: el estado de cada una sale de `desde` y `vence` (el plazo total es su diferencia),
// las más urgentes van primero y el error del grafo no se disfraza de lista vacía.

const RUTA = "/orgs/acme/workspaces/cert/grafo/retenidas";
const DIA = 86_400_000;
const AHORA = Date.parse("2026-10-07T12:00:00Z");
const iso = (ms: number) => new Date(ms).toISOString();
const COMMIT = "a1b2c3d4e5f6" + "0".repeat(28);

const retenida = (o: Partial<RetenidaGrafo> = {}): RetenidaGrafo => ({
  repositorio: "api",
  unidad: "0001-firma-pdf",
  integrado: COMMIT,
  desde: iso(AHORA - 2 * DIA),
  vence: iso(AHORA + 28 * DIA),
  ...o,
});

const montar = () => montarEnRuta(() => <Retenidas />, "/$org/$ws/grafo/retenidas", "/acme/cert/grafo/retenidas");

describe("estadoRetenida", () => {
  it("cuenta lo que falta y avisa desde la mitad del plazo, igual que graph.query", () => {
    expect(estadoRetenida(retenida(), AHORA)).toEqual({ clase: "en-plazo", texto: "vence en 28 días" });
    expect(estadoRetenida(retenida({ desde: iso(AHORA - 16 * DIA), vence: iso(AHORA + 14 * DIA) }), AHORA)).toEqual({
      clase: "por-vencer",
      texto: "vence en 14 días",
    });
    expect(estadoRetenida(retenida({ desde: iso(AHORA - 29.9 * DIA), vence: iso(AHORA + 3 * 3_600_000) }), AHORA)).toEqual({
      clase: "por-vencer",
      texto: "vence en 3 horas",
    });
    expect(estadoRetenida(retenida({ vence: iso(AHORA + DIA) }), AHORA).texto).toBe("vence en 1 día");
  });

  it("una vencida, una sin plazo y una sin fecha se distinguen", () => {
    expect(estadoRetenida(retenida({ vence: iso(AHORA - 1) }), AHORA).clase).toBe("vencida");
    expect(estadoRetenida(retenida({ vence: iso(AHORA) }), AHORA).clase).toBe("vencida");
    expect(estadoRetenida(retenida({ vence: null }), AHORA).clase).toBe("sin-plazo");
    expect(estadoRetenida(retenida({ desde: null, vence: null }), AHORA).clase).toBe("sin-dato");
  });
});

describe("ordenarRetenidas", () => {
  it("pone primero las vencidas, luego por vencimiento, y al final las sin plazo", () => {
    const sinPlazo = retenida({ unidad: "0005-a", vence: null });
    const lejana = retenida({ unidad: "0004-b", vence: iso(AHORA + 25 * DIA) });
    const cercana = retenida({ unidad: "0003-c", desde: iso(AHORA - 20 * DIA), vence: iso(AHORA + 10 * DIA) });
    const vencida = retenida({ unidad: "0002-d", vence: iso(AHORA - DIA) });
    const sinDato = retenida({ unidad: "0006-e", desde: null, vence: null });

    const orden = ordenarRetenidas([sinPlazo, lejana, sinDato, cercana, vencida], AHORA).map((r) => r.unidad);

    expect(orden).toEqual(["0002-d", "0003-c", "0004-b", "0006-e", "0005-a"]);
  });
});

describe("pantalla de retenidas", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lista unidad, repositorio, commit corto y estado, con las vencidas primero", async () => {
    const ahora = Date.now();
    servidorFalso({
      [`GET ${RUTA}`]: () => [
        retenida({ unidad: "0001-reciente", desde: iso(ahora - DIA), vence: iso(ahora + 29 * DIA) }),
        retenida({ unidad: "0002-varada", desde: iso(ahora - 31 * DIA), vence: iso(ahora - DIA) }),
      ],
    });
    montar();

    const [varada, reciente] = (await screen.findAllByRole("row")).slice(1);
    expect(within(varada!).getByText("0002-varada")).toBeInTheDocument();
    expect(within(varada!).getByText("vencida: la retira el próximo barrido")).toBeInTheDocument();
    expect(within(reciente!).getByText("0001-reciente")).toBeInTheDocument();
    expect(within(reciente!).getByText("a1b2c3d")).toBeInTheDocument();
    expect(within(reciente!).getByText("api")).toBeInTheDocument();
    expect(within(reciente!).getByText(/^vence en (28|29) días$/)).toBeInTheDocument();
    expect(screen.getByText(/2 retenidas, 1 vencida/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Repositorio")).toBeNull(); // con un solo repositorio no hay filtro
  });

  it("filtra por repositorio cuando hay más de uno", async () => {
    const ahora = Date.now();
    const v = (o: Partial<RetenidaGrafo>) => retenida({ desde: iso(ahora), vence: iso(ahora + 30 * DIA), ...o });
    servidorFalso({
      [`GET ${RUTA}`]: () => [v({ repositorio: "api", unidad: "0001-a" }), v({ repositorio: "web", unidad: "0002-b" })],
    });
    montar();

    await screen.findByText("0001-a");
    await userEvent.selectOptions(screen.getByLabelText("Repositorio"), "web");

    expect(screen.queryByText("0001-a")).toBeNull();
    expect(screen.getByText("0002-b")).toBeInTheDocument();
    expect(screen.getByText(/1 retenida\./)).toBeInTheDocument();
  });

  it("sin retenidas dice que no hay, no una tabla vacía", async () => {
    servidorFalso({ [`GET ${RUTA}`]: () => [] });
    montar();

    expect(await screen.findByText("No hay superposiciones retenidas")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("con el grafo caído muestra el error y no un vacío que engañe", async () => {
    servidorFalso({ [`GET ${RUTA}`]: () => json(503, { detalle: "el grafo no respondió (ConnectionError)" }) });
    montar();

    const alerta = await screen.findByRole("alert");
    expect(within(alerta).getByText(/el grafo no respondió/)).toBeInTheDocument();
    expect(screen.queryByText("No hay superposiciones retenidas")).toBeNull();
  });
});
