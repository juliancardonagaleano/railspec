import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FilaTelemetria, ResumenWorkspace } from "../../api/tipos";
import { json, montarEnRuta, servidorFalso, type Llamada } from "../../pruebas/servidor";
import { DIMENSIONES, Estadisticas, METRICAS } from "./Estadisticas";

// ECharts necesita canvas (jsdom no lo tiene): se sustituye por un nodo que expone la opción recibida.
vi.mock("../../componentes/Grafico", () => ({
  Grafico: ({ opcion, etiqueta }: { opcion: { series: { name: string; data: number[] }[]; xAxis: { data: string[] } }; etiqueta: string }) => (
    <div role="img" aria-label={etiqueta} data-series={JSON.stringify(opcion.series.map((s) => s.name))} data-datos={JSON.stringify(opcion.series[0]?.data)} data-x={JSON.stringify(opcion.xAxis.data)} />
  ),
}));

const resumen: ResumenWorkspace = {
  unidades: { total: 3, por_fase: { spec: 2, done: 1 }, por_estado: { "en-progreso": 2 }, integradas: 1, checkpoints_pendientes: 0 },
  gates: {},
  gasto: { mes_usd: 10, presupuesto_mensual_usd: 100, desde: "2026-10-01T00:00:00Z" },
  grafo: [],
};

const fila = (claves: FilaTelemetria["claves"], o: Partial<FilaTelemetria> = {}): FilaTelemetria => ({
  claves,
  llamadas: 4,
  tokens_entrada: 100,
  tokens_salida: 50,
  tokens_cache_lectura: 300,
  costo_usd: 2,
  duracion_ms: 4000,
  ...o,
});

/** Responde cada `telemetry.query` con filas según la clave por la que se agrupa. */
function servidor(filasPor: Record<string, FilaTelemetria[] | Response>) {
  return servidorFalso({
    "GET /orgs/acme/workspaces/cert/resumen": () => resumen,
    "POST /tools/telemetry.query": (l: Llamada) => {
      const r = filasPor[l.cuerpo.agrupar_por[0]];
      if (r instanceof Response) return r;
      return { filas: r ?? [] };
    },
  });
}

/** Celdas de la fila del desglose (8 columnas) que lleva `clave`; la tabla de gates también tiene filas «spec» y «plan». */
const celdasDe = (clave: string): string[] => {
  const fila = screen.getAllByRole("row").find((r) => within(r).queryByText(clave) && within(r).queryAllByRole("cell").length === 8);
  if (!fila) throw new Error(`sin fila ${clave} en el desglose`);
  return within(fila).getAllByRole("cell").map((c) => c.textContent ?? "");
};

const montar = () => montarEnRuta(() => <Estadisticas />, "/$org/$ws/estadisticas", "/acme/cert/estadisticas");
const consultas = (s: ReturnType<typeof servidor>) => s.de("POST", "/tools/telemetry.query").map((l) => l.cuerpo);

