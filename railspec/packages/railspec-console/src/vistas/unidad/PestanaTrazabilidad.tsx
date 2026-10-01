import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { claves, grafo } from "../../api/endpoints";
import type { RefSimbolo, TareaTraza, Trazabilidad } from "../../api/tipos";
import { EnlaceSimbolo } from "../../componentes/EnlaceSimbolo";
import { Cargando, Vacio } from "../../componentes/Estados";
import { ErrorContrato14 } from "../../componentes/SinContrato";
import { Button } from "../../componentes/ui/button";
import { EtiquetaSeveridad } from "../../componentes/Etiquetas";
import { Badge } from "../../componentes/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";

function Tareas({ tareas }: { tareas: TareaTraza[] }) {
  if (tareas.length === 0) return <span className="text-suave">—</span>;
  return (
    <ul className="flex flex-col gap-1">
      {tareas.map((t) => (
        <li key={t.id}>
          <span className="font-mono text-xs">{t.id}</span> {t.completada ? "✓" : "○"} {t.descripcion}
          {t.grupo ? <span className="text-xs text-suave"> ({t.grupo})</span> : null}
        </li>
      ))}
    </ul>
  );
}

/** Contrato 1.4: símbolos del grafo trazados a un criterio (verbo `trace`), bajo demanda. */
function SimbolosDelCriterio({ org, ws, unidad, criterio }: { org: string; ws: string; unidad: string; criterio: string }) {
  const [visible, setVisible] = useState(false);
  const q = useQuery({
    queryKey: [...claves.unidad(org, ws, unidad), "trace", criterio],
    queryFn: () => grafo.consultar({ alcance: { org, workspace: ws }, unidad, consulta: { verbo: "trace", criterio } }),
    enabled: visible,
    retry: false,
  });
  if (!visible)
    return (
      <Button variante="enlace" tamano="pequeno" onClick={() => setVisible(true)}>
        ver símbolos en el grafo
      </Button>
    );
  if (q.isPending) return <Cargando texto="Buscando…" className="p-0" />;
  if (q.isError) return <ErrorContrato14 error={q.error} />;
  const simbolos = q.data.resultados.map((r) => r.ref).filter((r): r is RefSimbolo => r.tipo === "simbolo");
  if (simbolos.length === 0) return <p className="text-xs text-suave">El grafo no tiene símbolos para {criterio}.</p>;
  return (
    <ul className="mt-1 text-xs">
      {simbolos.map((s) => (
        <li key={s.simbolo}>
          <EnlaceSimbolo org={org} ws={ws} simbolo={s.simbolo} nombre={s.nombre} repositorio={s.repositorio} ruta={s.ruta} />
        </li>
      ))}
    </ul>
  );
}

export function PestanaTrazabilidad({ datos, org, ws, unidad }: { datos: Trazabilidad; org: string; ws: string; unidad: string }) {
  if (datos.criterios.length === 0 && datos.sin_criterio.tareas.length === 0)
    return <Vacio titulo="Aún no hay criterios de aceptación trazados" />;
  return (
    <div className="flex flex-col gap-4">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Criterio</TableHead>
            <TableHead>Tareas</TableHead>
            <TableHead>Archivos</TableHead>
            <TableHead>Símbolos</TableHead>
            <TableHead>Hallazgos</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {datos.criterios.map((c) => (
            <TableRow key={c.id}>
              <TableCell className="max-w-64">
                <p className="font-mono text-xs font-semibold">{c.id}</p>
                <p>{c.texto}</p>
              </TableCell>
              <TableCell>
                <Tareas tareas={c.tareas} />
              </TableCell>
              <TableCell>
                {c.archivos.length === 0 ? (
                  <span className="text-suave">—</span>
                ) : (
                  <ul>
                    {c.archivos.map((a) => (
                      <li key={`${a.repositorio}:${a.ruta}`} className="font-mono text-xs">
                        {a.repositorio}/{a.ruta} <span className="text-suave">({a.estado})</span>
                      </li>
                    ))}
                  </ul>
                )}
              </TableCell>
              <TableCell>
                {c.simbolos.length === 0 ? (
                  <span className="text-suave">—</span>
                ) : (
                  <ul>
                    {c.simbolos.map((s) => (
                      <li key={s.simbolo}>
                        <EnlaceSimbolo org={org} ws={ws} simbolo={s.simbolo} nombre={s.nombre} repositorio={s.repositorio} ruta={s.ruta} />{" "}
                        <span className="text-xs text-suave">{s.tipo}</span>
                      </li>
                    ))}
                  </ul>
                )}
                <SimbolosDelCriterio org={org} ws={ws} unidad={unidad} criterio={c.id} />
              </TableCell>
              <TableCell>
                {c.hallazgos.length === 0 ? (
                  <span className="text-suave">—</span>
                ) : (
                  <ul className="flex flex-col gap-1">
                    {c.hallazgos.map((h) => (
                      <li key={`${h.gate}-${h.id}`} className={h.refutado ? "opacity-60" : undefined}>
                        <EtiquetaSeveridad severidad={h.severidad} /> <span className="font-mono text-xs">{h.id}</span> {h.titulo}{" "}
                        <span className="text-xs text-suave">(gate {h.gate})</span>
                        {h.refutado ? <Badge className="ml-1">refutado</Badge> : null}
                      </li>
                    ))}
                  </ul>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {datos.sin_criterio.tareas.length > 0 ? (
        <section>
          <h3 className="mb-1 text-sm font-semibold">Tareas sin criterio</h3>
          <Tareas tareas={datos.sin_criterio.tareas} />
        </section>
      ) : null}
    </div>
  );
}
