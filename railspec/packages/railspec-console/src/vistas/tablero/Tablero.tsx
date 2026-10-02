import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { claves, grafo, unidades } from "../../api/endpoints";
import { ESTADOS_FASE, FASES, type EstadoFase, type ResumenUnidad, type UnitListEntrada } from "../../api/tipos";
import { Cargando, Encabezado, ErrorVista, Vacio } from "../../componentes/Estados";
import { EtiquetaEstado, EtiquetaModo, EtiquetaRiesgo, NOMBRE_FASE } from "../../componentes/Etiquetas";
import { Badge } from "../../componentes/ui/badge";
import { Button } from "../../componentes/ui/button";
import { Select } from "../../componentes/ui/select";
import { alcanza } from "../../lib/roles";
import { useRol, useWorkspace } from "../../lib/sesion";
import { cn, fecha } from "../../lib/utiles";
import type { BusquedaTablero } from "../../router";
import { DialogoNuevaUnidad } from "../unidad/DialogoNuevaUnidad";
import {
  agruparEnCarriles,
  cambiadas,
  CRITERIOS_CARRIL,
  huellas,
  NOMBRE_CRITERIO_CARRIL,
  unirPaginas,
  type Carril,
} from "./agrupar";

/** Cada cuánto se vuelve a leer el tablero mientras está en vivo (y la pestaña visible). */
export const INTERVALO_VIVO_MS = 15_000;
/** Cuánto tiempo queda resaltada una unidad que cambió. */
const RESALTE_MS = 8_000;

function TarjetaUnidad({ u, org, ws, cambio }: { u: ResumenUnidad; org: string; ws: string; cambio: boolean }) {
  return (
    <li>
      <Link
        to="/$org/$ws/unidades/$unidad"
        params={{ org, ws, unidad: u.unidad }}
        className={cn(
          "block rounded-md border border-borde bg-superficie p-3 text-sm shadow-sm hover:border-primario focus-visible:outline-2 focus-visible:outline-primario",
          cambio && "border-primario ring-2 ring-primario/40",
        )}
      >
        <p className="font-medium leading-snug">
          {u.titulo}
          {cambio ? <Badge tono="info" className="ml-1">actualizada</Badge> : null}
        </p>
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
    <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4" role="group" aria-label="Filtros del tablero">
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
      <div className="flex flex-col gap-1">
        <label htmlFor="filtro-carriles" className="text-xs font-medium text-suave">
          Carriles
        </label>
        <Select
          id="filtro-carriles"
          vacio="Sin carriles"
          value={busqueda.carriles ?? ""}
          opciones={CRITERIOS_CARRIL.map((c) => ({ valor: c, etiqueta: `Por ${NOMBRE_CRITERIO_CARRIL[c].toLowerCase()}` }))}
          onChange={(e) => cambiar({ carriles: (e.target.value || undefined) as BusquedaTablero["carriles"] })}
        />
      </div>
    </div>
  );
}

function Columna({
  fase,
  unidades,
  org,
  ws,
  recientes,
  nivel,
}: {
  fase: (typeof FASES)[number];
  unidades: ResumenUnidad[];
  org: string;
  ws: string;
  recientes: ReadonlySet<string>;
  nivel: "h2" | "h3";
}) {
  const Titulo = nivel;
  const id = useId();
  return (
    <div role="group" aria-labelledby={id} className="flex w-64 shrink-0 flex-col rounded-lg bg-fondo p-2">
      <Titulo id={id} className="mb-2 flex items-center justify-between px-1 text-sm font-semibold">
        {NOMBRE_FASE[fase]}
        <Badge>{unidades.length}</Badge>
      </Titulo>
      <ul className="flex flex-col gap-2">
        {unidades.map((u) => (
          <TarjetaUnidad key={u.unidad} u={u} org={org} ws={ws} cambio={recientes.has(u.unidad)} />
        ))}
      </ul>
    </div>
  );
}

function CarrilTablero({
  carril,
  conTitulo,
  org,
  ws,
  recientes,
}: {
  carril: Carril;
  conTitulo: boolean;
  org: string;
  ws: string;
  recientes: ReadonlySet<string>;
}) {
  return (
    <section aria-label={conTitulo ? `Carril ${carril.etiqueta}` : "Columnas por fase"} className="flex flex-col gap-2">
      {conTitulo ? (
        <h2 className="flex items-center gap-2 border-b border-borde pb-1 text-sm font-semibold">
          {carril.etiqueta}
          <Badge>{carril.total}</Badge>
        </h2>
      ) : null}
      <div className="flex gap-3">
        {FASES.map((f) => (
          <Columna key={f} fase={f} unidades={carril.columnas[f]} org={org} ws={ws} recientes={recientes} nivel={conTitulo ? "h3" : "h2"} />
        ))}
      </div>
    </section>
  );
}

