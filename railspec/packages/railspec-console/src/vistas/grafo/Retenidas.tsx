import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { claves, grafo } from "../../api/endpoints";
import type { RetenidaGrafo } from "../../api/tipos";
import { Aviso, Cargando, Encabezado, ErrorVista, Vacio } from "../../componentes/Estados";
import { Badge } from "../../componentes/ui/badge";
import { Button } from "../../componentes/ui/button";
import { Campo } from "../../componentes/ui/input";
import { Select } from "../../componentes/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";
import { useWorkspace } from "../../lib/sesion";
import { commitCorto, fecha } from "../../lib/utiles";

// Superposiciones retenidas del grafo: unidades ya integradas cuyo código espera a que un índice cubra su
// commit. Una que lleva mucho sin salir suele ser un commit que nunca llegó a la rama por defecto: aquí se ve
// antes de que el servidor la retire por plazo (`RAILSPEC_GRAFO_RETENIDAS_DIAS`).

const DIA_MS = 86_400_000;

export interface EstadoRetenida {
  /** `vencida`: la retira el próximo barrido. `por-vencer`: lleva más de la mitad del plazo (como el aviso de `graph.query`). */
  clase: "vencida" | "por-vencer" | "en-plazo" | "sin-plazo" | "sin-dato";
  texto: string;
}

function tiempo(ms: number): string {
  const dias = Math.floor(ms / DIA_MS);
  if (dias >= 1) return `${dias} ${dias === 1 ? "día" : "días"}`;
  const horas = Math.max(1, Math.floor(ms / 3_600_000));
  return `${horas} ${horas === 1 ? "hora" : "horas"}`;
}

/** Qué le falta a una retenida para salir. El plazo total es `vence - desde`; no hace falta conocer la variable. */
export function estadoRetenida(r: RetenidaGrafo, ahora: number): EstadoRetenida {
  const vence = r.vence ? Date.parse(r.vence) : NaN;
  if (Number.isNaN(vence)) {
    return r.desde
      ? { clase: "sin-plazo", texto: "sin plazo: solo la retira un índice que cubra el commit" }
      : { clase: "sin-dato", texto: "aún sin fecha de retención: el próximo barrido la sella" };
  }
  if (vence <= ahora) return { clase: "vencida", texto: "vencida: la retira el próximo barrido" };
  const desde = r.desde ? Date.parse(r.desde) : NaN;
  const resta = vence - ahora;
  const texto = `vence en ${tiempo(resta)}`;
  const pasada = !Number.isNaN(desde) && ahora - desde > (vence - desde) / 2;
  return { clase: pasada ? "por-vencer" : "en-plazo", texto };
}

const ORDEN: Record<EstadoRetenida["clase"], number> = {
  vencida: 0,
  "por-vencer": 1,
  "en-plazo": 2,
  "sin-dato": 3,
  "sin-plazo": 4,
};

/** Las más urgentes primero: vencidas, luego por fecha de vencimiento; sin plazo al final. */
export function ordenarRetenidas(lista: RetenidaGrafo[], ahora: number): RetenidaGrafo[] {
  const clave = (r: RetenidaGrafo) => [ORDEN[estadoRetenida(r, ahora).clase], r.vence ? Date.parse(r.vence) : 0] as const;
  return [...lista].sort((a, b) => {
    const [ca, va] = clave(a);
    const [cb, vb] = clave(b);
    return ca - cb || va - vb || a.repositorio.localeCompare(b.repositorio) || a.unidad.localeCompare(b.unidad);
  });
}

const TONO: Record<EstadoRetenida["clase"], "peligro" | "aviso" | "info" | "neutro"> = {
  vencida: "peligro",
  "por-vencer": "aviso",
  "en-plazo": "info",
  "sin-plazo": "neutro",
  "sin-dato": "neutro",
};

export function Retenidas() {
  const { org, ws } = useWorkspace();
  const [repositorio, setRepositorio] = useState("");
  const consulta = useQuery({ queryKey: claves.retenidas(org, ws), queryFn: () => grafo.retenidas(org, ws) });
  const ahora = consulta.dataUpdatedAt || Date.now();
  const todas = consulta.data ?? [];
  const repositorios = useMemo(() => [...new Set(todas.map((r) => r.repositorio))].sort(), [todas]);
  const visibles = useMemo(
    () => ordenarRetenidas(todas.filter((r) => !repositorio || r.repositorio === repositorio), ahora),
    [todas, repositorio, ahora],
  );
  const vencidas = visibles.filter((r) => estadoRetenida(r, ahora).clase === "vencida").length;

  return (
    <>
      <Encabezado
        titulo="Superposiciones retenidas del grafo"
        descripcion="Unidades ya integradas cuyo código espera a que un índice del grafo cubra su commit. Solo unidad y commit: nunca código."
        acciones={
          <Button variante="secundario" tamano="pequeno" onClick={() => void consulta.refetch()} disabled={consulta.isFetching}>
            {consulta.isFetching ? "Actualizando…" : "Actualizar"}
          </Button>
        }
      />
      {consulta.isPending ? <Cargando texto="Cargando retenidas…" /> : null}
      {consulta.isError ? <ErrorVista error={consulta.error} reintentar={() => void consulta.refetch()} /> : null}
      {consulta.isSuccess && todas.length === 0 ? (
        <Vacio titulo="No hay superposiciones retenidas">
          <p className="text-sm text-suave">
            Cada unidad integrada ya la cubrió un índice del grafo. Si el workspace no tiene grafo configurado, tampoco hay retenidas.
          </p>
        </Vacio>
      ) : null}
      {todas.length > 0 ? (
        <>
          <Aviso tono={vencidas > 0 ? "aviso" : "info"} className="mb-4">
            {visibles.length} {visibles.length === 1 ? "retenida" : "retenidas"}
            {vencidas > 0 ? `, ${vencidas} vencida${vencidas === 1 ? "" : "s"} (las retira el barrido del servidor, cada hora)` : ""}. Un
            índice que cubra el commit de la unidad la retira antes; si el commit nunca llegó a la rama por defecto, el plazo la retira sola.
          </Aviso>
          {repositorios.length > 1 ? (
            <div className="mb-4 max-w-xs">
              <Campo etiqueta="Repositorio" htmlFor="ret-repo">
                <Select
                  id="ret-repo"
                  vacio="Todos"
                  opciones={repositorios.map((r) => ({ valor: r, etiqueta: r }))}
                  value={repositorio}
                  onChange={(e) => setRepositorio(e.target.value)}
                />
              </Campo>
            </div>
          ) : null}
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Unidad</TableHead>
                <TableHead>Repositorio</TableHead>
                <TableHead>Integrada en</TableHead>
                <TableHead>Retenida desde</TableHead>
                <TableHead>Estado</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {visibles.map((r) => {
                const e = estadoRetenida(r, ahora);
                return (
                  <TableRow key={`${r.repositorio}/${r.unidad}`}>
                    <TableCell className="font-mono text-xs">
                      <Link to="/$org/$ws/unidades/$unidad" params={{ org, ws, unidad: r.unidad }} className="text-primario underline">
                        {r.unidad}
                      </Link>
                    </TableCell>
                    <TableCell>{r.repositorio}</TableCell>
                    <TableCell className="font-mono text-xs" title={r.integrado}>
                      {commitCorto(r.integrado)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">{fecha(r.desde)}</TableCell>
                    <TableCell>
                      <Badge tono={TONO[e.clase]}>{e.texto}</Badge>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </>
      ) : null}
    </>
  );
}