describe("estadísticas", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("ofrece desglosar por nodo, repositorio, workspace, tier, duración y caché", () => {
    const claves = DIMENSIONES.map((d) => d.clave);
    for (const c of ["fase", "nodo", "modelo", "repositorio", "workspace", "tier"]) expect(claves).toContain(c);
    expect(METRICAS.map((m) => m.valor)).toEqual(["costo", "duracion", "cache"]);
  });

  it("por defecto desglosa por fase del workspace y muestra llamadas, caché, duración media y costo", async () => {
    const s = servidor({
      fase: [fila({ fase: "spec" }, { costo_usd: 3 }), fila({ fase: "plan" }, { llamadas: 2, duracion_ms: 6000, tokens_cache_lectura: 0, costo_usd: 1 })],
      unidad: [fila({ unidad: "0001-a" })],
    });
    montar();

    await waitFor(() => expect(() => celdasDe("spec")).not.toThrow());
    // spec: 4 llamadas, 100 entrada, 50 salida, 300 de caché = 75 % de la entrada total, 1,0 s de media.
    expect(celdasDe("spec")).toEqual(expect.arrayContaining(["4", "100", "50", "300", "75 %", "1,0 s"]));
    // plan no leyó caché y tardó 3 s de media.
    expect(celdasDe("plan")).toEqual(expect.arrayContaining(["2", "0 %", "3,0 s"]));
    // Fila de totales del desglose.
    expect(celdasDe("Total")).toEqual(expect.arrayContaining(["6", "200"]));
    expect(consultas(s)[0]).toMatchObject({ org: "acme", workspace: "cert", agrupar_por: ["fase"] });
  });

  it("cambiar la dimensión pide esa agrupación y la tabla usa su nombre", async () => {
    const s = servidor({
      fase: [fila({ fase: "spec" })],
      nodo: [fila({ nodo: "gate-spec" }), fila({ nodo: "redactar-plan" }, { costo_usd: 5 })],
      unidad: [],
    });
    const user = userEvent.setup();
    montar();
    await screen.findByText("spec");

    await user.selectOptions(screen.getByLabelText("Agrupar por"), "nodo");
    expect(await screen.findByText("gate-spec")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Nodo del DAG" })).toBeInTheDocument();
    expect(consultas(s).some((c) => c.agrupar_por[0] === "nodo" && c.workspace === "cert")).toBe(true);
  });

  it("por workspace consulta toda la organización (sin acotar el workspace)", async () => {
    const s = servidor({ fase: [fila({ fase: "spec" })], workspace: [fila({ workspace: "cert" }), fila({ workspace: "firmas" })], unidad: [] });
    const user = userEvent.setup();
    montar();
    await screen.findByText("spec");

    await user.selectOptions(screen.getByLabelText("Agrupar por"), "workspace");
    expect(await screen.findByText("firmas")).toBeInTheDocument();
    const c = consultas(s).find((x) => x.agrupar_por[0] === "workspace");
    expect(c).toMatchObject({ org: "acme", workspace: null });
  });

  it("si el servidor niega el desglose por workspace lo muestra", async () => {
    servidor({
      fase: [fila({ fase: "spec" })],
      workspace: json(403, { detalle: "telemetry.query requiere rol lector a nivel de organización" }),
      unidad: [],
    });
    const user = userEvent.setup();
    montar();
    await screen.findByText("spec");
    await user.selectOptions(screen.getByLabelText("Agrupar por"), "workspace");
    expect(await screen.findByText(/requiere rol lector a nivel de organización/)).toBeInTheDocument();
  });

  it("la métrica cambia las series del gráfico: duración media y caché", async () => {
    servidor({ fase: [fila({ fase: "spec" }, { llamadas: 2, duracion_ms: 5000 })], unidad: [] });
    const user = userEvent.setup();
    montar();
    const grafico = await screen.findByRole("img", { name: /Costo y tokens por fase/ });
    expect(JSON.parse(grafico.getAttribute("data-series")!)).toEqual(["Costo (USD)", "Tokens"]);

    await user.selectOptions(screen.getByLabelText("Métrica"), "duracion");
    const duracion = await screen.findByRole("img", { name: /Duración media por llamada por fase/ });
    expect(JSON.parse(duracion.getAttribute("data-series")!)).toEqual(["Duración media (s)", "Llamadas"]);
    expect(JSON.parse(duracion.getAttribute("data-datos")!)).toEqual([2.5]);

    await user.selectOptions(screen.getByLabelText("Métrica"), "cache");
    const cache = await screen.findByRole("img", { name: /Caché de prompts por fase/ });
    expect(JSON.parse(cache.getAttribute("data-datos")!)).toEqual([75]);
  });

  it("el rango cambia el intervalo consultado", async () => {
    const s = servidor({ fase: [fila({ fase: "spec" })], unidad: [] });
    const user = userEvent.setup();
    montar();
    await screen.findByText("spec");
    const dias = (c: { desde: string; hasta: string }) => Math.round((Date.parse(c.hasta) - Date.parse(c.desde)) / 86_400_000);
    expect(dias(consultas(s)[0])).toBe(30);

    await user.selectOptions(screen.getByLabelText("Rango"), "7");
    await waitFor(() => expect(consultas(s).some((c) => dias(c) === 7)).toBe(true));
  });

  it("sin llamadas en el rango lo dice y la tabla de unidades sigue aparte", async () => {
    servidor({ fase: [], unidad: [fila({ unidad: "0001-a" })] });
    montar();
    expect(await screen.findByText("Sin llamadas en el rango")).toBeInTheDocument();
    expect(await screen.findByText("0001-a")).toBeInTheDocument();
    expect(screen.getByText("Unidades más caras")).toBeInTheDocument();
  });
});
