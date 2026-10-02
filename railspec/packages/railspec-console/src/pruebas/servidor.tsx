import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from "@tanstack/react-router";
import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { vi } from "vitest";
import type {
  Actor,
  Auditoria,
  AsignacionRol,
  Organizacion,
  PerfilConfig,
  PresupuestoConfig,
  Rol,
  VinculoRepositorio,
  Workspace,
  Yo,
} from "../api/tipos";

// Utilidades de prueba para las vistas que hablan con la API de la consola: un `fetch` falso
// con rutas, registro de llamadas y datos de ejemplo con la forma del contrato.

const BASE = "/consola/api";

export interface Llamada {
  metodo: string;
  /** Ruta sin el prefijo `/consola/api` ni la query. */
  ruta: string;
  consulta: URLSearchParams;
  cuerpo: any;
  cabeceras: Record<string, string>;
}

/** Devuelve una `Response` o un valor JSON (se responde 200). `null` responde 204. */
export type Responder = (llamada: Llamada) => Response | unknown;

export const json = (estado: number, cuerpo: unknown) => new Response(JSON.stringify(cuerpo), { status: estado });

/**
 * Sustituye `fetch` por un servidor falso. Las rutas se indican como `"METODO /ruta"`
 * (sin `/consola/api` ni query). Una ruta no simulada responde 404 y falla la prueba al leer
 * `llamadas`/`rutaNoSimulada`.
 */
export function servidorFalso(rutas: Record<string, Responder>) {
  const llamadas: Llamada[] = [];
  const noSimuladas: string[] = [];
  const fetchFalso = vi.fn(async (url: string, init?: RequestInit) => {
    const u = new URL(url, "http://localhost");
    const metodo = init?.method ?? "GET";
    const ruta = u.pathname.startsWith(BASE) ? u.pathname.slice(BASE.length) : u.pathname;
    const llamada: Llamada = {
      metodo,
      ruta,
      consulta: u.searchParams,
      cuerpo: init?.body === undefined ? undefined : JSON.parse(String(init.body)),
      cabeceras: (init?.headers ?? {}) as Record<string, string>,
    };
    llamadas.push(llamada);
    const responder = rutas[`${metodo} ${ruta}`];
    if (!responder) {
      noSimuladas.push(`${metodo} ${ruta}`);
      return json(404, { detalle: `ruta no simulada: ${metodo} ${ruta}` });
    }
    const r = responder(llamada);
    if (r instanceof Response) return r;
    return r === null ? new Response(null, { status: 204 }) : json(200, r);
  });
  vi.stubGlobal("fetch", fetchFalso);
  return {
    llamadas,
    noSimuladas,
    /** Llamadas de un método a una ruta, en orden. */
    de: (metodo: string, ruta: string) => llamadas.filter((l) => l.metodo === metodo && l.ruta === ruta),
  };
}

export const clienteDePrueba = () => new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });

/** Monta `ui` bajo un QueryClient nuevo (sin router). */
export function montarConQuery(ui: ReactNode) {
  return render(<QueryClientProvider client={clienteDePrueba()}>{ui}</QueryClientProvider>);
}

/**
 * Monta una vista de ruta (usa `useParams`/`Link`) en `url`, con rutas hermanas de relleno para
 * los enlaces. `ruta` es el patrón con parámetros, p. ej. `/$org/$ws/administracion`.
 */
export function montarEnRuta(componente: () => ReactNode, ruta: string, url: string) {
  const raiz = createRootRoute({ component: Outlet });
  const vista = createRoute({ getParentRoute: () => raiz, path: ruta, component: componente });
  const relleno = createRoute({ getParentRoute: () => raiz, path: "/$org/$ws", component: () => null });
  const router = createRouter({
    routeTree: raiz.addChildren([vista, relleno]),
    history: createMemoryHistory({ initialEntries: [url] }),
  });
  return render(
    <QueryClientProvider client={clienteDePrueba()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------- datos de ejemplo

const ana: Actor = { tipo: "humano", canal: "consola", github_id: 1, login: "ana" };
export const auditoria: Auditoria = {
  creado_por: ana,
  creado_en: "2026-09-01T10:00:00Z",
  actualizado_por: ana,
  actualizado_en: "2026-09-02T10:00:00Z",
};

export const organizacion = (o: Partial<Organizacion> = {}): Organizacion => ({
  id: "acme",
  nombre: "Acme Corp",
  github_org: "acme-gh",
  region_datos: "eu",
  version: 2,
  auditoria,
  ...o,
});

export const workspace = (id: string, o: Partial<Workspace> = {}): Workspace => ({
  alcance: { org: "acme", workspace: id },
  nombre: id === "cert" ? "Certificados" : id,
  zona_datos_azure: null,
  perfil_por_defecto: "estandar",
  version: 3,
  auditoria,
  ...o,
});

export const asignacion = (id: string, o: Partial<AsignacionRol> = {}): AsignacionRol => ({
  id,
  org: "acme",
  workspace: "cert",
  rol: "desarrollador",
  sujeto: { tipo: "usuario", github_id: 7, login: "maria" },
  version: 1,
  auditoria,
  ...o,
});

export const vinculo = (repo: string, o: Partial<VinculoRepositorio> = {}): VinculoRepositorio => ({
  alcance: { org: "acme", workspace: "cert", repositorio: repo },
  url: `https://github.com/acme/${repo}`,
  rol: "primario",
  rama_por_defecto: "main",
  nivel_codigo: "restringido",
  chat_contexto_codigo: {
    hosting: "azure-zona-datos",
    huella_tokens_n: 8,
    presupuesto_fuga_conversacion: 2000,
    presupuesto_fuga_usuario_dia: 20000,
    permitido: true,
  },
  retencion_snapshots_dias: 30,
  exclusiones: [],
  version: 4,
  auditoria,
  ...o,
});

export const perfilConfig = (nombre: PerfilConfig["nombre"], o: Partial<PerfilConfig> = {}): PerfilConfig => ({
  org: "acme",
  workspace: null,
  nombre,
  roles: { redactor: { modelo: { anthropic: "claude-sonnet-5-5" }, effort: "medium", structured_outputs: false } },
  gate: {
    bajo: { criticos: 1, iteraciones: 1, adversarial: false },
    medio: { criticos: 2, iteraciones: 2, adversarial: false },
    alto: { criticos: 3, iteraciones: 3, adversarial: true },
  },
  exploradores: { bajo: 0, medio: 1, alto: 2 },
  version: 3,
  auditoria,
  ...o,
});

export const presupuestoConfig = (o: Partial<PresupuestoConfig> = {}): PresupuestoConfig => ({
  org: "acme",
  workspace: null,
  por_unidad: { tokens_max: 100000, segundos_max: 600, costo_usd_max: 5 },
  por_fase: { plan: { tokens_max: 20000 } },
  mensual_usd: 500,
  version: 2,
  auditoria,
  ...o,
});

/** `/yo` de una persona con `rolOrg` en acme y, si se da, `rolWs` en el workspace `cert`. */
export const yo = (rolOrg: Rol | null, rolWs?: Rol): Yo => ({
  login: "ana",
  github_id: 1,
  plataforma_admin: false,
  organizaciones: [
    {
      id: "acme",
      nombre: "Acme Corp",
      rol: rolOrg,
      workspaces: rolWs ? [{ workspace: "cert", nombre: "Certificados", rol: rolWs }] : [],
    },
  ],
});
