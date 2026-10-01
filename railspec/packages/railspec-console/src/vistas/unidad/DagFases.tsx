import { useMemo } from "react";
import { Background, MarkerType, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { EstadoUnidad } from "../../api/tipos";
import { calcularDag, COLOR_ESTADO, ETIQUETA_ESTADO_NODO, type EstadoNodo } from "./dag";

const ESTADOS: EstadoNodo[] = ["hecho", "actual", "pendiente", "escalado"];

export function Leyenda() {
  return (
    <ul className="flex flex-wrap gap-3 text-xs" aria-label="Leyenda del DAG">
      {ESTADOS.map((e) => (
        <li key={e} className="flex items-center gap-1">
          <span
            aria-hidden="true"
            className="inline-block h-3 w-3 rounded-sm border"
            style={{ background: COLOR_ESTADO[e].fondo, borderColor: COLOR_ESTADO[e].borde }}
          />
          {ETIQUETA_ESTADO_NODO[e]}
        </li>
      ))}
    </ul>
  );
}

/** DAG de fases y gates de la unidad con React Flow (solo lectura). */
export function DagFases({ estado }: { estado: EstadoUnidad }) {
  const { nodes, edges, descripcion } = useMemo(() => {
    const { nodos, aristas } = calcularDag(estado);
    let x = 0;
    const nodes: Node[] = nodos.map((n) => {
      const color = COLOR_ESTADO[n.estado];
      const esGate = n.clase === "gate";
      const nodo: Node = {
        id: n.id,
        position: { x, y: esGate ? 8 : 0 },
        data: {
          label: (
            <div title={`${n.etiqueta}: ${ETIQUETA_ESTADO_NODO[n.estado]}`}>
              <div className="font-semibold">{n.etiqueta}</div>
              <div className="text-[10px] opacity-80">
                {n.veredicto ? `${n.veredicto}${n.rehabilitado ? " · rehabilitado" : ""}` : ETIQUETA_ESTADO_NODO[n.estado]}
              </div>
            </div>
          ),
        },
        draggable: false,
        connectable: false,
        sourcePosition: "right" as Node["sourcePosition"],
        targetPosition: "left" as Node["targetPosition"],
        style: {
          background: color.fondo,
          borderColor: color.borde,
          color: color.texto,
          borderWidth: n.estado === "actual" ? 3 : 1.5,
          borderRadius: esGate ? 999 : 8,
          width: esGate ? 110 : 130,
          fontSize: 12,
          padding: 6,
        },
      };
      x += esGate ? 140 : 160;
      return nodo;
    });
    const edges: Edge[] = aristas.map((a) => ({
      id: a.id,
      source: a.origen,
      target: a.destino,
      animated: a.activa,
      markerEnd: { type: MarkerType.ArrowClosed },
    }));
    const descripcion = nodos.map((n) => `${n.etiqueta}: ${ETIQUETA_ESTADO_NODO[n.estado]}`).join(", ");
    return { nodes, edges, descripcion };
  }, [estado]);

  return (
    <figure className="flex flex-col gap-2">
      <div className="h-40 w-full rounded-lg border border-borde bg-superficie" role="img" aria-label={`Fases y gates: ${descripcion}`}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          fitView
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          panOnScroll
          zoomOnScroll={false}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={16} />
        </ReactFlow>
      </div>
      <figcaption>
        <Leyenda />
      </figcaption>
    </figure>
  );
}
