import { Link } from "@tanstack/react-router";
import type { CausaParada, EstadoMandato } from "../../api/tipos";
import { Badge } from "../../componentes/ui/badge";
import { EXPLICACION_CAUSA, estadoVisible, TONO_ESTADO_MANDATO } from "./mandato";

/** Insignia del estado; un mandato `aprobado` con la vigencia pasada se muestra como «caducado». */
export function EtiquetaEstadoMandato({ estado, vigente }: { estado: EstadoMandato; vigente: boolean }) {
  const visible = estadoVisible(estado, vigente);
  return <Badge tono={TONO_ESTADO_MANDATO[visible]}>{visible}</Badge>;
}

/** Insignia de la causa de una parada; el título explica el ámbito y qué hacer. */
export function EtiquetaCausaParada({ causa }: { causa: CausaParada }) {
  const e = EXPLICACION_CAUSA[causa];
  return (
    <Badge tono="aviso" title={`${e.ambito === "mandato" ? "Detiene el mandato" : "Detiene la unidad"}: ${e.texto}`}>
      {causa}
    </Badge>
  );
}

/** Insignia de la unidad con enlace al mandato que la ampara (`unidad.plan`). */
export function InsigniaMandato({ org, ws, mandato }: { org: string; ws: string; mandato: string }) {
  return (
    <Link to="/$org/$ws/mandatos/$mandato" params={{ org, ws, mandato }} className="rounded-full focus-visible:outline-2 focus-visible:outline-primario">
      <Badge tono="violeta">mandato {mandato}</Badge>
    </Link>
  );
}
