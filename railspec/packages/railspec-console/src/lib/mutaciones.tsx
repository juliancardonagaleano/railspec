import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { ErrorApi } from "../api/cliente";
import { Aviso, ErrorVista } from "../componentes/Estados";

/**
 * Mutación de escritura con bloqueo optimista: al terminar invalida las claves
 * dadas; ante un 409 también las invalida para recargar la versión vigente.
 */
export function useGuardar<V, R>(fn: (v: V) => Promise<R>, invalidar: QueryKey[], alExito?: (r: R) => void) {
  const clienteQuery = useQueryClient();
  const refrescar = () => invalidar.forEach((k) => void clienteQuery.invalidateQueries({ queryKey: k }));
  return useMutation({
    mutationFn: fn,
    onSuccess: (r) => {
      refrescar();
      alExito?.(r);
    },
    onError: (e) => {
      if (e instanceof ErrorApi && e.status === 409) refrescar();
    },
  });
}

/** Error de guardado: los 409 se explican como conflicto de versión. */
export function ErrorGuardado({ error }: { error: unknown }) {
  if (!error) return null;
  if (error instanceof ErrorApi && error.status === 409 && !error.codigo) {
    return (
      <Aviso tono="aviso">
        Otra persona modificó este registro mientras lo editabas
        {error.versionActual ? ` (versión actual ${error.versionActual})` : ""}. Se recargaron los datos: cierra, revisa y vuelve a
        guardar. <span className="text-xs">({error.detalle})</span>
      </Aviso>
    );
  }
  return <ErrorVista error={error} />;
}

/** Convierte un texto de número opcional en number | null. */
export function numeroOpcional(texto: string): number | null {
  const t = texto.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}
