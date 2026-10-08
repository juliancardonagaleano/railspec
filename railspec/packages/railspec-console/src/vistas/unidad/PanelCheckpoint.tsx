import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ErrorApi } from "../../api/cliente";
import { claves, unidades } from "../../api/endpoints";
import type { AlcanceUnidad, Checkpoint, Decision } from "../../api/tipos";
import { Aviso, ErrorVista } from "../../componentes/Estados";
import { Button } from "../../componentes/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../componentes/ui/card";
import { Campo, Textarea } from "../../componentes/ui/input";
import { fecha } from "../../lib/utiles";
import { EtiquetaCausaParada } from "../mandato/EtiquetasMandato";
import { EXPLICACION_CAUSA } from "../mandato/mandato";

const ETIQUETA_TIPO: Record<Checkpoint["tipo"], string> = {
  "aprobar-spec": "Aprobar spec",
  "aprobar-plan": "Aprobar plan",
  "paquete-aprobacion": "Paquete de aprobación",
  parada: "Parada",
  "gate-escalado": "Gate escalado",
};

/** ¿La decisión exige comentario? Solo aprobar puede ir sin él. */
export const exigeComentario = (d: Decision) => d !== "aprobado";

export function PanelCheckpoint({
  alcance,
  checkpoint,
  puedeResolver,
}: {
  alcance: AlcanceUnidad;
  checkpoint: Checkpoint;
  puedeResolver: boolean;
}) {
  const clienteQuery = useQueryClient();
  const [comentario, setComentario] = useState("");
  const [yaResuelto, setYaResuelto] = useState(false);
  const refrescar = () => clienteQuery.invalidateQueries({ queryKey: claves.unidad(alcance.org, alcance.workspace, alcance.unidad) });

  const resolver = useMutation({
    mutationFn: (decision: Decision) => {
      const entrada: Parameters<typeof unidades.aprobar>[0] = { unidad: alcance, checkpoint: checkpoint.id, decision };
      if (comentario.trim()) entrada.comentario = comentario.trim();
      return unidades.aprobar(entrada);
    },
    onSuccess: () => {
      setComentario("");
      void refrescar();
    },
    onError: (e) => {
      if (e instanceof ErrorApi && e.codigo === "checkpoint-ya-resuelto") {
        setYaResuelto(true);
        void refrescar();
      }
    },
  });

  const enviar = (d: Decision) => {
    if (exigeComentario(d) && !comentario.trim()) return;
    resolver.mutate(d);
  };
  const sinComentario = !comentario.trim();
  const error = resolver.error;
  const errorNoResuelto = error && !(error instanceof ErrorApi && error.codigo === "checkpoint-ya-resuelto");

  return (
    <Card className="border-amber-300 dark:border-amber-800">
      <CardHeader>
        <CardTitle>Checkpoint pendiente: {ETIQUETA_TIPO[checkpoint.tipo]}</CardTitle>
        <CardDescription>
          Fase {checkpoint.fase} · abierto {fecha(checkpoint.abierto_en)}. Es opcional resolverlo aquí: el desarrollador puede
          resolverlo desde su arnés y no queda bloqueado por la consola. Gana el primer canal que responda.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {checkpoint.causa_parada ? (
          <Aviso tono="aviso">
            <p className="flex flex-wrap items-center gap-2">
              Parada bajo mandato: <EtiquetaCausaParada causa={checkpoint.causa_parada} />
              <span className="text-xs">({EXPLICACION_CAUSA[checkpoint.causa_parada].ambito === "mandato" ? "detiene el mandato" : "detiene solo esta unidad"})</span>
            </p>
            <p className="mt-1">{EXPLICACION_CAUSA[checkpoint.causa_parada].texto}</p>
          </Aviso>
        ) : null}
        <p className="whitespace-pre-wrap rounded-md bg-fondo p-3 text-sm">{checkpoint.pregunta}</p>
        {yaResuelto ? (
          <Aviso tono="aviso">Este checkpoint ya lo resolvió otro canal (arnés, CI u otra sesión). Se recargó el estado.</Aviso>
        ) : null}
        {errorNoResuelto ? <ErrorVista error={error} /> : null}
        {puedeResolver ? (
          <>
            <Campo
              etiqueta="Comentario"
              htmlFor={`comentario-${checkpoint.id}`}
              ayuda="Obligatorio para pedir cambios o rechazar; opcional al aprobar."
            >
              <Textarea
                id={`comentario-${checkpoint.id}`}
                value={comentario}
                maxLength={4000}
                onChange={(e) => setComentario(e.target.value)}
              />
            </Campo>
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => enviar("aprobado")} disabled={resolver.isPending}>
                Aprobar
              </Button>
              <Button variante="secundario" onClick={() => enviar("cambios-solicitados")} disabled={resolver.isPending || sinComentario}>
                Pedir cambios
              </Button>
              <Button variante="peligro" onClick={() => enviar("rechazado")} disabled={resolver.isPending || sinComentario}>
                Rechazar
              </Button>
            </div>
          </>
        ) : (
          <p className="text-sm text-suave">Tu rol solo permite consultar este checkpoint.</p>
        )}
      </CardContent>
    </Card>
  );
}
