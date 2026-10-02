import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { json, montarEnRuta, servidorFalso, type Llamada } from "../../pruebas/servidor";
import type { ModeloGrafo } from "./modelo";
import { NavegadorGrafo } from "./NavegadorGrafo";

// Sigma necesita WebGL (jsdom no lo tiene): el lienzo se sustituye por una lista con el cambio de cada nodo.
vi.mock("./LienzoSigma", () => ({
  LienzoSigma: ({ modelo, colorearPor }: { modelo: ModeloGrafo; colorearPor: string }) => (
    <ul aria-label="Lienzo" data-colorear={colorearPor}>
      {Object.values(modelo.nodos).map((n) => (
        <li key={n.id}>{`${n.nombre}:${n.cambio ?? "igual"}`}</li>
      ))}
      {Object.values(modelo.aristas).map((a) => (
        <li key={a.id}>{`arista:${a.cambio ?? "igual"}`}</li>
      ))}
    </ul>
  ),
}));

const sha = (c: string) => c.repeat(64);
const COMMIT = "a".repeat(40);
const ref = (c: string, nombre: string) => ({
  tipo: "simbolo",
  repositorio: "api",
  commit: COMMIT,
  simbolo: sha(c),
  nombre,
  tipo_simbolo: "funcion",
  ruta: `src/${nombre}.py`,
});
const salida = (resultados: unknown[]) => ({ resultados, commits: { api: COMMIT }, truncado: false });
const res = (c: string, nombre: string, relacion = "llama", extra: Record<string, unknown> = {}) => ({ ref: ref(c, nombre), relacion, distancia: 1, ...extra });

/** Base: la llama «viejo»; snapshot: la llama «nuevo» (y «viejo» ya no). La unidad toca «centro». */
function respuesta(l: Llamada) {
  const { consulta, unidad } = l.cuerpo as { consulta: { verbo: string; direccion?: string }; unidad?: string };
  if (consulta.verbo === "impact") return salida([res("c", "centro", undefined, { distancia: 0, relacion: undefined })]);
  if (consulta.verbo === "traverse" && consulta.direccion === "upstream") return salida(unidad ? [res("5", "nuevo")] : [res("2", "viejo")]);
  return salida([]);
}

function servidor() {
  return servidorFalso({
    "GET /orgs/acme/workspaces/cert/grafo/repositorios": () => [{ repositorio: "api", nivel_codigo: "restringido", rol: "primario", commit: COMMIT }],
    "POST /tools/unit.list": () => ({
      unidades: [
        { unidad: "0001-emitir-pdf", titulo: "Emitir PDF", fase: "implement", estado: "en-progreso", modo: "interactivo", riesgo: "bajo", repositorio_primario: "api", dueno_login: "ana", integrada: false, actualizado_en: "2026-10-02T10:00:00Z" },
      ],
      cursor_siguiente: null,
    }),
    "POST /tools/graph.query": (l) => (l.cuerpo.consulta ? respuesta(l) : json(422, {})),
  });
}

// El id del centro viaja por la URL: con solo dígitos el analizador de la ruta lo leería como número.
const ABRIR = `/acme/cert/grafo?simbolo=${sha("c")}&nombre=centro&repositorio=api`;
const montar = (url: string) => montarEnRuta(() => <NavegadorGrafo />, "/$org/$ws/grafo", url);
const consultas = (s: ReturnType<typeof servidor>) => s.de("POST", "/tools/graph.query").map((l) => l.cuerpo);

