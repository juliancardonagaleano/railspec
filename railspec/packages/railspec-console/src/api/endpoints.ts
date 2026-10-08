import { invocarTool, pedir } from "./cliente";
import type {
  AlcanceUnidad,
  AsignacionRol,
  CommitIntegrable,
  ConfigAuth,
  EscrituraAvisos,
  EstadoAvisos,
  InformeSemanal,
  ConfigAvisos,
  ResultadoPruebaAvisos,
  DeclaracionModelo,
  Decision,
  DetalleUnidad,
  EscrituraPerfil,
  EscrituraProveedorContexto,
  EscrituraSuscripcion,
  EscrituraVinculo,
  EstadoCatalogo,
  EstadoUnidad,
  FiltrosAuditoria,
  GraphQueryEntrada,
  GraphQuerySalida,
  LineaDeTiempo,
  ListaSuscripciones,
  ModeloCatalogo,
  Modo,
  Organizacion,
  PaginaAuditoria,
  Perfil,
  PerfilConfig,
  Presupuesto,
  PresupuestoConfig,
  Proveedor,
  ProveedorContexto,
  RepositorioGrafo,
  RetenidaGrafo,
  ResultadoDescubrimiento,
  ResultadoSincronizacion,
  ResumenWorkspace,
  Rol,
  RolContexto,
  SujetoRolNuevo,
  Suscripcion,
  TelemetryQueryEntrada,
  TelemetryQuerySalida,
  TokenChat,
  Trazabilidad,
  UnitListEntrada,
  UnitListSalida,
  UnitStartEntrada,
  UnitStartSalida,
  VinculoRepositorio,
  Workspace,
  Yo,
} from "./tipos";

const c = encodeURIComponent;
const rutaWs = (org: string, ws: string) => `/orgs/${c(org)}/workspaces/${c(ws)}`;
const rutaUnidad = (org: string, ws: string, u: string) => `${rutaWs(org, ws)}/unidades/${c(u)}`;

// ---------------------------------------------------------------- autenticación

export const auth = {
  config: () => pedir<ConfigAuth>("/auth/config", { sinRedireccion: true }),
  desarrollo: (token: string) =>
    pedir<null>("/auth/desarrollo", { metodo: "POST", cuerpo: { token }, sinRedireccion: true }),
  salir: () => pedir<null>("/auth/salir", { metodo: "POST", sinRedireccion: true }),
  token: () => pedir<TokenChat>("/auth/token", { metodo: "POST" }),
  urlGithub: (volver: string) => `/consola/api/auth/github/inicio?volver=${c(volver)}`,
};

export const obtenerYo = () => pedir<Yo>("/yo");

// ---------------------------------------------------------------- unidades (tools)

export const unidades = {
  listar: (entrada: UnitListEntrada) => invocarTool<UnitListSalida>("unit.list", entrada),
  detalle: (org: string, ws: string, u: string) => pedir<DetalleUnidad>(rutaUnidad(org, ws, u)),
  lineaDeTiempo: (org: string, ws: string, u: string) =>
    pedir<LineaDeTiempo>(`${rutaUnidad(org, ws, u)}/linea-de-tiempo`),
  trazabilidad: (org: string, ws: string, u: string) =>
    pedir<Trazabilidad>(`${rutaUnidad(org, ws, u)}/trazabilidad`),
  commitIntegrable: (org: string, ws: string, u: string) =>
    pedir<CommitIntegrable>(`${rutaUnidad(org, ws, u)}/commit-integrable`),
  urlEventos: (org: string, ws: string, u: string) =>
    `/consola/api${rutaUnidad(org, ws, u)}/eventos?desde_remoto=0&desde_local=0`,
  iniciar: (entrada: UnitStartEntrada) => invocarTool<UnitStartSalida>("unit.start", entrada),
  aprobar: (entrada: { unidad: AlcanceUnidad; checkpoint: string; decision: Decision; comentario?: string }) =>
    invocarTool<{ estado: EstadoUnidad }>("unit.approve", entrada),
  integrar: (entrada: { unidad: AlcanceUnidad; especificacion_viva: string; pr_url?: string; commit_integrado?: string }) =>
    invocarTool<{ estado: EstadoUnidad }>("unit.integrate", entrada),
  fijarModo: (entrada: { unidad: AlcanceUnidad; modo: Modo; motivo: string; version_vista: number }) =>
    invocarTool<{ estado: EstadoUnidad }>("unit.set_mode", entrada),
};

// ---------------------------------------------------------------- grafo y telemetría

export const grafo = {
  repositorios: (org: string, ws: string) => pedir<RepositorioGrafo[]>(`${rutaWs(org, ws)}/grafo/repositorios`),
  consultar: (entrada: GraphQueryEntrada) => invocarTool<GraphQuerySalida>("graph.query", entrada),
  retenidas: (org: string, ws: string) => pedir<RetenidaGrafo[]>(`${rutaWs(org, ws)}/grafo/retenidas`),
};

