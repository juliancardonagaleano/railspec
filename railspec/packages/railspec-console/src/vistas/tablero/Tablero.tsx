import { useMemo } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { claves, grafo, unidades } from "../../api/endpoints";
import { ESTADOS_FASE, FASES, type EstadoFase, type ResumenUnidad, type UnitListEntrada } from "../../api/tipos";
import { Cargando, Encabezado, ErrorVista, Vacio } from "../../componentes/Estados";
import { EtiquetaEstado, EtiquetaModo, EtiquetaRiesgo, NOMBRE_FASE } from "../../componentes/Etiquetas";
import { Badge } from "../../componentes/ui/badge";
import { Button } from "../../componentes/ui/button";
import { Select } from "../../componentes/ui/select";
import { useWorkspace } from "../../lib/sesion";
import { fecha } from "../../lib/utiles";
import type { BusquedaTablero } from "../../router";
import { agruparPorFase, unirPaginas } from "./agrupar";

function TarjetaUnidad({ u, org, ws }: { u: ResumenUnidad; org: string; ws: string }) {
  return (
    <li>
      <Link
        to="/$org/$ws/unidades/$unidad"
        params={{ org, ws, unidad: u.unidad }}
        className="block rounded-md border border-borde bg-superficie p-3 text-sm shadow-sm hover:border-primario focus-visible:outline-2 focus-visible:outline-primario"
      >
        <p className="font-medium leading-snug">{u.titulo}</p>
        <p className="mt-0.5 font-mono text-xs text-suave">{u.unidad}</p>
        <div className="mt-2 flex flex-wrap gap-1">
          <EtiquetaEstado estado={u.estado} />
          <EtiquetaModo modo={u.modo} />
          <EtiquetaRiesgo riesgo={u.riesgo} />
          {u.integrada ? <Badge tono="exito">integrada</Badge> : null}
          {u.checkpoint_pendiente ? <Badge tono="aviso">checkpoint pendiente</Badge> : null}
        </div>
        <p className="mt-2 text-xs text-suave">
          {u.repositorio_primario} · {u.dueno_login ?? "sin dueño"}
        </p>
        <p className="text-xs text-suave">Actualizada {fecha(u.actualizado_en)}</p>
      </Link>
    </li>
  );
}

function Filtros({ busqueda, repos }: { busqueda: BusquedaTablero; repos: string[] }) {
  const navegar = useNavigate();
  const cambiar = (parcial: Partial<BusquedaTablero>) =>
    void navegar({ to: ".", search: (prev: BusquedaTablero) => ({ ...prev, ...parcial }) });
  return (
    <div className="mb-4 grid gap-3 sm:grid-cols-3" role="group" aria-label="Filtros del tablero">
      <div className="flex flex-col gap-1">
        <label htmlFor="filtro-repo" className="text-xs font-medium text-suave">
          Repositorio
        </label>
        <Select
          id="filtro-repo"
          vacio="Todos"
          value={busqueda.repositorio ?? ""}
          opciones={repos.map((r) => ({ valor: r, etiqueta: r }))}
          onChange={(e) => cambiar({ repositorio: e.target.value || undefined })}
        />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor="filtro-estado" className="text-xs font-medium text-suave">
          Estado
        </label>
        <Select
          id="filtro-estado"
          vacio="Todos"
          value={busqueda.estado ?? ""}
          opciones={ESTADOS_FASE.map((e) => ({ valor: e, etiqueta: e }))}
          onChange={(e) => cambiar({ estado: e.target.value || undefined })}
        />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor="filtro-integradas" className="text-xs font-medium text-suave">
          Integración
        </label>
        <Select
          id="filtro-integradas"
          vacio="Todas"
          value={busqueda.integradas ?? ""}
          opciones={[
            { valor: "si", etiqueta: "Solo integradas" },
            { valor: "no", etiqueta: "Sin integrar" },
          ]}
          onChange={(e) => cambiar({ integradas: (e.target.value || undefined) as BusquedaTablero["integradas"] })}
        />
      </div>
    </div>
  );
}

export function Tablero() {
  const { org, ws } = useWorkspace();
  const busqueda = useSearch({ strict: false }) as BusquedaTablero;
  const repos = useQuery({ queryKey: [...claves.grafo(org, ws), "repositorios"], queryFn: () => grafo.repositorios(org, ws) });

  const consulta = useInfiniteQuery({
    queryKey: [...claves.tablero(org, ws), busqueda],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => {
      const entrada: UnitListEntrada = { alcance: { org, workspace: ws }, limite: 100, cursor: pageParam };
      if (busqueda.repositorio) entrada.repositorio = busqueda.repositorio;
      if (busqueda.estado) entrada.estado = [busqueda.estado as EstadoFase];
      if (busqueda.integradas) entrada.integradas = busqueda.integradas === "si";
      return unidades.listar(entrada);
    },
    getNextPageParam: (ultima) => ultima.cursor_siguiente ?? undefined,
  });

  const lista = useMemo(() => unirPaginas(consulta.data?.pages ?? []), [consulta.data]);
  const columnas = useMemo(() => agruparPorFase(lista), [lista]);

  return (
    <>
      <Encabezado titulo="Tablero de unidades" descripcion={`${org} / ${ws}`} />
      <Filtros busqueda={busqueda} repos={(repos.data ?? []).map((r) => r.repositorio)} />
      {consulta.isPending ? <Cargando texto="Cargando unidades…" /> : null}
      {consulta.isError ? <ErrorVista error={consulta.error} reintentar={() => void consulta.refetch()} /> : null}
      {consulta.isSuccess && lista.length === 0 ? (
        <Vacio titulo="No hay unidades con estos filtros">Las unidades nacen en el arnés con unit.start.</Vacio>
      ) : null}
      {consulta.isSuccess && lista.length > 0 ? (
        <div className="flex gap-3 overflow-x-auto pb-2" aria-label="Columnas por fase">
          {FASES.map((f) => (
            <section key={f} aria-labelledby={`col-${f}`} className="flex w-64 shrink-0 flex-col rounded-lg bg-fondo p-2">
              <h2 id={`col-${f}`} className="mb-2 flex items-center justify-between px-1 text-sm font-semibold">
                {NOMBRE_FASE[f]}
                <Badge>{columnas[f].length}</Badge>
              </h2>
              <ul className="flex flex-col gap-2">
                {columnas[f].map((u) => (
                  <TarjetaUnidad key={u.unidad} u={u} org={org} ws={ws} />
                ))}
              </ul>
            </section>
          ))}
        </div>
      ) : null}
      {consulta.hasNextPage ? (
        <Button variante="secundario" className="mt-4" onClick={() => void consulta.fetchNextPage()} disabled={consulta.isFetchingNextPage}>
          {consulta.isFetchingNextPage ? "Cargando…" : "Cargar más unidades"}
        </Button>
      ) : null}
    </>
  );
}
