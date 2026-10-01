import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { claves, grafo } from "../../api/endpoints";
import type { RefCriterio } from "../../api/tipos";
import { Cargando } from "../../componentes/Estados";
import { ErrorContrato14 } from "../../componentes/SinContrato";

/** Contrato 1.4: criterios de aceptación (de cualquier unidad) que trazan a este símbolo. */
export function SeccionCriterios({ org, ws, simbolo }: { org: string; ws: string; simbolo: string }) {
  const q = useQuery({
    queryKey: [...claves.grafo(org, ws), "trace", simbolo],
    queryFn: () => grafo.consultar({ alcance: { org, workspace: ws }, consulta: { verbo: "trace", simbolo } }),
    retry: false,
  });
  const criterios = (q.data?.resultados ?? []).map((r) => r.ref).filter((r): r is RefCriterio => r.tipo === "criterio");
  return (
    <section aria-label="Criterios">
      <p className="font-medium">Criterios</p>
      {q.isPending ? <Cargando className="p-0" /> : null}
      {q.isError ? <ErrorContrato14 error={q.error} /> : null}
      {q.isSuccess && criterios.length === 0 ? <p className="text-xs text-suave">Ningún criterio traza a este símbolo.</p> : null}
      {criterios.length > 0 ? (
        <ul className="flex flex-col gap-1">
          {criterios.map((c) => (
            <li key={`${c.workspace}/${c.unidad}/${c.criterio}`}>
              <Link
                to="/$org/$ws/unidades/$unidad"
                params={{ org, ws: c.workspace, unidad: c.unidad }}
                className="text-primario underline-offset-2 hover:underline"
              >
                <span className="font-mono text-xs">{c.criterio}</span> · {c.unidad}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
