import { useEffect, useRef } from "react";
import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import Sigma from "sigma";
import { COLOR_CAMBIO, COLOR_RELACION, COLOR_TIPO, colorRepositorio, type ModeloGrafo, type NodoModelo } from "./modelo";

export type ColorearPor = "tipo" | "repositorio" | "cambio";

interface Props {
  modelo: ModeloGrafo;
  seleccionado: string | null;
  colorearPor: ColorearPor;
  alElegir: (id: string) => void;
}

function colorDeNodo(n: NodoModelo, colorearPor: ColorearPor): string {
  if (colorearPor === "tipo") return COLOR_TIPO[n.tipo_simbolo];
  if (colorearPor === "repositorio") return colorRepositorio(n.repositorio);
  return COLOR_CAMBIO[n.cambio ?? "igual"];
}

/** Sincroniza el grafo de graphology con el modelo, conservando las posiciones ya calculadas. */
function sincronizar(grafo: Graph, modelo: ModeloGrafo, colorearPor: ColorearPor): void {
  for (const id of grafo.nodes()) if (!modelo.nodos[id]) grafo.dropNode(id);
  for (const id of grafo.edges()) if (!modelo.aristas[id]) grafo.dropEdge(id);

  const nuevos: string[] = [];
  for (const n of Object.values(modelo.nodos)) {
    const color = colorDeNodo(n, colorearPor);
    const atributos = { label: n.nombre, color, size: modelo.expandidos.includes(n.id) ? 9 : 6 };
    if (grafo.hasNode(n.id)) grafo.mergeNodeAttributes(n.id, atributos);
    else {
      grafo.addNode(n.id, { ...atributos, x: Math.random() * 10, y: Math.random() * 10 });
      nuevos.push(n.id);
    }
  }
  for (const a of Object.values(modelo.aristas)) {
    if (!grafo.hasNode(a.origen) || !grafo.hasNode(a.destino)) continue;
    // Al comparar, una relación nueva o eliminada se distingue por color en cualquier modo de coloreado.
    const color =
      a.cambio === "nueva" ? COLOR_CAMBIO.nuevo : a.cambio === "eliminada" ? COLOR_CAMBIO.eliminado : a.relacion ? COLOR_RELACION[a.relacion] : "#94a3b8";
    const atributos = { label: a.relacion ?? "", color, size: a.indirecta ? 1 : a.cambio ? 3 : 2, type: "arrow" };
    if (grafo.hasEdge(a.id)) grafo.mergeEdgeAttributes(a.id, atributos);
    else grafo.addDirectedEdgeWithKey(a.id, a.origen, a.destino, atributos);
  }
  // Coloca los nuevos cerca de un vecino ya posicionado y reajusta con ForceAtlas2.
  for (const id of nuevos) {
    const vecino = grafo.neighbors(id).find((v) => !nuevos.includes(v));
    if (vecino) {
      const { x, y } = grafo.getNodeAttributes(vecino) as { x: number; y: number };
      grafo.mergeNodeAttributes(id, { x: x + (Math.random() - 0.5) * 4, y: y + (Math.random() - 0.5) * 4 });
    }
  }
  if (grafo.order > 1 && nuevos.length > 0) {
    forceAtlas2.assign(grafo, { iterations: 80, settings: { ...forceAtlas2.inferSettings(grafo), slowDown: 5 } });
  }
}

/** Lienzo WebGL del grafo de código (Sigma.js). Clic en un nodo = elegirlo. */
export function LienzoSigma({ modelo, seleccionado, colorearPor, alElegir }: Props) {
  const contenedor = useRef<HTMLDivElement>(null);
  const grafo = useRef(new Graph({ type: "directed", multi: true }));
  const sigma = useRef<Sigma | null>(null);
  const seleccion = useRef(seleccionado);
  const elegir = useRef(alElegir);
  elegir.current = alElegir;

  useEffect(() => {
    const div = contenedor.current;
    if (!div) return;
    let instancia: Sigma;
    try {
      instancia = new Sigma(grafo.current, div, {
        renderEdgeLabels: true,
        defaultEdgeType: "arrow",
        labelRenderedSizeThreshold: 4,
        nodeReducer: (nodo, datos) =>
          nodo === seleccion.current ? { ...datos, highlighted: true, size: (datos.size ?? 6) + 4, zIndex: 1 } : datos,
      });
    } catch {
      div.textContent = "Tu navegador no soporta WebGL; no se puede pintar el grafo.";
      return;
    }
    instancia.on("clickNode", ({ node }) => elegir.current(node));
    sigma.current = instancia;
    return () => {
      instancia.kill();
      sigma.current = null;
    };
  }, []);

  useEffect(() => {
    sincronizar(grafo.current, modelo, colorearPor);
    sigma.current?.refresh();
  }, [modelo, colorearPor]);

  useEffect(() => {
    seleccion.current = seleccionado;
    sigma.current?.refresh();
  }, [seleccionado]);

  return (
    <div
      ref={contenedor}
      className="h-[60vh] min-h-80 w-full rounded-lg border border-borde bg-superficie"
      role="img"
      aria-label={`Grafo con ${Object.keys(modelo.nodos).length} símbolos y ${Object.keys(modelo.aristas).length} relaciones`}
    />
  );
}
