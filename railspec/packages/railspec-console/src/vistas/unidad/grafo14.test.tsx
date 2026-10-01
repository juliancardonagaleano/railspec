import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from "@tanstack/react-router";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Trazabilidad } from "../../api/tipos";
import { SeccionCriterios } from "../grafo/SeccionCriterios";
import { PestanaImpacto } from "./PestanaImpacto";
import { PestanaTrazabilidad } from "./PestanaTrazabilidad";

// Contrato 1.4 de graph.query (verbos `impact` y `trace`): lo que la SPA envía y cómo
// pinta lo que el servidor devuelve. Las formas siguen schemas/v1/tools.json.

const sha = (c: string) => c.repeat(64);
const COMMIT = "a".repeat(40);
const simbolo = (c: string, nombre: string) => ({
  tipo: "simbolo",
  repositorio: "api",
  commit: COMMIT,
  simbolo: sha(c),
  nombre,
  tipo_simbolo: "funcion",
  ruta: `src/${nombre}.py`,
});
const salida = (resultados: unknown[]) => ({ resultados, commits: { api: COMMIT }, truncado: false, version_contrato: "1.4" });

function montar(componente: () => ReactNode) {
  const raiz = createRootRoute({ component: Outlet });
  const indice = createRoute({ getParentRoute: () => raiz, path: "/", component: componente });
  const grafo = createRoute({ getParentRoute: () => raiz, path: "/$org/$ws/grafo" });
  const unidad = createRoute({ getParentRoute: () => raiz, path: "/$org/$ws/unidades/$unidad" });
  const router = createRouter({
    routeTree: raiz.addChildren([indice, grafo, unidad]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={cliente}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

/** Falsifica `fetch` y devuelve el cuerpo de cada llamada a graph.query. */
function servidor(responder: (cuerpo: Record<string, unknown>) => Response) {
  const cuerpos: Record<string, unknown>[] = [];
  const fetchFalso = vi.fn(async (url: string, init?: RequestInit) => {
    expect(url).toBe("/consola/api/tools/graph.query");
    const cuerpo = JSON.parse(String(init?.body)) as Record<string, unknown>;
    cuerpos.push(cuerpo);
    return responder(cuerpo);
  });
  vi.stubGlobal("fetch", fetchFalso);
  return cuerpos;
}

const json = (estado: number, cuerpo: unknown) => new Response(JSON.stringify(cuerpo), { status: estado });

describe("contrato 1.4 de graph.query en la SPA", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("impacto: envía unidad y verbo impact, y separa tocados de afectados con su riesgo", async () => {
    const cuerpos = servidor(() =>
      json(
        200,
        salida([
          { ref: simbolo("1", "firmar"), puntuacion: null, relacion: null, distancia: 0, riesgo: "alto" },
          { ref: simbolo("2", "emitir"), puntuacion: null, relacion: "llama", distancia: 1, riesgo: "alto" },
          { ref: simbolo("3", "api_emitir"), puntuacion: null, relacion: "llama", distancia: 2, riesgo: "alto" },
        ]),
      ),
    );
    montar(() => <PestanaImpacto org="acme" ws="cert" unidad="0001-firma" />);
    expect(await screen.findByText("Símbolos tocados (1)")).toBeInTheDocument();
    expect(screen.getByText("Afectados aguas arriba (2)")).toBeInTheDocument();
    expect(screen.getByText("riesgo alto")).toBeInTheDocument();
    expect(screen.queryByText(/contrato 1\.4/)).toBeNull();
    // `unidad` va en el primer nivel (no dentro de `consulta`) y es obligatoria para impact.
    expect(cuerpos).toEqual([
      { alcance: { org: "acme", workspace: "cert" }, unidad: "0001-firma", consulta: { verbo: "impact", profundidad: 3 }, limite: 200 },
    ]);
    const afectados = screen.getByText("Afectados aguas arriba (2)").closest("section")!;
    const filas = within(afectados).getAllByRole("row").slice(1);
    expect(within(filas[0]!).getByText("emitir")).toBeInTheDocument();
    expect(within(filas[0]!).getByText("llama")).toBeInTheDocument();
    expect(within(filas[1]!).getByText("api_emitir")).toBeInTheDocument();
  });

  it("impacto: un servidor anterior a 1.4 (422 en `consulta`) muestra el aviso", async () => {
    servidor(() =>
      json(422, {
        detalle: "entrada fuera de contrato",
        errores: [{ ruta: "consulta", mensaje: "Input tag 'impact' found using 'verbo' does not match any of the expected tags" }],
      }),
    );
    montar(() => <PestanaImpacto org="acme" ws="cert" unidad="0001-firma" />);
    expect(await screen.findByText("Disponible cuando el servidor tenga el contrato 1.4.")).toBeInTheDocument();
  });

  it("impacto: un servidor 1.4 sin grafo (404) se ve como error, no como falta de contrato", async () => {
    servidor(() => json(404, { codigo: "no-encontrado", detalle: "tool graph.query no disponible", version_contrato: "1.4" }));
    montar(() => <PestanaImpacto org="acme" ws="cert" unidad="0001-firma" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("tool graph.query no disponible");
    expect(screen.queryByText(/contrato 1\.4/)).toBeNull();
  });

  it("trazabilidad: trace por criterio envía unidad y criterio, y lista los RefSimbolo", async () => {
    const cuerpos = servidor(() => json(200, salida([{ ref: simbolo("4", "verificar") }])));
    const datos: Trazabilidad = {
      criterios: [{ id: "CA-07", texto: "Firma el certificado", tareas: [], archivos: [], simbolos: [], hallazgos: [] }],
      sin_criterio: { tareas: [] },
    };
    montar(() => <PestanaTrazabilidad datos={datos} org="acme" ws="cert" unidad="0001-firma" />);
    await userEvent.click(await screen.findByRole("button", { name: "ver símbolos en el grafo" }));
    expect(await screen.findByRole("link", { name: "verificar" })).toBeInTheDocument();
    expect(cuerpos).toEqual([{ alcance: { org: "acme", workspace: "cert" }, unidad: "0001-firma", consulta: { verbo: "trace", criterio: "CA-07" } }]);
  });

  it("trazabilidad: un criterio sin redacción (texto null, solo aparece en tareas) se pinta con un aviso", async () => {
    const tarea = { id: "T-01", descripcion: "Verificar la cadena", grupo: null, completada: false, orden: null };
    const datos: Trazabilidad = {
      criterios: [
        { id: "CA-07", texto: "Firma el certificado", tareas: [], archivos: [], simbolos: [], hallazgos: [] },
        { id: "CA-09", texto: null, tareas: [tarea], archivos: [], simbolos: [], hallazgos: [] },
      ],
      sin_criterio: { tareas: [] },
    };
    montar(() => <PestanaTrazabilidad datos={datos} org="acme" ws="cert" unidad="0001-firma" />);
    const filas = await screen.findAllByRole("row");
    const huerfano = filas.find((f) => within(f).queryByText("CA-09"));
    expect(huerfano).toBeDefined();
    expect(within(huerfano as HTMLElement).getByText("Sin redacción: solo aparece en tareas.")).toBeInTheDocument();
    expect(within(huerfano as HTMLElement).getByText(/Verificar la cadena/)).toBeInTheDocument();
    expect(within(huerfano as HTMLElement).queryByText("null")).toBeNull();
    // El criterio redactado conserva su texto y no lleva el aviso.
    const redactado = filas.find((f) => within(f).queryByText("CA-07")) as HTMLElement;
    expect(within(redactado).getByText("Firma el certificado")).toBeInTheDocument();
    expect(within(redactado).queryByText(/Sin redacción/)).toBeNull();
  });

  it("símbolo: trace por símbolo envía solo el símbolo (sin criterio ni unidad) y enlaza las unidades de los RefCriterio", async () => {
    const cuerpos = servidor(() =>
      json(200, salida([{ ref: { tipo: "criterio", workspace: "cert", unidad: "0002-revocar", criterio: "CA-03" } }])),
    );
    montar(() => <SeccionCriterios org="acme" ws="cert" simbolo={sha("1")} />);
    const enlace = await screen.findByRole("link", { name: /CA-03/ });
    expect(enlace).toHaveAttribute("href", "/acme/cert/unidades/0002-revocar");
    expect(cuerpos).toEqual([{ alcance: { org: "acme", workspace: "cert" }, consulta: { verbo: "trace", simbolo: sha("1") } }]);
  });
});
