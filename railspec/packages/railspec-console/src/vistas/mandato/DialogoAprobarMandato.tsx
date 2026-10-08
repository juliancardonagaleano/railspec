import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ErrorApi } from "../../api/cliente";
import { claves, mandatos } from "../../api/endpoints";
import type { MandateGetSalida } from "../../api/tipos";
import { Aviso, ErrorVista } from "../../componentes/Estados";
import { Button } from "../../componentes/ui/button";
import { Dialog } from "../../componentes/ui/dialog";
import { Campo, Textarea } from "../../componentes/ui/input";
import { ContenidoMandato, filasPresupuesto } from "./ContenidoMandato";

const HUELLA = /^[0-9a-f]{64}$/;

/**
 * Aprueba (o renueva) un mandato sobre el contenido exacto que se ve aquí. La huella es la que calculó el
 * servidor para `mandate.get`: la consola no la recalcula, solo la devuelve. Si el contenido cambió entre
 * medias el servidor responde `conflicto-version`; el diálogo se recarga con lo nuevo (el contenido y la huella
 * que se muestran son siempre los de la última lectura) y hay que volver a leerlo antes de aprobar.
 */
export function DialogoAprobarMandato({ salida, org, ws, alCerrar }: { salida: MandateGetSalida; org: string; ws: string; alCerrar: () => void }) {
  const clienteQuery = useQueryClient();
  const [comentario, setComentario] = useState("");
  const [cambio, setCambio] = useState(false);
  const { mandato, huella } = salida;
  const c = mandato.contenido;
  const renovando = mandato.estado !== "propuesto";
  const huellaValida = HUELLA.test(huella ?? "");
  const topes = filasPresupuesto(c.limites.presupuesto);

  const aprobar = useMutation({
    mutationFn: () => {
      const entrada: Parameters<typeof mandatos.aprobar>[0] = {
        alcance: mandato.alcance,
        id: mandato.id,
        version_vista: mandato.version,
        huella,
      };
      if (comentario.trim()) entrada.comentario = comentario.trim();
      return mandatos.aprobar(entrada);
    },
    onSuccess: () => {
      void clienteQuery.invalidateQueries({ queryKey: claves.mandatos(org, ws) });
      alCerrar();
    },
    onError: (e) => {
      if (e instanceof ErrorApi && e.status === 409) {
        setCambio(true);
        void clienteQuery.invalidateQueries({ queryKey: claves.mandato(org, ws, mandato.id) });
      }
    },
  });

  const enviar = (e: FormEvent) => {
    e.preventDefault();
    if (!huellaValida) return;
    setCambio(false);
    aprobar.mutate();
  };

  return (
    <Dialog
      abierto
      alCerrar={alCerrar}
      titulo={renovando ? `Renovar el mandato ${mandato.id}` : `Aprobar el mandato ${mandato.id}`}
      descripcion="Lee el contenido íntegro: lo que apruebas es exactamente esto."
      className="max-w-2xl"
    >
      <form onSubmit={enviar} className="flex flex-col gap-4">
        <Aviso tono="aviso">
          <p className="font-medium">Qué autorizas</p>
          <p className="mt-1">
            Las unidades de este mandato podrán trabajar en modo <strong>{c.modo}</strong> <strong>sin checkpoints humanos</strong> hasta que
            caduque la aprobación ({c.limites.vigencia_horas} h desde ahora), dentro de estos límites: hasta {c.limites.max_unidades} unidades en{" "}
            {c.limites.repositorios.join(", ")}
            {c.limites.rutas_permitidas.length > 0 ? `, solo en ${c.limites.rutas_permitidas.length} ruta(s) permitida(s)` : ""}
            {topes.length > 0 ? `, con un presupuesto total de ${topes.map((t) => `${t.etiqueta.toLowerCase()} ${t.tope}`).join(", ")}` : ", sin tope de presupuesto total"}
            . Los gates siguen escalando y una parada tipificada detiene el trabajo.
          </p>
        </Aviso>

        <div className="max-h-72 overflow-y-auto rounded-md border border-borde p-3">
          <ContenidoMandato contenido={c} />
        </div>

        <div>
          <p className="text-xs font-medium text-suave">Huella que se aprueba (SHA-256 del contenido, calculada por el servidor)</p>
          <p className="break-all font-mono text-xs" data-testid="huella-mandato">
            {huella || "—"}
          </p>
          <p className="text-xs text-suave">Versión {mandato.version}. Si el contenido cambia antes de aprobar, el servidor rechaza la aprobación.</p>
        </div>

        {!huellaValida ? <Aviso tono="aviso">El servidor no devolvió la huella del contenido: no se puede aprobar. Recarga la página.</Aviso> : null}

        <Campo etiqueta="Comentario" htmlFor="comentario-aprobacion" ayuda="Opcional; queda en la historia de aprobaciones.">
          <Textarea id="comentario-aprobacion" rows={2} maxLength={2000} value={comentario} onChange={(e) => setComentario(e.target.value)} />
        </Campo>

        {cambio ? (
          <Aviso tono="aviso">
            El mandato cambió mientras lo revisabas; ya se recargó. Lee de nuevo el contenido y la huella antes de aprobar.
          </Aviso>
        ) : null}
        {aprobar.isError && !cambio ? <ErrorVista error={aprobar.error} /> : null}

        <div className="flex justify-end gap-2">
          <Button variante="secundario" onClick={alCerrar}>
            Cancelar
          </Button>
          <Button type="submit" disabled={!huellaValida || aprobar.isPending}>
            {aprobar.isPending ? "Aprobando…" : renovando ? "Renovar mandato" : "Aprobar mandato"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