/**
 * Unidades nuevas o cambiadas desde la lectura anterior; se apagan solas tras unos segundos. La primera
 * lectura de cada consulta (y la de un filtro nuevo) no resalta nada: solo vale comparar contra una lectura previa.
 */
function useRecientes(lista: readonly ResumenUnidad[], cargado: boolean): ReadonlySet<string> {
  const anterior = useRef<Map<string, string> | null>(null);
  const [recientes, setRecientes] = useState<ReadonlySet<string>>(new Set());
  useEffect(() => {
    if (!cargado) {
      anterior.current = null;
      return;
    }
    const ahora = cambiadas(anterior.current, lista);
    anterior.current = huellas(lista);
    if (ahora.size === 0) return;
    setRecientes((previas) => new Set([...previas, ...ahora]));
    const t = setTimeout(() => setRecientes(new Set()), RESALTE_MS);
    return () => clearTimeout(t);
  }, [lista, cargado]);
  return recientes;
}

export function Tablero() {
  const { org, ws } = useWorkspace();
  const { rol } = useRol();
  const navegar = useNavigate();
  const busqueda = useSearch({ strict: false }) as BusquedaTablero;
  const enVivo = busqueda.vivo !== "no";
  const [creando, setCreando] = useState(false);
  const repos = useQuery({ queryKey: [...claves.grafo(org, ws), "repositorios"], queryFn: () => grafo.repositorios(org, ws) });

  const consulta = useInfiniteQuery({
    queryKey: [...claves.tablero(org, ws), busqueda.repositorio ?? null, busqueda.estado ?? null, busqueda.integradas ?? null],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => {
      const entrada: UnitListEntrada = { alcance: { org, workspace: ws }, limite: 100, cursor: pageParam };
      if (busqueda.repositorio) entrada.repositorio = busqueda.repositorio;
      if (busqueda.estado) entrada.estado = [busqueda.estado as EstadoFase];
      if (busqueda.integradas) entrada.integradas = busqueda.integradas === "si";
      return unidades.listar(entrada);
    },
    getNextPageParam: (ultima) => ultima.cursor_siguiente ?? undefined,
    // En vivo: relee cada tanto mientras la pestaña está a la vista (sin canal de empuje por workspace).
    refetchInterval: enVivo ? INTERVALO_VIVO_MS : false,
    refetchIntervalInBackground: false,
  });

  const lista = useMemo(() => unirPaginas(consulta.data?.pages ?? []), [consulta.data]);
  const carriles = useMemo(() => agruparEnCarriles(lista, busqueda.carriles), [lista, busqueda.carriles]);
  const recientes = useRecientes(lista, consulta.isSuccess);
  const puedeCrear = alcanza(rol, "desarrollador");

  return (
    <>
      <Encabezado
        titulo="Tablero de unidades"
        descripcion={`${org} / ${ws}`}
        acciones={
          <>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={enVivo}
                onChange={(e) =>
                  void navegar({ to: ".", search: (prev: BusquedaTablero) => ({ ...prev, vivo: e.target.checked ? undefined : ("no" as const) }) })
                }
              />
              En vivo
            </label>
            {enVivo && consulta.dataUpdatedAt ? (
              <Badge tono="exito" aria-live="off">
                actualizado {new Date(consulta.dataUpdatedAt).toLocaleTimeString("es")}
              </Badge>
            ) : null}
            {puedeCrear ? <Button onClick={() => setCreando(true)}>Nueva unidad</Button> : null}
          </>
        }
      />
      <Filtros busqueda={busqueda} repos={(repos.data ?? []).map((r) => r.repositorio)} />
      {consulta.isPending ? <Cargando texto="Cargando unidades…" /> : null}
      {consulta.isError ? <ErrorVista error={consulta.error} reintentar={() => void consulta.refetch()} /> : null}
      {consulta.isSuccess && lista.length === 0 ? (
        <Vacio titulo="No hay unidades con estos filtros">Las unidades nacen en el arnés con unit.start o desde «Nueva unidad».</Vacio>
      ) : null}
      {consulta.isSuccess && lista.length > 0 ? (
        <div className="flex flex-col gap-4 overflow-x-auto pb-2">
          {carriles.map((c) => (
            <CarrilTablero key={c.clave} carril={c} conTitulo={busqueda.carriles !== undefined} org={org} ws={ws} recientes={recientes} />
          ))}
        </div>
      ) : null}
      {consulta.hasNextPage ? (
        <Button variante="secundario" className="mt-4" onClick={() => void consulta.fetchNextPage()} disabled={consulta.isFetchingNextPage}>
          {consulta.isFetchingNextPage ? "Cargando…" : "Cargar más unidades"}
        </Button>
      ) : null}
      {creando ? <DialogoNuevaUnidad org={org} ws={ws} alCerrar={() => setCreando(false)} /> : null}
    </>
  );
}