export const telemetria = {
  consultar: (entrada: TelemetryQueryEntrada) => invocarTool<TelemetryQuerySalida>("telemetry.query", entrada),
  resumen: (org: string, ws: string) => pedir<ResumenWorkspace>(`${rutaWs(org, ws)}/resumen`),
};

export const auditoria = {
  listar: (org: string, ws: string, filtros: FiltrosAuditoria) =>
    pedir<PaginaAuditoria>(`${rutaWs(org, ws)}/auditoria`, { consulta: { ...filtros } }),
};

// ---------------------------------------------------------------- administración

export const organizaciones = {
  listar: () => pedir<Organizacion[]>("/orgs"),
  crear: (datos: { id: string; nombre: string; github_org?: string | null; region_datos: string }) =>
    pedir<Organizacion>("/orgs", { metodo: "POST", cuerpo: datos }),
  editar: (org: string, datos: { nombre: string; github_org?: string | null; region_datos: string; version: number }) =>
    pedir<Organizacion>(`/orgs/${c(org)}`, { metodo: "PUT", cuerpo: datos }),
};

export const workspaces = {
  listar: (org: string) => pedir<Workspace[]>(`/orgs/${c(org)}/workspaces`),
  crear: (
    org: string,
    datos: { workspace: string; nombre: string; zona_datos_azure?: string | null; perfil_por_defecto?: Perfil },
  ) => pedir<Workspace>(`/orgs/${c(org)}/workspaces`, { metodo: "POST", cuerpo: datos }),
  editar: (
    org: string,
    ws: string,
    datos: { nombre: string; zona_datos_azure?: string | null; perfil_por_defecto: Perfil; version: number },
  ) => pedir<Workspace>(rutaWs(org, ws), { metodo: "PUT", cuerpo: datos }),
};

export const roles = {
  listar: (org: string, ws?: string) =>
    pedir<AsignacionRol[]>(`/orgs/${c(org)}/roles`, { consulta: { workspace: ws } }),
  asignar: (org: string, datos: { workspace: string | null; rol: Rol; sujeto: SujetoRolNuevo }) =>
    pedir<AsignacionRol>(`/orgs/${c(org)}/roles`, { metodo: "POST", cuerpo: datos }),
  quitar: (org: string, id: string) => pedir<null>(`/orgs/${c(org)}/roles/${c(id)}`, { metodo: "DELETE" }),
};

export const repositorios = {
  listar: (org: string, ws: string) => pedir<VinculoRepositorio[]>(`${rutaWs(org, ws)}/repositorios`),
  guardar: (org: string, ws: string, repo: string, datos: EscrituraVinculo) =>
    pedir<VinculoRepositorio>(`${rutaWs(org, ws)}/repositorios/${c(repo)}`, { metodo: "PUT", cuerpo: datos }),
  desvincular: (org: string, ws: string, repo: string, motivo: string) =>
    pedir<null>(`${rutaWs(org, ws)}/repositorios/${c(repo)}`, { metodo: "DELETE", consulta: { motivo } }),
};

// ---------------------------------------------------------------- configuración

export const catalogo = {
  listar: (org: string) => pedir<ModeloCatalogo[]>(`/orgs/${c(org)}/catalogo`),
  estado: (org: string) => pedir<EstadoCatalogo>(`/orgs/${c(org)}/catalogo/estado`),
  /** Sin `proveedor` lee todos; con él, solo ese. 502 si todos los pedidos fallaron. */
  sincronizar: (org: string, proveedor?: Proveedor) =>
    pedir<ResultadoSincronizacion>(`/orgs/${c(org)}/catalogo/sincronizar`, { metodo: "POST", consulta: { proveedor } }),
};

export const suscripciones = {
  listar: (org: string) => pedir<ListaSuscripciones>(`/orgs/${c(org)}/suscripciones`),
  /** Sin `version` crea; con ella edita. La clave nunca vuelve en la respuesta. */
  guardar: (org: string, id: string, datos: EscrituraSuscripcion) =>
    pedir<Suscripcion>(`/orgs/${c(org)}/suscripciones/${c(id)}`, { metodo: "PUT", cuerpo: datos }),
  borrar: (org: string, id: string) => pedir<null>(`/orgs/${c(org)}/suscripciones/${c(id)}`, { metodo: "DELETE" }),
  /** 502 con `codigo` y `detalle` si el proveedor falla (el cliente lo recibe como `ErrorApi`). */
  descubrir: (org: string, id: string) =>
    pedir<ResultadoDescubrimiento>(`/orgs/${c(org)}/suscripciones/${c(id)}/descubrir`, { metodo: "POST" }),
  elegir: (org: string, id: string, seleccionados: string[], version: number) =>
    pedir<Suscripcion>(`/orgs/${c(org)}/suscripciones/${c(id)}/modelos`, { metodo: "PUT", cuerpo: { seleccionados, version } }),
  declarar: (org: string, id: string, datos: DeclaracionModelo) =>
    pedir<Suscripcion>(`/orgs/${c(org)}/suscripciones/${c(id)}/modelos`, { metodo: "POST", cuerpo: datos }),
  retirar: (org: string, id: string, clave: string, version: number) =>
    pedir<Suscripcion>(`/orgs/${c(org)}/suscripciones/${c(id)}/modelos/${c(clave)}`, { metodo: "DELETE", consulta: { version } }),
};

