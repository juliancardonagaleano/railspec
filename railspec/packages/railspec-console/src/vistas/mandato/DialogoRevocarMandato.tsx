import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ErrorApi } from "../../api/cliente";
import { claves, mandatos } from "../../api/endpoints";
import type { Mandato } from "../../api/tipos";
import { Aviso, ErrorVista } from "../../componentes/Estados";
import { Button } from "../../componentes/ui/button";
import { Dialog } from "../../componentes/ui/dialog";
import { Campo, Textarea } from "../../componentes/ui/input";

/** Cierra el mandato para siempre (`mandate.revoke`): el motivo es obligatorio y las unidades quedan detenidas. */
export function DialogoRevocarMandato({ mandato, org, ws, alCerrar }: { mandato: Mandato; org: string; ws: string; alCerrar: () => void }) {
  const clienteQuery = useQueryClient();
  const [motivo, setMotivo] = useState("");
  const [cambio, setCambio] = useState(false);
  const revocar = useMutation({
    mutationFn: () => mandatos.revocar({ alcance: mandato.alcance, id: mandato.id, version_vista: mandato.version, motivo: motivo.trim() }),
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
    if (!motivo.trim()) return;
    setCambio(false);
    revocar.mutate();
  };
  return (
    <Dialog
      abierto
      alCerrar={alCerrar}
      titulo={`Revocar el mandato ${mandato.id}`}
      descripcion="Detener es siempre más seguro que seguir, pero es definitivo: un mandato revocado no se reabre, se redacta otro."
    >
      <form onSubmit={enviar} className="flex flex-col gap-3">
        <Aviso tono="aviso">Las unidades que ampara quedan detenidas hasta que haya otro mandato con ese id de plan o bajes su autonomía.</Aviso>
        <Campo etiqueta="Motivo" htmlFor="motivo-revocacion">
          <Textarea id="motivo-revocacion" required rows={3} maxLength={2000} value={motivo} onChange={(e) => setMotivo(e.target.value)} />
        </Campo>
        {cambio ? <Aviso tono="aviso">El mandato cambió mientras lo revisabas; ya se recargó. Revísalo y vuelve a intentarlo.</Aviso> : null}
        {revocar.isError && !cambio ? <ErrorVista error={revocar.error} /> : null}
        <div className="flex justify-end gap-2">
          <Button variante="secundario" onClick={alCerrar}>
            Cancelar
          </Button>
          <Button type="submit" variante="peligro" disabled={!motivo.trim() || revocar.isPending}>
            {revocar.isPending ? "Revocando…" : "Revocar definitivamente"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
