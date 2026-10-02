import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { ErrorApi, esSinContrato14 } from "../../api/cliente";
import { claves, grafo, unidades } from "../../api/endpoints";
import { TIPOS_SIMBOLO, type AlcanceWorkspace, type RefSimbolo, type TipoSimbolo } from "../../api/tipos";
import { Aviso, Cargando, Encabezado, ErrorVista, Vacio } from "../../componentes/Estados";
import { EtiquetaRiesgo } from "../../componentes/Etiquetas";
import { Badge } from "../../componentes/ui/badge";
import { Button } from "../../componentes/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../componentes/ui/card";
import { Campo, Input } from "../../componentes/ui/input";
import { Select } from "../../componentes/ui/select";
import { useWorkspace } from "../../lib/sesion";
import { commitCorto } from "../../lib/utiles";
import type { BusquedaGrafo } from "../../router";
import { LienzoSigma, type ColorearPor } from "./LienzoSigma";
import { SeccionCriterios } from "./SeccionCriterios";
import { separarImpacto } from "../unidad/PestanaImpacto";
import {
  COLOR_CAMBIO,
  COLOR_TIPO,
  colorRepositorio,
  fusionarComparacion,
  fusionarVecindario,
  MODELO_VACIO,
  nodoDeRef,
  type ModeloGrafo,
  type NodoModelo,
} from "./modelo";

function esSinGrafo(e: unknown): boolean {
  return e instanceof ErrorApi && e.status === 404 && e.codigo === "no-encontrado";
}

function SelectorRepos({
  org,
  ws,
  elegidos,
  alCambiar,
}: {
  org: string;
  ws: string;
  elegidos: string[];
  alCambiar: (r: string[]) => void;
}) {
  const repos = useQuery({ queryKey: [...claves.grafo(org, ws), "repositorios"], queryFn: () => grafo.repositorios(org, ws) });
  if (repos.isPending) return <Cargando texto="Cargando repositorios…" />;
  if (repos.isError) return <ErrorVista error={repos.error} reintentar={() => void repos.refetch()} />;
  if (repos.data.length === 0) return <Vacio titulo="Este workspace no tiene repositorios vinculados" />;
  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="mb-1 text-sm font-medium">Repositorios (ninguno marcado = todos)</legend>
      {repos.data.map((r) => (
        <label key={r.repositorio} className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={elegidos.includes(r.repositorio)}
            onChange={(e) =>
              alCambiar(e.target.checked ? [...elegidos, r.repositorio] : elegidos.filter((x) => x !== r.repositorio))
            }
          />
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: colorRepositorio(r.repositorio) }} aria-hidden="true" />
          <span className="font-medium">{r.repositorio}</span>
          <span className="text-xs text-suave">
            {r.rol} · {r.nivel_codigo} · {commitCorto(r.commit)}
          </span>
        </label>
      ))}
    </fieldset>
  );
}