export const avisos = {
  leer: (org: string) => pedir<EstadoAvisos>(`/orgs/${c(org)}/avisos`),
  /** Sin `version` crea; con ella edita. El URL del webhook nunca vuelve en la respuesta. */
  guardar: (org: string, datos: EscrituraAvisos) => pedir<ConfigAvisos>(`/orgs/${c(org)}/avisos`, { metodo: "PUT", cuerpo: datos }),
  prueba: (org: string, que: "aviso" | "informe") =>
    pedir<ResultadoPruebaAvisos>(`/orgs/${c(org)}/avisos/prueba`, { metodo: "POST", cuerpo: { que } }),
  informeSemanal: (org: string) => pedir<InformeSemanal>(`/orgs/${c(org)}/informe-semanal`),
};

export const perfiles = {
  listar: (org: string, ws?: string) =>
    pedir<PerfilConfig[]>(`/orgs/${c(org)}/perfiles`, { consulta: { workspace: ws } }),
  guardar: (org: string, nombre: Perfil, datos: EscrituraPerfil, ws?: string) =>
    pedir<{ perfil: PerfilConfig; avisos: string[] }>(`/orgs/${c(org)}/perfiles/${c(nombre)}`, {
      metodo: "PUT",
      cuerpo: datos,
      consulta: { workspace: ws },
    }),
};

export const presupuestos = {
  listar: (org: string, ws?: string) =>
    pedir<PresupuestoConfig[]>(`/orgs/${c(org)}/presupuestos`, { consulta: { workspace: ws } }),
  guardar: (
    org: string,
    datos: { por_unidad: Presupuesto; por_fase?: PresupuestoConfig["por_fase"]; mensual_usd?: number | null; version?: number },
    ws?: string,
  ) => pedir<PresupuestoConfig>(`/orgs/${c(org)}/presupuestos`, { metodo: "PUT", cuerpo: datos, consulta: { workspace: ws } }),
};

export const proveedoresContexto = {
  listar: (org: string, ws?: string) =>
    pedir<ProveedorContexto[]>(`/orgs/${c(org)}/proveedores-contexto`, { consulta: { workspace: ws } }),
  guardar: (org: string, rol: RolContexto, nombre: string, datos: EscrituraProveedorContexto, ws?: string) =>
    pedir<ProveedorContexto>(`/orgs/${c(org)}/proveedores-contexto/${c(rol)}/${c(nombre)}`, {
      metodo: "PUT",
      cuerpo: datos,
      consulta: { workspace: ws },
    }),
  borrar: (org: string, rol: RolContexto, nombre: string, ws?: string) =>
    pedir<null>(`/orgs/${c(org)}/proveedores-contexto/${c(rol)}/${c(nombre)}`, {
      metodo: "DELETE",
      consulta: { workspace: ws },
    }),
};

// ---------------------------------------------------------------- claves de TanStack Query

export const claves = {
  yo: ["yo"] as const,
  configAuth: ["auth", "config"] as const,
  unidad: (org: string, ws: string, u: string) => ["unidad", org, ws, u] as const,
  tablero: (org: string, ws: string) => ["tablero", org, ws] as const,
  grafo: (org: string, ws: string) => ["grafo", org, ws] as const,
  retenidas: (org: string, ws: string) => ["grafo", org, ws, "retenidas"] as const,
  telemetria: (org: string, ws: string) => ["telemetria", org, ws] as const,
  auditoria: (org: string, ws: string) => ["auditoria", org, ws] as const,
  organizaciones: ["organizaciones"] as const,
  workspaces: (org: string) => ["workspaces", org] as const,
  roles: (org: string, ws?: string) => ["roles", org, ws ?? null] as const,
  repositorios: (org: string, ws: string) => ["repositorios", org, ws] as const,
  catalogo: (org: string) => ["catalogo", org] as const,
  catalogoEstado: (org: string) => ["catalogo", org, "estado"] as const,
  suscripciones: (org: string) => ["suscripciones", org] as const,
  avisos: (org: string) => ["avisos", org] as const,
  informeSemanal: (org: string) => ["informe-semanal", org] as const,
  perfiles: (org: string, ws?: string) => ["perfiles", org, ws ?? null] as const,
  presupuestos: (org: string, ws?: string) => ["presupuestos", org, ws ?? null] as const,
  proveedores: (org: string, ws?: string) => ["proveedores-contexto", org, ws ?? null] as const,
};
