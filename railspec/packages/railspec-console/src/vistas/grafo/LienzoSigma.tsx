import { useEffect, useRef } from "react";
import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import Sigma from "sigma";
import { COLOR_RELACION, COLOR_TIPO, colorRepositorio, type ModeloGrafo } from "./modelo";

export type ColorearPor = "tipo" | "repositorio";

interface Props {
  modelo: ModeloGrafo;
  seleccionado: string | null;
  colorearPor: ColorearPor;
  alElegir: (id: string) => void;
}

/** Sincroniza el grafo de graphology con el modelo, conservando las posiciones ya calculadas. */
function sincronizar(grafo: Graph, modelo: ModeloGrafo, colorearPor: ColorearPor): void {
  for (const id of grafo.nodes()) if (!modelo.nodos[id]) grafo.dropNode(id);
  for (const id of grafo.edges()) if (!modelo.aristas[id]) grafo.dropEdge(id);

  const nuevos: string[] = [];
  for (const n of Object.values(modelo.nodos)) {
    const color = colorearPor === "tipo" ? COLOR_TIPO[n.tipo_simbolo] : colorRepositorio(n.repositorio);
    const atributos = { label: n.nombre, color, size: modelo.expandidos.includes(n.id) ? 9 : 6 };
    if (grafo.hasNode(n.id)) grafo.mergeNodeAttributes(n.id, atributos);
    else {
      grafo.addNode(n.id, { ...atributos, x: Math.random() * 10, y: Math.random() * 10 });
      nuevos.push(n.id);
    }
  }
  for (const a of Object.values(modelo.aristas)) {
    if (grafo.hasEdge(a.id) || !grafo.hasNode(a.origen) || !grafo.hasNode(a.destino)) continue;
    grafo.addDirectedEdgeWithKey(a.id, a.origen, a.destino, {
      label: a.relacion ?? "",
      color: a.relacion ? COLOR_RELACION[a.relacion] : "#94a3b8",
      size: a.indirecta ? 1 : 2,
      type: "arrow",
    });
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
