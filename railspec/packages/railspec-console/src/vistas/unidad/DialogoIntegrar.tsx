import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { claves, unidades } from "../../api/endpoints";
import type { AlcanceUnidad, CommitIntegrable } from "../../api/tipos";
import { ErrorVista } from "../../componentes/Estados";
import { Button } from "../../componentes/ui/button";
import { Dialog } from "../../componentes/ui/dialog";
import { Campo, Input } from "../../componentes/ui/input";
import { PATRON_SHA } from "./nuevaUnidad";

/** Qué le dice el diálogo a quien integra sobre el commit, según lo que el servidor pudo sugerir. */
function ayudaCommit(sugerencia: CommitIntegrable | undefined, cargando: boolean, error: boolean, sugerido: boolean) {
  const efecto =
    "Con él, el grafo conserva el código de la unidad hasta que el índice canónico llegue a ese commit.";
  if (cargando) return "Consultando la punta de la rama por defecto en el servidor…";
  if (sugerido && sugerencia?.commit) {
    const indexado = sugerencia.commit_indexado === sugerencia.commit ? " El índice del grafo ya está en este commit." : "";
    return `Sugerido: la punta de ${sugerencia.rama ?? "la rama por defecto"} en el clon del servidor. Confírmala solo si ya incluye el merge de la unidad; si el clon va por detrás, pega el sha del merge o squash.${indexado} ${efecto}`;
  }
  const porque = error
    ? "No se pudo consultar la sugerencia del servidor."
    : sugerencia?.motivo === "sin-clones" || sugerencia?.motivo === "sin-clon"
      ? "El servidor no tiene clon de este repositorio, así que no sugiere un commit."
      : "";
  return `${porque} Sha completo del commit resultante en la rama por defecto (el del merge o squash). ${efecto}`.trim();
}

export function DialogoIntegrar({ alcance, abierto, alCerrar }: { alcance: AlcanceUnidad; abierto: boolean; alCerrar: () => void }) {
  const clienteQuery = useQueryClient();
  const [especificacion, setEspecificacion] = useState("");
  const [prUrl, setPrUrl] = useState("");
  // `null`: la persona no lo ha tocado y vale la sugerencia del servidor (punta de la rama por defecto).
  const [commit, setCommit] = useState<string | null>(null);
  const [sinGrafo, setSinGrafo] = useState(false);
  const sugerencia = useQuery({
    queryKey: [...claves.unidad(alcance.org, alcance.workspace, alcance.unidad), "commit-integrable"],
    queryFn: () => unidades.commitIntegrable(alcance.org, alcance.workspace, alcance.unidad),
    enabled: abierto,
  });
  const valor = (commit ?? sugerencia.data?.commit ?? "").trim().toLowerCase();
  const integrar = useMutation({
    mutationFn: () => {
      const entrada: Parameters<typeof unidades.integrar>[0] = { unidad: alcance, especificacion_viva: especificacion.trim() };
      if (prUrl.trim()) entrada.pr_url = prUrl.trim();
      if (valor) entrada.commit_integrado = valor;
      return unidades.integrar(entrada);
    },
    onSuccess: () => {
      void clienteQuery.invalidateQueries({ queryKey: claves.unidad(alcance.org, alcance.workspace, alcance.unidad) });
      alCerrar();
    },
  });
  const prValida = !prUrl.trim() || prUrl.trim().startsWith("https://");
  const commitValido = !valor || PATRON_SHA.test(valor);
  // Sin commit el grafo descarta el código de la unidad al integrar: hay que elegirlo a propósito.
  const eleccionCommit = Boolean(valor) || sinGrafo;
  const puedeEnviar = Boolean(especificacion.trim()) && prValida && commitValido && eleccionCommit && !integrar.isPending;
  const enviar = (e: FormEvent) => {
    e.preventDefault();
    if (puedeEnviar) integrar.mutate();
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
        <Campo
          etiqueta="Commit integrado"
          htmlFor="commit-integrado"
          ayuda={ayudaCommit(sugerencia.data, sugerencia.isPending && abierto, sugerencia.isError, commit === null)}
        >
          <Input
            id="commit-integrado"
            placeholder="40 caracteres hexadecimales"
            maxLength={40}
            spellCheck={false}
            className="font-mono"
            value={commit ?? sugerencia.data?.commit ?? ""}
            aria-invalid={!commitValido}
            onChange={(e) => setCommit(e.target.value)}
          />
        </Campo>
        {!valor ? (
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={sinGrafo} onChange={(e) => setSinGrafo(e.target.checked)} />
            <span>
              Integrar sin conservar el grafo de la unidad
              <span className="block text-xs text-suave">
                El grafo descarta el código de la unidad al integrar y las consultas dejan de verlo hasta que el índice
                canónico incorpore el merge.
              </span>
            </span>
          </label>
        ) : null}
        {!commitValido ? <p className="text-sm text-peligro">El commit debe ser un sha completo (40 caracteres hexadecimales).</p> : null}
        {integrar.isError ? <ErrorVista error={integrar.error} /> : null}
        <div className="flex justify-end gap-2">
          <Button variante="secundario" onClick={alCerrar}>
            Cancelar
          </Button>
          <Button type="submit" disabled={!puedeEnviar}>
            {integrar.isPending ? "Integrando…" : "Integrar"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
