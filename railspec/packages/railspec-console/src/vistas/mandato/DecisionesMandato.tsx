import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ErrorApi } from "../../api/cliente";
import { claves, mandatos } from "../../api/endpoints";
import type { DecisionDelegada, ResultadoRevision } from "../../api/tipos";
import { Aviso, ErrorVista, Vacio } from "../../componentes/Estados";
import { Badge } from "../../componentes/ui/badge";
import { Button } from "../../componentes/ui/button";
import { Campo, Textarea } from "../../componentes/ui/input";
import { fecha, nombreActor } from "../../lib/utiles";
import { etiquetaDelegacion } from "./mandato";

export interface DecisionConUnidad {
  unidad: string;
  decision: DecisionDelegada;
}

/** Revertir una decisión exige comentario; aceptar puede ir sin él. */
export const exigeComentarioRevision = (r: ResultadoRevision) => r === "revertida";

function EstadoRevision({ d }: { d: DecisionDelegada }) {
  const r = d.revision;
  if (!r) return <Badge tono="aviso">pendiente de revisión</Badge>;
  return (
    <span className="text-xs text-suave">
      <Badge tono={r.resultado === "aceptada" ? "exito" : "peligro"}>{r.resultado}</Badge> por {nombreActor(r.actor)} el {fecha(r.en)}
      {r.comentario ? ` · «${r.comentario}»` : ""}
    </span>
  );
}

function FilaDecision({
  org,
  ws,
  unidad,
  decision: d,
  puedeRevisar,
  versionUnidad,
  enlazarUnidad,
}: {
  org: string;
  ws: string;
  unidad: string;
  decision: DecisionDelegada;
  puedeRevisar: boolean;
  versionUnidad: () => Promise<number>;
  enlazarUnidad: boolean;
}) {
  const clienteQuery = useQueryClient();
  const [comentario, setComentario] = useState("");
  const [desactualizado, setDesactualizado] = useState(false);
  const refrescar = () => {
    void clienteQuery.invalidateQueries({ queryKey: claves.mandatos(org, ws) });
    void clienteQuery.invalidateQueries({ queryKey: claves.unidad(org, ws, unidad) });
  };
  const revisar = useMutation({
    mutationFn: async (resultado: ResultadoRevision) => {
      const version_vista = await versionUnidad();
      const entrada: Parameters<typeof mandatos.revisarDecision>[0] = {
        unidad: { org, workspace: ws, unidad },
        decision: d.id,
        resultado,
        version_vista,
      };
      if (comentario.trim()) entrada.comentario = comentario.trim();
      return mandatos.revisarDecision(entrada);
    },
    onSuccess: () => {
      setComentario("");
      refrescar();
    },
    onError: (e) => {
      if (e instanceof ErrorApi && e.status === 409) {
        setDesactualizado(true);
        refrescar();
      }
    },
  });
  const enviar = (r: ResultadoRevision) => {
    if (exigeComentarioRevision(r) && !comentario.trim()) return;
    setDesactualizado(false);
    revisar.mutate(r);
  };
  const sinComentario = !comentario.trim();
  const idComentario = `comentario-${unidad}-${d.id}`;
  const error = revisar.error && !(revisar.error instanceof ErrorApi && revisar.error.status === 409) ? revisar.error : null;

  return (
    <li className="rounded-md border border-borde p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs">{d.id}</span>
        <Badge tono="violeta" title="Delegación del mandato en que se apoya">
          {etiquetaDelegacion(d.delegacion)}
        </Badge>
        <span className="text-xs text-suave">
          fase {d.fase} · {nombreActor(d.tomada_por)} · {fecha(d.en)}
          {" · "}
          {enlazarUnidad ? (
            <Link to="/$org/$ws/unidades/$unidad" params={{ org, ws, unidad }} className="text-primario underline">
              {unidad}
            </Link>
          ) : (
            unidad
          )}
        </span>
        <EstadoRevision d={d} />
      </div>
      <p className="mt-2 whitespace-pre-wrap">{d.que}</p>
      {d.alternativas && d.alternativas.length > 0 ? (
        <p className="mt-1 text-xs text-suave">Descartó: {d.alternativas.join("; ")}</p>
      ) : null}
      <p className="mt-1 text-xs text-suave">Cómo revertirla: {d.revertir}</p>
      {puedeRevisar && !d.revision ? (
        <div className="mt-2 flex flex-col gap-2">
          <Campo
            etiqueta={`Comentario de ${d.id}`}
            htmlFor={idComentario}
            ayuda="Obligatorio para revertir; opcional al aceptar. Revertir no deshace el código: deja constancia y lo revierte una persona."
          >
            <Textarea id={idComentario} rows={2} maxLength={2000} value={comentario} onChange={(e) => setComentario(e.target.value)} />
          </Campo>
          {desactualizado ? (
            <Aviso tono="aviso">La unidad cambió mientras revisabas; ya se recargó. Revisa y vuelve a intentarlo.</Aviso>
          ) : null}
          {error ? <ErrorVista error={error} /> : null}
          <div className="flex flex-wrap gap-2">
            <Button tamano="pequeno" aria-label={`Aceptar ${d.id}`} onClick={() => enviar("aceptada")} disabled={revisar.isPending}>
              Aceptar
            </Button>
            <Button
              tamano="pequeno"
              variante="peligro"
              aria-label={`Revertir ${d.id}`}
              onClick={() => enviar("revertida")}
              disabled={revisar.isPending || sinComentario}
            >
              Revertir
            </Button>
          </div>
        </div>
      ) : null}
    </li>
  );
}

/**
 * Decisiones tomadas bajo un mandato, con su estado de revisión y, si la persona puede, **Aceptar** y
 * **Revertir** (`mandate.review`). `versionUnidad` da la versión del estado de la unidad que se «vio»: en el
 * detalle de la unidad es la que ya está en pantalla; en el del mandato se lee al revisar, porque
 * `mandate.get` no la trae.
 */
export function DecisionesMandato({
  org,
  ws,
  decisiones,
  puedeRevisar,
  versionUnidad,
  enlazarUnidad = false,
}: {
  org: string;
  ws: string;
  decisiones: DecisionConUnidad[];
  puedeRevisar: boolean;
  versionUnidad: (unidad: string) => Promise<number>;
  enlazarUnidad?: boolean;
}) {
  if (decisiones.length === 0) return <Vacio titulo="Aún no se tomó ninguna decisión delegada" />;
  return (
    <ul className="flex flex-col gap-2">
      {decisiones.map(({ unidad, decision }) => (
        <FilaDecision
          key={`${unidad}/${decision.id}`}
          org={org}
          ws={ws}
          unidad={unidad}
          decision={decision}
          puedeRevisar={puedeRevisar}
          versionUnidad={() => versionUnidad(unidad)}
          enlazarUnidad={enlazarUnidad}
        />
      ))}
    </ul>
  );
}
