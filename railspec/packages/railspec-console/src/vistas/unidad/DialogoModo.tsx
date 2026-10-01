import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ErrorApi } from "../../api/cliente";
import { claves, unidades } from "../../api/endpoints";
import { MODOS, type EstadoUnidad, type Modo } from "../../api/tipos";
import { ErrorVista } from "../../componentes/Estados";
import { Button } from "../../componentes/ui/button";
import { Dialog } from "../../componentes/ui/dialog";
import { Campo, Textarea } from "../../componentes/ui/input";
import { Select } from "../../componentes/ui/select";

export function DialogoModo({ estado, abierto, alCerrar }: { estado: EstadoUnidad; abierto: boolean; alCerrar: () => void }) {
  const clienteQuery = useQueryClient();
  const [modo, setModo] = useState<Modo>(estado.modo);
  const [motivo, setMotivo] = useState("");
  const { org, workspace, unidad } = estado.unidad;
  const cambiar = useMutation({
    mutationFn: () =>
      unidades.fijarModo({ unidad: { org, workspace, unidad }, modo, motivo: motivo.trim(), version_vista: estado.version }),
    onSuccess: () => {
      void clienteQuery.invalidateQueries({ queryKey: claves.unidad(org, workspace, unidad) });
      setMotivo("");
      alCerrar();
    },
    onError: (e) => {
      if (e instanceof ErrorApi && e.status === 409) void clienteQuery.invalidateQueries({ queryKey: claves.unidad(org, workspace, unidad) });
    },
  });
  const enviar = (e: FormEvent) => {
    e.preventDefault();
    if (motivo.trim() && modo !== estado.modo) cambiar.mutate();
  };
  return (
    <Dialog
      abierto={abierto}
      alCerrar={alCerrar}
      titulo="Cambiar modo"
      descripcion="El servidor solo admite el cambio tras research o tras el checkpoint del spec; queda registrado con su motivo."
    >
      <form onSubmit={enviar} className="flex flex-col gap-3">
        <Campo etiqueta="Modo" htmlFor="modo-nuevo">
          <Select
            id="modo-nuevo"
            value={modo}
            onChange={(e) => setModo(e.target.value as Modo)}
            opciones={MODOS.map((m) => ({ valor: m, etiqueta: m === estado.modo ? `${m} (actual)` : m }))}
          />
        </Campo>
        <Campo etiqueta="Motivo" htmlFor="motivo-modo">
          <Textarea id="motivo-modo" required maxLength={2000} value={motivo} onChange={(e) => setMotivo(e.target.value)} />
        </Campo>
        {cambiar.isError ? <ErrorVista error={cambiar.error} /> : null}
        {cambiar.error instanceof ErrorApi && cambiar.error.status === 409 ? (
          <p className="text-sm text-suave">El estado cambió mientras editabas; ya se recargó. Revisa y vuelve a intentarlo.</p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button variante="secundario" onClick={alCerrar}>
            Cancelar
          </Button>
          <Button type="submit" disabled={!motivo.trim() || modo === estado.modo || cambiar.isPending}>
            {cambiar.isPending ? "Guardando…" : "Cambiar modo"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