function Buscador({ alcance, repos, unidad, alElegir }: { alcance: AlcanceWorkspace; repos: string[]; unidad?: string; alElegir: (r: RefSimbolo) => void }) {
  const [texto, setTexto] = useState("");
  const [tipo, setTipo] = useState<TipoSimbolo | "">("");
  const buscar = useMutation({
    mutationFn: () =>
      grafo.consultar({
        alcance,
        repositorios: repos,
        ...(unidad ? { unidad } : {}),
        consulta: { verbo: "search", texto: texto.trim(), ...(tipo ? { tipos: [tipo] } : {}) },
        limite: 50,
      }),
  });
  const enviar = (e: FormEvent) => {
    e.preventDefault();
    if (texto.trim()) buscar.mutate();
  };
  const simbolos = (buscar.data?.resultados ?? []).map((r) => r.ref).filter((r): r is RefSimbolo => r.tipo === "simbolo");
  return (
    <div className="flex flex-col gap-2">
      <form onSubmit={enviar} className="flex flex-col gap-2" role="search">
        <Campo etiqueta="Buscar símbolo" htmlFor="buscar-simbolo">
          <Input id="buscar-simbolo" value={texto} maxLength={1000} onChange={(e) => setTexto(e.target.value)} placeholder="nombre o ruta" />
        </Campo>
        <Campo etiqueta="Tipo" htmlFor="buscar-tipo">
          <Select
            id="buscar-tipo"
            vacio="Cualquiera"
            value={tipo}
            opciones={TIPOS_SIMBOLO.map((t) => ({ valor: t, etiqueta: t }))}
            onChange={(e) => setTipo(e.target.value as TipoSimbolo | "")}
          />
        </Campo>
        <Button type="submit" disabled={!texto.trim() || buscar.isPending}>
          {buscar.isPending ? "Buscando…" : "Buscar"}
        </Button>
      </form>
      {buscar.isError ? (
        esSinGrafo(buscar.error) ? (
          <Aviso tono="aviso">El servidor no tiene grafo de código configurado.</Aviso>
        ) : (
          <ErrorVista error={buscar.error} />
        )
      ) : null}
      {buscar.isSuccess && simbolos.length === 0 ? <p className="text-sm text-suave">Sin resultados.</p> : null}
      {simbolos.length > 0 ? (
        <ul className="flex max-h-72 flex-col gap-1 overflow-y-auto" aria-label="Resultados de la búsqueda">
          {simbolos.map((s) => (
            <li key={s.simbolo}>
              <button
                type="button"
                onClick={() => alElegir(s)}
                className="w-full rounded-md px-2 py-1 text-left text-sm hover:bg-fondo focus-visible:outline-2 focus-visible:outline-primario"
              >
                <span className="font-medium">{s.nombre}</span> <span className="text-xs text-suave">{s.tipo_simbolo}</span>
                <span className="block truncate font-mono text-xs text-suave">
                  {s.repositorio}/{s.ruta}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {buscar.data?.truncado ? <p className="text-xs text-suave">Resultados truncados; afina la búsqueda.</p> : null}
    </div>
  );
}

const TEXTO_CAMBIO = {
  nuevo: "nuevo en el snapshot de la unidad",
  eliminado: "oculto por el snapshot de la unidad",
  tocado: "tocado por la unidad",
} as const;

function PanelDetalle({
  org,
  ws,
  nodo,
  modelo,
  cargando,
  alExpandir,
}: {
  org: string;
  ws: string;
  nodo: NodoModelo;
  modelo: ModeloGrafo;
  cargando: boolean;
  alExpandir: () => void;
}) {
  const impacto = modelo.impacto[nodo.id];
  const relacionados = modelo.relacionados[nodo.id] ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="break-all">{nodo.nombre}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-sm">
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
          <dt className="text-suave">Tipo</dt>
          <dd>
            <span className="mr-1 inline-block h-2.5 w-2.5 rounded-full" style={{ background: COLOR_TIPO[nodo.tipo_simbolo] }} />
            {nodo.tipo_simbolo}
          </dd>
          <dt className="text-suave">Ruta</dt>
          <dd className="break-all font-mono text-xs">{nodo.ruta || "—"}</dd>
          <dt className="text-suave">Repositorio</dt>
          <dd>{nodo.repositorio || "—"}</dd>
          <dt className="text-suave">Commit</dt>
          <dd className="font-mono text-xs">{commitCorto(nodo.commit)}</dd>
          {nodo.cambio ? (
            <>
              <dt className="text-suave">Frente a la base</dt>
              <dd>
                <span className="mr-1 inline-block h-2.5 w-2.5 rounded-full" style={{ background: COLOR_CAMBIO[nodo.cambio] }} />
                {TEXTO_CAMBIO[nodo.cambio]}
              </dd>
            </>
          ) : null}
          <dt className="text-suave">Impacto upstream</dt>
          <dd>
            {impacto ? (
              <span className="flex flex-wrap items-center gap-1">
                {impacto.total} dependientes {impacto.riesgoMax ? <EtiquetaRiesgo riesgo={impacto.riesgoMax} /> : null}
              </span>
            ) : (
              <span className="text-suave">expande para calcularlo</span>
            )}
          </dd>
        </dl>
        {relacionados.length > 0 ? (
          <div>
            <p className="font-medium">Clusters y procesos</p>
            <ul className="flex flex-wrap gap-1">
              {relacionados.map((r) => (
                <li key={`${r.clase}-${r.id}`}>
                  <Badge tono={r.clase === "cluster" ? "violeta" : "info"}>
                    {r.clase}: {r.nombre}
                  </Badge>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <SeccionCriterios org={org} ws={ws} simbolo={nodo.id} />
        <Button variante="secundario" onClick={alExpandir} disabled={cargando}>
          {cargando ? "Cargando vecindario…" : modelo.expandidos.includes(nodo.id) ? "Volver a expandir" : "Expandir"}
        </Button>
        <p className="text-xs text-suave">La consola nunca muestra código: solo nombres, rutas y relaciones.</p>
      </CardContent>
    </Card>
  );
}

const opcionesTocados = (alcance: AlcanceWorkspace, unidad: string) => ({
  queryKey: [...claves.unidad(alcance.org, alcance.workspace, unidad), "tocados"],
  queryFn: () => grafo.consultar({ alcance, unidad, consulta: { verbo: "impact" as const, profundidad: 1 }, limite: 500 }),
  retry: false,
});

function SelectorUnidad({ alcance, unidad, alCambiar }: { alcance: AlcanceWorkspace; unidad: string | undefined; alCambiar: (u: string | undefined) => void }) {
  const lista = useQuery({
    queryKey: [...claves.tablero(alcance.org, alcance.workspace), "para-grafo"],
    queryFn: () => unidades.listar({ alcance, limite: 100 }),
  });
  const tocados = useQuery({ ...opcionesTocados(alcance, unidad ?? ""), enabled: !!unidad });
  const opciones = (lista.data?.unidades ?? []).map((u) => ({ valor: u.unidad, etiqueta: `${u.unidad} · ${u.titulo}` }));
  if (unidad && !opciones.some((o) => o.valor === unidad)) opciones.unshift({ valor: unidad, etiqueta: unidad });
  const nTocados = tocados.data ? separarImpacto(tocados.data.resultados).tocados.length : null;
  return (
    <div className="flex flex-col gap-2">
      <Campo etiqueta="Comparar con una unidad" htmlFor="comparar-unidad" ayuda="Muestra la base (canónico) contra el snapshot de la unidad: lo nuevo, lo oculto y lo tocado.">
        <Select id="comparar-unidad" vacio="Solo la base" value={unidad ?? ""} opciones={opciones} onChange={(e) => alCambiar(e.target.value || undefined)} />
      </Campo>
      {lista.isError ? <p className="text-xs text-peligro">No se pudo cargar la lista de unidades.</p> : null}
      {unidad ? (
        <>
          <ul className="flex flex-wrap gap-x-3 gap-y-1 text-xs" aria-label="Leyenda de la comparación">
            {(["nuevo", "eliminado", "tocado"] as const).map((c) => (
              <li key={c} className="flex items-center gap-1">
                <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: COLOR_CAMBIO[c] }} aria-hidden="true" />
                {c === "nuevo" ? "nuevo" : c === "eliminado" ? "oculto" : "tocado"}
              </li>
            ))}
            <li className="flex items-center gap-1">
              <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: COLOR_CAMBIO.igual }} aria-hidden="true" />
              igual
            </li>
          </ul>
          {tocados.isPending ? <p className="text-xs text-suave">Calculando lo que toca la unidad…</p> : null}
          {nTocados !== null ? <p className="text-xs text-suave">La unidad toca {nTocados} símbolo(s).</p> : null}
          {tocados.isError ? (
            <p className="text-xs text-suave">
              {esSinContrato14(tocados.error) ? "El servidor aún no habla el contrato 1.4: no se marcan los símbolos tocados." : "No se pudo calcular lo que toca la unidad."}
            </p>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

export function NavegadorGrafo() {
  const { org, ws } = useWorkspace();
  const busqueda = useSearch({ strict: false }) as BusquedaGrafo;
  const navegar = useNavigate();
  const clienteQuery = useQueryClient();
  const alcance = { org, workspace: ws };
  const comparada = busqueda.unidad;
  const [repos, setRepos] = useState<string[]>([]);
  const [profundidad, setProfundidad] = useState(1);
  const [colorearPor, setColorearPor] = useState<ColorearPor>(comparada ? "cambio" : "tipo");
  const [modelo, setModelo] = useState<ModeloGrafo>(MODELO_VACIO);
  const [seleccionado, setSeleccionado] = useState<string | null>(null);

  const vecindario = useMutation({
    mutationFn: async (centro: NodoModelo) => {
      const base = { alcance, ...(repos.length ? { repositorios: repos } : {}), limite: 100 };
      const traer = async (unidad?: string) => {
        const u = unidad ? { unidad } : {};
        const [up, down, rel] = await Promise.all([
          grafo.consultar({ ...base, ...u, consulta: { verbo: "traverse", simbolo: centro.id, direccion: "upstream", profundidad } }),
          grafo.consultar({ ...base, ...u, consulta: { verbo: "traverse", simbolo: centro.id, direccion: "downstream", profundidad } }),
          grafo.consultar({ ...base, ...u, consulta: { verbo: "related", simbolo: centro.id } }),
        ]);
        return { up, down, rel };
      };
      // Con una unidad se mira el mismo vecindario en la base y en su snapshot, y se marca la diferencia.
      const [canonico, snapshot, tocados] = await Promise.all([
        traer(),
        comparada ? traer(comparada) : Promise.resolve(null),
        comparada
          ? clienteQuery.ensureQueryData(opcionesTocados(alcance, comparada)).then(
              (r) => new Set(separarImpacto(r.resultados).tocados.map((t) => t.ref.simbolo)),
              () => new Set<string>(), // sin contrato 1.4 o sin snapshot: se compara sin marcar tocados
            )
          : Promise.resolve(new Set<string>()),
      ]);
      const vista = snapshot ?? canonico;
      const commit = vista.up.commits[centro.repositorio] ?? vista.down.commits[centro.repositorio] ?? centro.commit;
      return { centro: { ...centro, commit }, canonico, snapshot, tocados };
    },
    onSuccess: ({ centro, canonico, snapshot, tocados }) =>
      setModelo((m) =>
        snapshot
          ? fusionarComparacion(
              m,
              centro,
              { up: canonico.up.resultados, down: canonico.down.resultados, rel: canonico.rel.resultados },
              { up: snapshot.up.resultados, down: snapshot.down.resultados, rel: snapshot.rel.resultados },
              tocados,
            )
          : fusionarVecindario(m, centro, canonico.up.resultados, canonico.down.resultados, canonico.rel.resultados),
      ),
  });

  const expandir = useCallback(
    (nodo: NodoModelo) => {
      setSeleccionado(nodo.id);
      vecindario.mutate(nodo);
    },
    [vecindario.mutate],
  );

  // Cambiar la unidad comparada invalida lo explorado (las marcas son de la anterior): se vuelve a abrir el símbolo elegido.
  const unidadPrevia = useRef(comparada);
  useEffect(() => {
    if (unidadPrevia.current === comparada) return;
    unidadPrevia.current = comparada;
    setColorearPor(comparada ? "cambio" : "tipo");
    const abierto = seleccionado ? modelo.nodos[seleccionado] : undefined;
    setModelo(MODELO_VACIO);
    if (abierto) {
      const { cambio: _marcaAnterior, ...limpio } = abierto;
      expandir(limpio);
    }
  }, [comparada, seleccionado, modelo.nodos, expandir]);

  const elegirDeBusqueda = (ref: RefSimbolo) => {
    void navegar({ to: ".", search: { simbolo: ref.simbolo, nombre: ref.nombre, repositorio: ref.repositorio } });
    expandir(nodoDeRef(ref));
  };

  // Símbolo llegado por la URL (p. ej. desde la trazabilidad de una unidad).
  const cargadoDeUrl = useRef<string | null>(null);
  useEffect(() => {
    const id = busqueda.simbolo;
    if (!id || cargadoDeUrl.current === id || modelo.nodos[id]) return;
    cargadoDeUrl.current = id;
    expandir({
      id,
      nombre: busqueda.nombre ?? `${id.slice(0, 12)}…`,
      tipo_simbolo: "desconocido",
      repositorio: busqueda.repositorio ?? "",
      ruta: "",
      commit: null,
    });
  }, [busqueda.simbolo, busqueda.nombre, busqueda.repositorio, modelo.nodos, expandir]);

  const nodo = seleccionado ? modelo.nodos[seleccionado] : undefined;
  const vacio = Object.keys(modelo.nodos).length === 0;

  return (
    <>
      <Encabezado
        titulo="Grafo de código"
        descripcion="Busca un símbolo y explora quién depende de él (upstream) y de qué depende (downstream)."
        acciones={
          !vacio ? (
            <Button
              variante="secundario"
              onClick={() => {
                setModelo(MODELO_VACIO);
                setSeleccionado(null);
                cargadoDeUrl.current = null;
                // Se conserva la unidad comparada: «Limpiar» vacía el lienzo, no la comparación.
                void navegar({ to: ".", search: comparada ? { unidad: comparada } : {} });
              }}
            >
              Limpiar
            </Button>
          ) : null
        }
      />
      <div className="grid gap-4 lg:grid-cols-[18rem_1fr_18rem]">
        <div className="flex flex-col gap-4">
          <SelectorRepos org={org} ws={ws} elegidos={repos} alCambiar={setRepos} />
          <div className="grid grid-cols-2 gap-2">
            <Campo etiqueta="Profundidad" htmlFor="profundidad">
              <Select
                id="profundidad"
                value={String(profundidad)}
                onChange={(e) => setProfundidad(Number(e.target.value))}
                opciones={[1, 2, 3].map((n) => ({ valor: String(n), etiqueta: String(n) }))}
              />
            </Campo>
            <Campo etiqueta="Colorear por" htmlFor="colorear">
              <Select
                id="colorear"
                value={colorearPor}
                onChange={(e) => setColorearPor(e.target.value as ColorearPor)}
                opciones={[
                  { valor: "tipo", etiqueta: "tipo" },
                  { valor: "repositorio", etiqueta: "repositorio" },
                  ...(comparada ? [{ valor: "cambio", etiqueta: "cambio (base contra snapshot)" }] : []),
                ]}
              />
            </Campo>
          </div>
          <SelectorUnidad
            alcance={alcance}
            unidad={comparada}
            alCambiar={(u) => void navegar({ to: ".", search: (prev: BusquedaGrafo) => ({ ...prev, unidad: u }) })}
          />
          <Buscador alcance={alcance} repos={repos} {...(comparada ? { unidad: comparada } : {})} alElegir={elegirDeBusqueda} />
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          {vecindario.isPending ? <Cargando texto="Cargando vecindario…" /> : null}
          {vecindario.isError ? (
            esSinGrafo(vecindario.error) ? (
              <Aviso tono="aviso">El servidor no tiene grafo de código configurado o el símbolo no existe en él.</Aviso>
            ) : (
              <ErrorVista error={vecindario.error} />
            )
          ) : null}
          {vacio && !vecindario.isPending ? (
            <Vacio titulo="Elige un símbolo para empezar">Usa el buscador o llega desde la trazabilidad de una unidad.</Vacio>
          ) : (
            <LienzoSigma
              modelo={modelo}
              seleccionado={seleccionado}
              colorearPor={colorearPor}
              alElegir={(id) => setSeleccionado(id)}
            />
          )}
        </div>
        <div>
          {nodo ? (
            <PanelDetalle org={org} ws={ws} nodo={nodo} modelo={modelo} cargando={vecindario.isPending} alExpandir={() => expandir(nodo)} />
          ) : (
            <p className="text-sm text-suave">Haz clic en un nodo para ver su detalle.</p>
          )}
        </div>
      </div>
    </>
  );
}