describe("grafo: base contra snapshot de una unidad", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sin unidad solo mira la base y no marca cambios", async () => {
    const s = servidor();
    montar(ABRIR);
    const lienzo = await screen.findByRole("list", { name: "Lienzo" });
    await waitFor(() => expect(within(lienzo).getByText("viejo:igual")).toBeInTheDocument());
    expect(consultas(s).every((c) => c.unidad === undefined)).toBe(true);
    expect(consultas(s).some((c) => (c.consulta as { verbo: string }).verbo === "impact")).toBe(false);
    expect(screen.queryByRole("list", { name: "Leyenda de la comparación" })).toBeNull();
  });

  it("con una unidad pide el vecindario a la base y al snapshot y marca lo nuevo, lo oculto y lo tocado", async () => {
    const s = servidor();
    montar(`${ABRIR}&unidad=0001-emitir-pdf`);
    const lienzo = await screen.findByRole("list", { name: "Lienzo" });
    await waitFor(() => expect(within(lienzo).getByText("nuevo:nuevo")).toBeInTheDocument());
    expect(within(lienzo).getByText("viejo:eliminado")).toBeInTheDocument();
    expect(within(lienzo).getByText("centro:tocado")).toBeInTheDocument();
    expect(within(lienzo).getByText("arista:nueva")).toBeInTheDocument();
    expect(within(lienzo).getByText("arista:eliminada")).toBeInTheDocument();
    // Se colorea por cambio y se explica la leyenda.
    expect(lienzo).toHaveAttribute("data-colorear", "cambio");
    expect(screen.getByRole("list", { name: "Leyenda de la comparación" })).toBeInTheDocument();
    expect(await screen.findByText("La unidad toca 1 símbolo(s).")).toBeInTheDocument();

    const traverse = consultas(s).filter((c) => (c.consulta as { verbo: string }).verbo === "traverse");
    // upstream y downstream, una vez contra la base (sin `unidad`) y otra contra el snapshot.
    expect(traverse.filter((c) => c.unidad === "0001-emitir-pdf")).toHaveLength(2);
    expect(traverse.filter((c) => c.unidad === undefined)).toHaveLength(2);
  });

  it("al dejar la comparación vuelve a mirar solo la base y borra las marcas", async () => {
    const s = servidor();
    const user = userEvent.setup();
    montar(`${ABRIR}&unidad=0001-emitir-pdf`);
    const lienzo = await screen.findByRole("list", { name: "Lienzo" });
    await waitFor(() => expect(within(lienzo).getByText("nuevo:nuevo")).toBeInTheDocument());
    const antes = consultas(s).length;

    await user.selectOptions(await screen.findByLabelText("Comparar con una unidad"), "");
    await waitFor(() => expect(screen.getByRole("list", { name: "Lienzo" })).toHaveAttribute("data-colorear", "tipo"));
    const despues = screen.getByRole("list", { name: "Lienzo" });
    await waitFor(() => expect(within(despues).getByText("viejo:igual")).toBeInTheDocument());
    expect(within(despues).queryByText("nuevo:nuevo")).toBeNull();
    expect(consultas(s).slice(antes).every((c) => c.unidad === undefined)).toBe(true);
  });

  it("elegir una unidad de la lista compara el símbolo ya abierto", async () => {
    const s = servidor();
    const user = userEvent.setup();
    montar(ABRIR);
    await waitFor(() => expect(screen.getByRole("list", { name: "Lienzo" })).toBeInTheDocument());
    await user.selectOptions(await screen.findByLabelText("Comparar con una unidad"), "0001-emitir-pdf");
    await waitFor(() => expect(within(screen.getByRole("list", { name: "Lienzo" })).getByText("nuevo:nuevo")).toBeInTheDocument());
    expect(consultas(s).some((c) => c.unidad === "0001-emitir-pdf")).toBe(true);
  });

  it("si el servidor no habla el contrato 1.4 compara igual, sin marcar lo tocado", async () => {
    servidorFalso({
      "GET /orgs/acme/workspaces/cert/grafo/repositorios": () => [],
      "POST /tools/unit.list": () => ({ unidades: [], cursor_siguiente: null }),
      "POST /tools/graph.query": (l: Llamada) => {
        const { consulta, unidad } = l.cuerpo as { consulta: { verbo: string; direccion?: string }; unidad?: string };
        if (consulta.verbo === "impact") return json(422, { detalle: "entrada inválida", errores: [{ ruta: "consulta", mensaje: "verbo desconocido" }] });
        if (consulta.verbo === "traverse" && consulta.direccion === "upstream") return salida(unidad ? [res("5", "nuevo")] : []);
        return salida([]);
      },
    });
    montar(`${ABRIR}&unidad=0001-emitir-pdf`);
    const lienzo = await screen.findByRole("list", { name: "Lienzo" });
    await waitFor(() => expect(within(lienzo).getByText("nuevo:nuevo")).toBeInTheDocument());
    expect(within(lienzo).getByText("centro:igual")).toBeInTheDocument();
    expect(await screen.findByText(/aún no habla el contrato 1\.4/)).toBeInTheDocument();
  });
});
