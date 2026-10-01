import { useState, type FormEvent } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { auditoria, claves } from "../../api/endpoints";
import { EVENTOS_AUDITORIA, type FiltrosAuditoria, type RegistroAuditoria } from "../../api/tipos";
import { Cargando, Encabezado, ErrorVista, Vacio } from "../../componentes/Estados";
import { Badge } from "../../componentes/ui/badge";
import { Button } from "../../componentes/ui/button";
import { Campo, Input } from "../../componentes/ui/input";
import { Select } from "../../componentes/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";
import { useWorkspace } from "../../lib/sesion";
import { fecha, fechaAIso, nombreActor } from "../../lib/utiles";

interface Formulario {
  evento: string;
  repositorio: string;
  unidad: string;
  desde: string;
  hasta: string;
}

const VACIO: Formulario = { evento: "", repositorio: "", unidad: "", desde: "", hasta: "" };

export function aFiltros(f: Formulario): FiltrosAuditoria {
  const r: FiltrosAuditoria = { limite: 50 };
  if (f.evento) r.evento = f.evento;
  if (f.repositorio.trim()) r.repositorio = f.repositorio.trim();
  if (f.unidad.trim()) r.unidad = f.unidad.trim();
  const desde = fechaAIso(f.desde);
  const hasta = fechaAIso(f.hasta, true);
  if (desde) r.desde = desde;
  if (hasta) r.hasta = hasta;
  return r;
}

function Detalle({ r }: { r: RegistroAuditoria }) {
  const extra = [r.nivel_codigo && `nivel ${r.nivel_codigo}`, r.proveedor, r.modelo, r.region, r.conversacion && `conv. ${r.conversacion.slice(0, 8)}`]
    .filter(Boolean)
    .join(" · ");
  const claves = Object.keys(r.detalle ?? {});
  return (
    <div className="text-xs">
      {extra ? <p>{extra}</p> : null}
      {r.sha256_enviado ? <p className="font-mono text-suave">sha256 {r.sha256_enviado.slice(0, 16)}…</p> : null}
      {claves.length > 0 ? (
        <details>
          <summary className="cursor-pointer text-suave">detalle ({claves.length})</summary>
          <pre className="mt-1 max-w-md overflow-x-auto whitespace-pre-wrap rounded bg-fondo p-2">{JSON.stringify(r.detalle, null, 2)}</pre>
        </details>
      ) : null}
    </div>
  );
}

export function Auditoria() {
  const { org, ws } = useWorkspace();
  const [borrador, setBorrador] = useState<Formulario>(VACIO);
  const [filtros, setFiltros] = useState<FiltrosAuditoria>(aFiltros(VACIO));

  const consulta = useInfiniteQuery({
    queryKey: [...claves.auditoria(org, ws), filtros],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => auditoria.listar(org, ws, pageParam ? { ...filtros, cursor: pageParam } : filtros),
    getNextPageParam: (p) => p.cursor_siguiente ?? undefined,
  });
  const registros = consulta.data?.pages.flatMap((p) => p.registros) ?? [];

  const aplicar = (e: FormEvent) => {
    e.preventDefault();
    setFiltros(aFiltros(borrador));
  };
  const campo = (k: keyof Formulario) => ({
    value: borrador[k],
    onChange: (e: { target: { value: string } }) => setBorrador((b) => ({ ...b, [k]: e.target.value })),
  });

  return (
    <>
      <Encabezado titulo="Auditoría" descripcion="Registro inmutable de lo que pasó en el workspace (más recientes primero)." />
      <form onSubmit={aplicar} className="mb-4 grid gap-3 sm:grid-cols-3 lg:grid-cols-6" aria-label="Filtros de auditoría">
        <Campo etiqueta="Evento" htmlFor="aud-evento">
          <Select id="aud-evento" vacio="Todos" opciones={EVENTOS_AUDITORIA.map((e) => ({ valor: e, etiqueta: e }))} {...campo("evento")} />
        </Campo>
        <Campo etiqueta="Repositorio" htmlFor="aud-repo">
          <Input id="aud-repo" {...campo("repositorio")} />
        </Campo>
        <Campo etiqueta="Unidad" htmlFor="aud-unidad">
          <Input id="aud-unidad" placeholder="0001-slug" {...campo("unidad")} />
        </Campo>
        <Campo etiqueta="Desde" htmlFor="aud-desde">
          <Input id="aud-desde" type="date" {...campo("desde")} />
        </Campo>
        <Campo etiqueta="Hasta" htmlFor="aud-hasta">
          <Input id="aud-hasta" type="date" {...campo("hasta")} />
        </Campo>
        <div className="flex items-end gap-2">
          <Button type="submit">Filtrar</Button>
          <Button
            variante="fantasma"
            onClick={() => {
              setBorrador(VACIO);
              setFiltros(aFiltros(VACIO));
            }}
          >
            Limpiar
          </Button>
        </div>
      </form>
      {consulta.isPending ? <Cargando texto="Cargando registros…" /> : null}
      {consulta.isError ? <ErrorVista error={consulta.error} reintentar={() => void consulta.refetch()} /> : null}
      {consulta.isSuccess && registros.length === 0 ? <Vacio titulo="No hay registros con estos filtros" /> : null}
      {registros.length > 0 ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Fecha</TableHead>
              <TableHead>Evento</TableHead>
              <TableHead>Actor</TableHead>
              <TableHead>Repositorio</TableHead>
              <TableHead>Unidad</TableHead>
              <TableHead>Detalle</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {registros.map((r) => (
              <TableRow key={r.id}>
                <TableCell className="whitespace-nowrap">{fecha(r.en)}</TableCell>
                <TableCell>
                  <Badge tono="info">{r.evento}</Badge>
                </TableCell>
                <TableCell>
                  {nombreActor(r.actor)} <span className="text-xs text-suave">({r.actor.canal})</span>
                </TableCell>
                <TableCell>{r.repositorio ?? "—"}</TableCell>
                <TableCell className="font-mono text-xs">{r.unidad ?? "—"}</TableCell>
                <TableCell>
                  <Detalle r={r} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
      {consulta.hasNextPage ? (
        <Button variante="secundario" className="mt-4" onClick={() => void consulta.fetchNextPage()} disabled={consulta.isFetchingNextPage}>
          {consulta.isFetchingNextPage ? "Cargando…" : "Cargar más"}
        </Button>
      ) : null}
    </>
  );
}
