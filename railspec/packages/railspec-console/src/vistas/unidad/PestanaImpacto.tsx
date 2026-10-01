import { useQuery } from "@tanstack/react-query";
import { claves, grafo } from "../../api/endpoints";
import type { RefSimbolo, ResultadoGrafo } from "../../api/tipos";
import { EnlaceSimbolo } from "../../componentes/EnlaceSimbolo";
import { Cargando, Vacio } from "../../componentes/Estados";
import { EtiquetaRiesgo } from "../../componentes/Etiquetas";
import { ErrorContrato14 } from "../../componentes/SinContrato";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";
import { riesgoMaximo } from "../grafo/modelo";

type ResultadoSimbolo = ResultadoGrafo & { ref: RefSimbolo };
const esSimbolo = (r: ResultadoGrafo): r is ResultadoSimbolo => r.ref.tipo === "simbolo";

/** Separa los símbolos tocados (distancia 0) de los afectados aguas arriba (distancia ≥ 1). */
export function separarImpacto(resultados: ResultadoGrafo[]): { tocados: ResultadoSimbolo[]; afectados: ResultadoSimbolo[] } {
  const simbolos = resultados.filter(esSimbolo);
  return {
    tocados: simbolos.filter((r) => (r.distancia ?? 0) === 0),
    afectados: simbolos.filter((r) => (r.distancia ?? 0) >= 1).sort((a, b) => (a.distancia ?? 0) - (b.distancia ?? 0)),
  };
}

function TablaSimbolos({ filas, org, ws, conDistancia }: { filas: ResultadoSimbolo[]; org: string; ws: string; conDistancia: boolean }) {
  if (filas.length === 0) return <p className="text-sm text-suave">Ninguno.</p>;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Símbolo</TableHead>
          <TableHead>Tipo</TableHead>
          <TableHead>Repositorio / ruta</TableHead>
          {conDistancia ? (
            <>
              <TableHead className="text-right">Distancia</TableHead>
              <TableHead>Relación</TableHead>
            </>
          ) : null}
        </TableRow>
      </TableHeader>
      <TableBody>
        {filas.map((r) => (
          <TableRow key={`${r.ref.simbolo}-${r.distancia ?? 0}-${r.relacion ?? ""}`}>
            <TableCell>
              <EnlaceSimbolo org={org} ws={ws} simbolo={r.ref.simbolo} nombre={r.ref.nombre} repositorio={r.ref.repositorio} ruta={r.ref.ruta} />
            </TableCell>
            <TableCell>{r.ref.tipo_simbolo}</TableCell>
            <TableCell className="font-mono text-xs">
              {r.ref.repositorio}/{r.ref.ruta}
            </TableCell>
            {conDistancia ? (
              <>
                <TableCell className="text-right">{r.distancia}</TableCell>
                <TableCell>{r.relacion ?? "—"}</TableCell>
              </>
            ) : null}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function PestanaImpacto({ org, ws, unidad }: { org: string; ws: string; unidad: string }) {
  const q = useQuery({
    queryKey: [...claves.unidad(org, ws, unidad), "impacto"],
    queryFn: () => grafo.consultar({ alcance: { org, workspace: ws }, unidad, consulta: { verbo: "impact", profundidad: 3 }, limite: 200 }),
    retry: false,
  });
  if (q.isPending) return <Cargando texto="Calculando impacto…" />;
  if (q.isError) return <ErrorContrato14 error={q.error} reintentar={() => void q.refetch()} />;
  const { tocados, afectados } = separarImpacto(q.data.resultados);
  if (tocados.length === 0 && afectados.length === 0) return <Vacio titulo="La unidad aún no toca símbolos del grafo" />;
  const riesgo = riesgoMaximo(q.data.resultados.map((r) => r.riesgo));
  return (
    <div className="flex flex-col gap-4">
      <p className="flex items-center gap-2 text-sm">
        Riesgo de impacto: {riesgo ? <EtiquetaRiesgo riesgo={riesgo} /> : "—"}
        {q.data.truncado ? <span className="text-xs text-suave">(resultados truncados)</span> : null}
      </p>
      <section>
        <h3 className="mb-1 text-sm font-semibold">Símbolos tocados ({tocados.length})</h3>
        <TablaSimbolos filas={tocados} org={org} ws={ws} conDistancia={false} />
      </section>
      <section>
        <h3 className="mb-1 text-sm font-semibold">Afectados aguas arriba ({afectados.length})</h3>
        <TablaSimbolos filas={afectados} org={org} ws={ws} conDistancia />
      </section>
    </div>
  );
}
