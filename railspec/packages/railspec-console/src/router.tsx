import { QueryClient } from "@tanstack/react-query";
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  lazyRouteComponent,
  Link,
  Outlet,
} from "@tanstack/react-router";
import { BASE_SPA, ErrorApi } from "./api/cliente";
import { Marco } from "./componentes/Marco";
import { ErrorVista, Vacio } from "./componentes/Estados";
import { Login } from "./vistas/login/Login";
import { Inicio, InicioOrg } from "./vistas/inicio/Inicio";
import { CRITERIOS_CARRIL, type CriterioCarril } from "./vistas/tablero/agrupar";

export const clienteQuery = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      // No reintentar errores del cliente (401/403/404/409/422/501): no van a cambiar.
      retry: (fallos, error) => {
        if (error instanceof ErrorApi && (error.status < 500 || error.status === 501)) return false;
        return fallos < 2;
      },
    },
  },
});

function NoEncontrada() {
  return (
    <div className="p-6">
      <Vacio titulo="Página no encontrada">
        <Link to="/" className="text-primario underline">
          Volver al inicio
        </Link>
      </Vacio>
    </div>
  );
}

const raiz = createRootRouteWithContext<{ clienteQuery: QueryClient }>()({
  component: Outlet,
  notFoundComponent: NoEncontrada,
  errorComponent: ({ error }) => (
    <div className="p-6">
      <ErrorVista error={error} />
    </div>
  ),
});

const login = createRoute({
  getParentRoute: () => raiz,
  path: "login",
  validateSearch: (s: Record<string, unknown>): { volver?: string } =>
    typeof s.volver === "string" ? { volver: s.volver } : {},
  component: Login,
});

const autenticado = createRoute({ getParentRoute: () => raiz, id: "autenticado", component: Marco });

const inicio = createRoute({ getParentRoute: () => autenticado, path: "/", component: Inicio });

const listaOrganizaciones = createRoute({
  getParentRoute: () => autenticado,
  path: "organizaciones",
  component: lazyRouteComponent(() => import("./vistas/admin/Organizaciones"), "Organizaciones"),
});

const operacionServidor = createRoute({
  getParentRoute: () => autenticado,
  path: "operacion",
  component: lazyRouteComponent(() => import("./vistas/operacion/Operacion"), "Operacion"),
});

// ---------------------------------------------------------------- organización

const org = createRoute({ getParentRoute: () => autenticado, path: "$org", component: Outlet });
const orgInicio = createRoute({ getParentRoute: () => org, path: "/", component: InicioOrg });
const orgAdministracion = createRoute({
  getParentRoute: () => org,
  path: "administracion",
  component: lazyRouteComponent(() => import("./vistas/admin/AdminOrganizacion"), "AdminOrganizacion"),
});
const orgConfiguracion = createRoute({
  getParentRoute: () => org,
  path: "configuracion",
  component: lazyRouteComponent(() => import("./vistas/config/Configuracion"), "ConfiguracionOrg"),
});

// ---------------------------------------------------------------- workspace

const ws = createRoute({ getParentRoute: () => org, path: "$ws", component: Outlet });

export interface BusquedaTablero {
  repositorio?: string;
  estado?: string;
  integradas?: "si" | "no";
  /** Reparte las unidades en carriles por este criterio. */
  carriles?: CriterioCarril;
  /** `no` apaga la actualización en vivo (encendida por defecto). */
  vivo?: "no";
}

const tablero = createRoute({
  getParentRoute: () => ws,
  path: "/",
  validateSearch: (s: Record<string, unknown>): BusquedaTablero => {
    const r: BusquedaTablero = {};
    if (typeof s.repositorio === "string" && s.repositorio) r.repositorio = s.repositorio;
    if (typeof s.estado === "string" && s.estado) r.estado = s.estado;
    if (s.integradas === "si" || s.integradas === "no") r.integradas = s.integradas;
    if (CRITERIOS_CARRIL.includes(s.carriles as CriterioCarril)) r.carriles = s.carriles as CriterioCarril;
    if (s.vivo === "no") r.vivo = "no";
    return r;
  },
  component: lazyRouteComponent(() => import("./vistas/tablero/Tablero"), "Tablero"),
});

const unidad = createRoute({
  getParentRoute: () => ws,
  path: "unidades/$unidad",
  component: lazyRouteComponent(() => import("./vistas/unidad/DetalleUnidad"), "DetalleUnidad"),
});

export interface BusquedaGrafo {
  simbolo?: string;
  nombre?: string;
  repositorio?: string;
  /** Unidad con la que se compara la base (canónico) contra su snapshot (superposición). */
  unidad?: string;
}

const grafo = createRoute({
  getParentRoute: () => ws,
  path: "grafo",
  validateSearch: (s: Record<string, unknown>): BusquedaGrafo => {
    const r: BusquedaGrafo = {};
    if (typeof s.simbolo === "string" && s.simbolo) r.simbolo = s.simbolo;
    if (typeof s.nombre === "string" && s.nombre) r.nombre = s.nombre;
    if (typeof s.repositorio === "string" && s.repositorio) r.repositorio = s.repositorio;
    if (typeof s.unidad === "string" && s.unidad) r.unidad = s.unidad;
    return r;
  },
  component: lazyRouteComponent(() => import("./vistas/grafo/NavegadorGrafo"), "NavegadorGrafo"),
});

const grafoRetenidas = createRoute({
  getParentRoute: () => ws,
  path: "grafo/retenidas",
  component: lazyRouteComponent(() => import("./vistas/grafo/Retenidas"), "Retenidas"),
});

const mandatos = createRoute({
  getParentRoute: () => ws,
  path: "mandatos",
  component: lazyRouteComponent(() => import("./vistas/mandato/Mandatos"), "Mandatos"),
});

const mandato = createRoute({
  getParentRoute: () => ws,
  path: "mandatos/$mandato",
  component: lazyRouteComponent(() => import("./vistas/mandato/DetalleMandato"), "DetalleMandato"),
});

const auditoria = createRoute({
  getParentRoute: () => ws,
  path: "auditoria",
  component: lazyRouteComponent(() => import("./vistas/auditoria/Auditoria"), "Auditoria"),
});

const estadisticas = createRoute({
  getParentRoute: () => ws,
  path: "estadisticas",
  component: lazyRouteComponent(() => import("./vistas/estadisticas/Estadisticas"), "Estadisticas"),
});

const chat = createRoute({
  getParentRoute: () => ws,
  path: "chat",
  component: lazyRouteComponent(() => import("./vistas/chat/RutaChat"), "RutaChat"),
});

const wsAdministracion = createRoute({
  getParentRoute: () => ws,
  path: "administracion",
  component: lazyRouteComponent(() => import("./vistas/admin/AdminWorkspace"), "AdminWorkspace"),
});

const wsConfiguracion = createRoute({
  getParentRoute: () => ws,
  path: "configuracion",
  component: lazyRouteComponent(() => import("./vistas/config/Configuracion"), "ConfiguracionWorkspace"),
});

const arbol = raiz.addChildren([
  login,
  autenticado.addChildren([
    inicio,
    listaOrganizaciones,
    operacionServidor,
    org.addChildren([
      orgInicio,
      orgAdministracion,
      orgConfiguracion,
      ws.addChildren([tablero, unidad, mandatos, mandato, grafo, grafoRetenidas, auditoria, estadisticas, chat, wsAdministracion, wsConfiguracion]),
    ]),
  ]),
]);

export const router = createRouter({
  routeTree: arbol,
  basepath: BASE_SPA,
  context: { clienteQuery },
  defaultPreload: "intent",
  scrollRestoration: true,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
