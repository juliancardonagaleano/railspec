import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { claves, unidades } from "../../api/endpoints";
import type { AlcanceUnidad } from "../../api/tipos";
import { ErrorVista } from "../../componentes/Estados";
import { Button } from "../../componentes/ui/button";
import { Dialog } from "../../componentes/ui/dialog";
import { Campo, Input } from "../../componentes/ui/input";

export function DialogoIntegrar({ alcance, abierto, alCerrar }: { alcance: AlcanceUnidad; abierto: boolean; alCerrar: () => void }) {
  const clienteQuery = useQueryClient();
  const [especificacion, setEspecificacion] = useState("");
  const [prUrl, setPrUrl] = useState("");
  const integrar = useMutation({
    mutationFn: () => {
      const entrada: Parameters<typeof unidades.integrar>[0] = { unidad: alcance, especificacion_viva: especificacion.trim() };
      if (prUrl.trim()) entrada.pr_url = prUrl.trim();
      return unidades.integrar(entrada);
    },
    onSuccess: () => {
      void clienteQuery.invalidateQueries({ queryKey: claves.unidad(alcance.org, alcance.workspace, alcance.unidad) });
      alCerrar();
    },
  });
  const prValida = !prUrl.trim() || prUrl.trim().startsWith("https://");
  const enviar = (e: FormEvent) => {
    e.preventDefault();
    if (especificacion.trim() && prValida) integrar.mutate();
  };
  return (
    <Dialog
      abierto={abierto}
      alCerrar={alCerrar}
      titulo="Integrar unidad"
      descripcion="Registra dónde quedó consolidada la especificación viva. No cambia el cierre de la unidad."
    >
      <form onSubmit={enviar} className="flex flex-col gap-3">
        <Campo etiqueta="Especificación viva" htmlFor="esp-viva" ayuda="Ruta o id donde quedó la spec consolidada.">
          <Input id="esp-viva" required maxLength={512} value={especificacion} onChange={(e) => setEspecificacion(e.target.value)} />
        </Campo>
        <Campo etiqueta="URL del PR (opcional)" htmlFor="pr-url">
          <Input
            id="pr-url"
            type="url"
            placeholder="https://…"
            maxLength={512}
            value={prUrl}
            aria-invalid={!prValida}
            onChange={(e) => setPrUrl(e.target.value)}
          />
        </Campo>
        {!prValida ? <p className="text-sm text-peligro">La URL debe empezar por https://</p> : null}
        {integrar.isError ? <ErrorVista error={integrar.error} /> : null}
        <div className="flex justify-end gap-2">
          <Button variante="secundario" onClick={alCerrar}>
            Cancelar
          </Button>
          <Button type="submit" disabled={!especificacion.trim() || !prValida || integrar.isPending}>
            {integrar.isPending ? "Integrando…" : "Integrar"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
