/**
 * Tipos de la API de la consola, escritos a mano y fieles a
 * `railspec/schemas/v1/*.schema.json` y `tools.json` (contrato 1.3).
 * Solo se modela lo que la consola usa.
 */

export const FASES = ["research", "spec", "plan", "tasks", "aprobacion", "implement", "done"] as const;
export type Fase = (typeof FASES)[number];

export const GATES = ["spec", "plan", "tasks", "codigo"] as const;
export type GateFase = (typeof GATES)[number];

export const ESTADOS_FASE = ["en-progreso", "bloqueado", "completado"] as const;
export type EstadoFase = (typeof ESTADOS_FASE)[number];

export const MODOS = ["interactivo", "semi-autonomo", "supervisado", "desatendido"] as const;
export type Modo = (typeof MODOS)[number];

export type Riesgo = "bajo" | "medio" | "alto";
export type RiesgoImpacto = Riesgo | "critico";
export type Severidad = "alta" | "media" | "baja";
export type Veredicto = "aprobado" | "refinado" | "escalado";
export type Decision = "aprobado" | "cambios-solicitados" | "rechazado";

export const PERFILES = ["ligero", "estandar", "profundo"] as const;
export type Perfil = (typeof PERFILES)[number];

export const EFFORTS = ["low", "medium", "high", "xhigh", "max"] as const;
export type Effort = (typeof EFFORTS)[number];

export const PROVEEDORES = ["foundry", "anthropic", "compatible"] as const;
export type Proveedor = (typeof PROVEEDORES)[number];

export type Canal = "arnes" | "consola" | "ci" | "servidor";

export const ROLES = ["lector", "desarrollador", "workspace-admin", "org-admin"] as const;
export type Rol = (typeof ROLES)[number];

export const NIVELES_CODIGO = ["restringido", "interno", "abierto"] as const;
export type NivelCodigo = (typeof NIVELES_CODIGO)[number];

export type RolRepositorio = "primario" | "transversal";

export interface Actor {
  tipo: "humano" | "servicio" | "agente";
  canal: Canal;
  github_id?: number | null;
  login?: string | null;
  agente?: string | null;
  en_nombre_de?: number | null;
  proveedor_identidad?: string;
}

export interface Auditoria {
  creado_por: Actor;
  creado_en: string;
  actualizado_por: Actor;
  actualizado_en: string;
}

// ---------------------------------------------------------------- sesión

export interface ConfigAuth {
  github: boolean;
  desarrollo: boolean;
}

export interface WorkspaceYo {
  workspace: string;
  nombre: string;
  rol: Rol;
}

export interface OrganizacionYo {
  id: string;
  nombre: string;
  rol: Rol | null;
  workspaces: WorkspaceYo[];
}

export interface Yo {
  login: string;
  github_id: number;
  plataforma_admin: boolean;
  organizaciones: OrganizacionYo[];
}

export interface TokenChat {
  token: string;
  expira_en: string;
}

// ---------------------------------------------------------------- errores

export type CodigoError =
  | "version-contrato-no-soportada"
  | "fuera-de-alcance"
  | "no-encontrado"
  | "conflicto-version"
  | "orden-no-vigente"
  | "base-commit-distinto"
  | "secuencia-duplicada"
  | "snapshot-invalido"
  | "secretos-detectados"
  | "checkpoint-ya-resuelto"
  | "perfil-insatisfacible"
  | "conversion-no-permitida"
  | "unidad-no-cerrada"
  | "presupuesto-agotado"
  | "secuencia-con-hueco";

export interface CuerpoError {
  detalle?: string;
  codigo?: CodigoError;
  version_estado?: number | null;
  version_actual?: number | null;
  errores?: { ruta: string; mensaje: string }[];
}

// ---------------------------------------------------------------- unidades

export interface AlcanceWorkspace {
  org: string;
  workspace: string;
}

export interface AlcanceUnidad extends AlcanceWorkspace {
  unidad: string;
  plan?: string | null;
}

export interface ResumenUnidad {
  unidad: string;
  titulo: string;
  fase: Fase;
  estado: EstadoFase;
  modo: Modo;
  riesgo: Riesgo;
  repositorio_primario: string;
  dueno_login: string | null;
  integrada: boolean;
  actualizado_en: string;
  /** No está en el contrato de `unit.list`; se pinta si el servidor lo añade. */
  checkpoint_pendiente?: boolean | null;
}

export interface UnitListEntrada {
  alcance: AlcanceWorkspace;
  repositorio?: string | null;
  fase?: Fase[];
  estado?: EstadoFase[];
  integradas?: boolean | null;
  cursor?: string | null;
  limite?: number;
}

export interface UnitListSalida {
  unidades: ResumenUnidad[];
  cursor_siguiente: string | null;
}

/** Versión del contrato que declara la SPA al arrancar una unidad (`version_contrato_cliente`). */
export const VERSION_CONTRATO_CLIENTE = "1.4";

/** El primero es el primario donde se trabaja; `base_commit` es el sha completo (40 hex). */
export interface RepositorioInicio {
  repositorio: string;
  rama: string;
  base_commit: string;
}

export interface UnitStartEntrada {
  alcance: AlcanceWorkspace;
  repositorios: RepositorioInicio[];
  titulo: string;
  pedido: string;
  /** Ids (uuid) de insumos exportados por el chat de este workspace. */
  insumos?: string[];
  version_contrato_cliente: string;
}

export interface UnitStartSalida {
  estado: EstadoUnidad;
  version_contrato_negociada?: string;
}

export interface Checkpoint {
  id: string;
  tipo: "aprobar-spec" | "aprobar-plan" | "paquete-aprobacion" | "parada" | "gate-escalado";
  fase: Fase;
  pregunta: string;
  abierto_en: string;
  artefacto_sha256?: string | null;
}

export interface Cita {
  ruta?: string | null;
  seccion?: string | null;
  linea_inicio?: number | null;
  linea_fin?: number | null;
}

/**
 * Hallazgo como lo sirve la consola: sin `evidencia` ni `propuesta` (texto libre de los críticos que
 * puede citar código); la API los quita y la consola nunca muestra código.
 */
export interface Hallazgo {
  id: string;
  gate: GateFase;
  lente: string;
  severidad: Severidad;
  titulo: string;
  cita: Cita;
  criterio?: string | null;
  refutado?: boolean;
}

export interface Rehabilitacion {
  actor: Actor;
  en: string;
  motivo: string;
}

export interface ResultadoGate {
  veredicto: Veredicto;
  iteraciones: number;
  gobernanza_consultada: "si" | "parcial" | "no";
  cerrado_en: string;
  causa?: string | null;
  criticos?: string[];
  hallazgos?: Hallazgo[];
  refutador?: boolean;
  rehabilitado?: Rehabilitacion | null;
}

export interface Integracion {
  actor: Actor;
  en: string;
  especificacion_viva: string;
  pr_url?: string | null;
}

export interface RepositorioUnidad {
  repositorio: string;
  rol: RolRepositorio;
  base_commit: string;
  rama?: string | null;
}

export interface Consumo {
  tokens?: number;
  segundos?: number;
  costo_usd?: number;
}

export interface EstadoUnidad {
  unidad: AlcanceUnidad;
  version: number;
  titulo: string;
  dueno: Actor;
  repositorios: RepositorioUnidad[];
  fase: Fase;
  estado: EstadoFase;
  modo: Modo;
  riesgo: Riesgo;
  perfil: Perfil;
  creado_en: string;
  actualizado_en: string;
  actualizado_por: Actor;
  checkpoint_pendiente?: Checkpoint | null;
  gates?: Partial<Record<GateFase, ResultadoGate>>;
  integracion?: Integracion | null;
  consumo?: Consumo;
  depende_de?: string[];
  pedido?: string | null;
  orden_vigente?: string | null;
}

export interface ResumenOrden {
  id: string;
  secuencia: number;
  tipo: "redactar" | "refinar" | "implementar" | "validar";
  fase: Fase;
  repositorio: string;
  base_commit: string;
  emitida_en: string;
  artefacto?: string | null;
  grupo?: string | null;
  tareas?: { id: string; descripcion: string; criterios: string[] }[];
  criterios: { id: string; texto: string }[];
  alcance: { permitidos: string[]; prohibidos: string[] };
  reporte?: {
    resultado: string;
    reportado_en: string;
    tareas_completadas: string[];
    archivos: { ruta: string; estado: string }[];
  } | null;
}

export interface DetalleUnidad {
  estado: EstadoUnidad;
  orden_vigente: ResumenOrden | null;
}

export interface CargaEvento {
  tipo:
    | "snapshot.subido"
    | "orden.reportada"
    | "commit.empujado"
    | "orden.emitida"
    | "veredicto.emitido"
    | "estado.actualizado"
    | "checkpoint.solicitado"
    | "checkpoint.resuelto"
    | "unidad.integrada";
  [clave: string]: unknown;
}

export interface EventoSync {
  id: string;
  direccion: "remoto-a-local" | "local-a-remoto";
  secuencia: number;
  emitido_en: string;
  actor: Actor;
  causado_por?: string | null;
  carga: CargaEvento;
}

export interface LineaDeTiempo {
  eventos: EventoSync[];
  ordenes: ResumenOrden[];
}

export interface TareaTraza {
  id: string;
  descripcion: string;
  grupo?: string | null;
  completada: boolean;
  orden?: string | null;
}

export interface SimboloTraza {
  repositorio: string;
  simbolo: string;
  nombre: string;
  tipo: string;
  ruta: string;
}

export interface CriterioTraza {
  id: string;
  /** `null` cuando el criterio CA-NN solo aparece en tareas y ninguna orden lo redacta. */
  texto: string | null;
  tareas: TareaTraza[];
  archivos: { repositorio: string; ruta: string; estado: string }[];
  simbolos: SimboloTraza[];
  hallazgos: { id: string; gate: GateFase; severidad: Severidad; titulo: string; refutado: boolean }[];
}

export interface Trazabilidad {
  criterios: CriterioTraza[];
  sin_criterio: { tareas: TareaTraza[] };
}

// ---------------------------------------------------------------- grafo

export const TIPOS_SIMBOLO = ["modulo", "clase", "interfaz", "funcion", "metodo", "variable", "otro"] as const;
export type TipoSimbolo = (typeof TIPOS_SIMBOLO)[number];

export const RELACIONES = ["llama", "importa", "hereda", "implementa", "define", "prueba"] as const;
export type Relacion = (typeof RELACIONES)[number];

export interface RefSimbolo {
  tipo: "simbolo";
  repositorio: string;
  commit: string;
  simbolo: string;
  nombre: string;
  tipo_simbolo: TipoSimbolo;
  ruta: string;
}

export interface RefNodoGrafo {
  tipo: "nodo-grafo";
  repositorio: string;
  commit: string;
  clase: "cluster" | "proceso";
  id: string;
  nombre: string;
}

export interface RefArchivo {
  tipo: "archivo";
  repositorio: string;
  commit: string;
  ruta: string;
  linea_inicio?: number | null;
  linea_fin?: number | null;
}

/** Contrato 1.4 (verbo `trace`): criterio de aceptación de una unidad. */
export interface RefCriterio {
  tipo: "criterio";
  workspace: string;
  unidad: string;
  criterio: string;
}

export type RefGrafo = RefSimbolo | RefNodoGrafo | RefArchivo | RefCriterio;

export interface ResultadoGrafo {
  ref: RefGrafo;
  puntuacion?: number | null;
  relacion?: Relacion | null;
  distancia?: number | null;
  riesgo?: RiesgoImpacto | null;
}

export type ConsultaGrafo =
  | { verbo: "search"; texto: string; tipos?: TipoSimbolo[]; semantica?: boolean }
  | { verbo: "resolve"; nombre: string }
  | { verbo: "traverse"; simbolo: string; relaciones?: Relacion[]; direccion: "upstream" | "downstream"; profundidad: number }
  | { verbo: "related"; simbolo: string }
  // Contrato 1.4: impacto de una unidad y traza símbolo ↔ criterio.
  | { verbo: "impact"; profundidad: number }
  | { verbo: "trace"; simbolo: string }
  | { verbo: "trace"; criterio: string };

export interface GraphQueryEntrada {
  alcance: AlcanceWorkspace;
  repositorios?: string[];
  unidad?: string | null;
  consulta: ConsultaGrafo;
  limite?: number;
}

export interface GraphQuerySalida {
  resultados: ResultadoGrafo[];
  commits: Record<string, string>;
  truncado?: boolean;
}

/** `GET …/unidades/{u}/commit-integrable`: sugerencia de `commit_integrado` al integrar. */
export interface CommitIntegrable {
  repositorio: string | null;
  rama: string | null;
  /** Punta de la rama por defecto en el clon del servidor; `null` si no hay (ver `motivo`). */
  commit: string | null;
  /** Commit al que llegó el índice canónico del grafo, si se pudo leer. */
  commit_indexado: string | null;
  motivo: "sin-vinculo" | "sin-clones" | "sin-clon" | null;
}

export interface RepositorioGrafo {
  repositorio: string;
  nivel_codigo: NivelCodigo;
  rol: RolRepositorio;
  commit: string | null;
}

// ---------------------------------------------------------------- telemetría

export const CLAVES_TELEMETRIA = [
  "workspace",
  "repositorio",
  "unidad",
  "nodo",
  "fase",
  "tier",
  "proveedor",
  "modelo",
  "veredicto",
] as const;
export type ClaveTelemetria = (typeof CLAVES_TELEMETRIA)[number];

export interface TelemetryQueryEntrada {
  org: string;
  workspace?: string | null;
  desde: string;
  hasta: string;
  agrupar_por: ClaveTelemetria[];
  filtros?: Partial<Record<ClaveTelemetria, string>>;
}

export interface FilaTelemetria {
  claves: Partial<Record<ClaveTelemetria, string | null>>;
  llamadas: number;
  tokens_entrada: number;
  tokens_salida: number;
  tokens_cache_lectura: number;
  costo_usd: number;
  duracion_ms: number;
}

export interface TelemetryQuerySalida {
  filas: FilaTelemetria[];
}

export interface ResumenGate {
  total: number;
  aprobado: number;
  refinado: number;
  escalado: number;
  iteraciones_media: number | null;
  hallazgos: Partial<Record<Severidad, number>>;
  rehabilitados: number;
}

export interface ResumenWorkspace {
  unidades: {
    total: number;
    por_fase: Partial<Record<Fase, number>>;
    por_estado: Partial<Record<EstadoFase, number>>;
    integradas: number;
    checkpoints_pendientes: number;
  };
  gates: Partial<Record<GateFase, ResumenGate>>;
  gasto: { mes_usd: number; presupuesto_mensual_usd: number | null; desde: string };
  grafo: { repositorio: string; nivel_codigo: NivelCodigo; commit: string | null }[];
}

// ---------------------------------------------------------------- auditoría

export const EVENTOS_AUDITORIA = [
  "llamada-modelo",
  "lectura-codigo",
  "resolucion-checkpoint",
  "rehabilitacion-gate",
  "integracion",
  "cambio-nivel",
  "cambio-configuracion",
  "desvinculo-repositorio",
  "bloqueo-gate-salida",
] as const;
export type EventoAuditoria = (typeof EVENTOS_AUDITORIA)[number];

export interface RegistroAuditoria {
  id: string;
  alcance: Record<string, string | null>;
  evento: EventoAuditoria;
  actor: Actor;
  en: string;
  repositorio?: string | null;
  unidad?: string | null;
  conversacion?: string | null;
  nivel_codigo?: NivelCodigo | null;
  proveedor?: Proveedor | null;
  modelo?: string | null;
  region?: string | null;
  sha256_enviado?: string | null;
  detalle: Record<string, unknown>;
}

export interface PaginaAuditoria {
  registros: RegistroAuditoria[];
  cursor_siguiente: string | null;
}

export interface FiltrosAuditoria {
  evento?: string;
  repositorio?: string;
  unidad?: string;
  desde?: string;
  hasta?: string;
  cursor?: string;
  limite?: number;
}

// ---------------------------------------------------------------- administración

export interface Organizacion {
  id: string;
  nombre: string;
  github_org?: string | null;
  region_datos: string;
  version: number;
  auditoria: Auditoria;
}

export interface Workspace {
  alcance: AlcanceWorkspace;
  nombre: string;
  zona_datos_azure?: string | null;
  perfil_por_defecto: Perfil;
  version: number;
  auditoria: Auditoria;
}

export type SujetoRol =
  | { tipo: "usuario"; github_id: number; login?: string | null }
  | { tipo: "equipo"; github_org: string; equipo: string; equipo_id: number };

export type SujetoRolNuevo =
  | { tipo: "usuario"; login: string }
  | { tipo: "equipo"; github_org: string; equipo: string; equipo_id: number };

export interface AsignacionRol {
  id: string;
  org: string;
  workspace: string | null;
  rol: Rol;
  sujeto: SujetoRol;
  version: number;
  auditoria: Auditoria;
}

export interface PoliticaChat {
  hosting: "azure-zona-datos" | "cualquiera";
  huella_tokens_n: number;
  presupuesto_fuga_conversacion: number;
  presupuesto_fuga_usuario_dia: number;
  fragmentos_en_respuesta?: boolean;
  modelos_permitidos?: string[];
  permitido?: boolean;
}

export interface VinculoRepositorio {
  alcance: AlcanceWorkspace & { repositorio: string };
  url: string;
  rol: RolRepositorio;
  rama_por_defecto: string;
  nivel_codigo: NivelCodigo;
  chat_contexto_codigo: PoliticaChat;
  retencion_snapshots_dias: number;
  exclusiones: string[];
  version: number;
  auditoria: Auditoria;
}

export interface EscrituraVinculo {
  url: string;
  rol: RolRepositorio;
  rama_por_defecto?: string;
  nivel_codigo?: NivelCodigo;
  chat_contexto_codigo?: PoliticaChat;
  retencion_snapshots_dias?: number;
  exclusiones?: string[];
  version?: number;
  motivo?: string;
}

// ---------------------------------------------------------------- configuración

export interface ModeloCatalogo {
  org: string;
  proveedor: Proveedor;
  modelo: string;
  despliegue?: string | null;
  hosting: "azure" | "anthropic" | "externo";
  region?: string | null;
  capacidades: {
    efforts?: Effort[];
    thinking?: boolean;
    structured_outputs?: boolean;
    contexto_max_tokens: number;
  };
  leido_en: string;
}

/** Códigos estables de un fallo al leer el catálogo de un proveedor (el texto ya viene saneado del servidor). */
export type CodigoErrorCatalogo =
  | "autenticacion"
  | "permiso"
  | "no-encontrado"
  | "limite"
  | "proveedor"
  | "red"
  | "tiempo"
  | "forma"
  | "configuracion"
  | "interno";

export interface ErrorLecturaCatalogo {
  codigo: CodigoErrorCatalogo;
  detalle: string;
}

/** Último intento de leer el catálogo de un proveedor para la organización. */
export interface IntentoCatalogo {
  intento_en: string;
  origen: "consola" | "automatica";
  por: string | null;
  resultado: "ok" | "error";
  /** La sincronización no llamó al proveedor: su última lectura era reciente. */
  reutilizada: boolean;
  modelos: number;
  leido_en: string | null;
  error: ErrorLecturaCatalogo | null;
}

export interface EstadoProveedorCatalogo {
  proveedor: Proveedor;
  /** El servidor tiene credenciales y fuente para este proveedor. */
  configurado: boolean;
  /** De dónde sale el catálogo: `declarados`, `proyecto` o `api`. */
  fuentes: string[];
  modelos: number;
  leido_en: string | null;
  ultimo_intento: IntentoCatalogo | null;
  aviso: string | null;
}

export interface EstadoCatalogo {
  /** El servidor tiene al menos un proveedor de modelos que leer. */
  sincronizable: boolean;
  proveedores: EstadoProveedorCatalogo[];
}

export interface ResultadoSincronizacion {
  resultado: "ok" | "parcial" | "error";
  modelos: number;
  proveedores: {
    proveedor: Proveedor;
    intento_en: string;
    resultado: "ok" | "error";
    reutilizada: boolean;
    modelos: number;
    leido_en: string | null;
    error: ErrorLecturaCatalogo | null;
  }[];
}

export interface RequisitoRol {
  modelo: Partial<Record<Proveedor, string>>;
  effort?: Effort | null;
  structured_outputs?: boolean;
  contexto_min_tokens?: number | null;
}

export interface TopeGate {
  criticos: number;
  iteraciones: number;
  adversarial: boolean;
}

export interface PerfilConfig {
  org: string;
  workspace: string | null;
  nombre: Perfil;
  roles: Record<string, RequisitoRol>;
  gate: Partial<Record<Riesgo, TopeGate>>;
  exploradores: Partial<Record<Riesgo, number>>;
  /** Suscripción de la que salen los modelos del perfil (contrato 1.6); `null` = respaldo del servidor. */
  suscripcion?: string | null;
  version: number;
  auditoria: Auditoria;
}

export interface EscrituraPerfil {
  roles: Record<string, RequisitoRol>;
  gate: Partial<Record<Riesgo, TopeGate>>;
  exploradores: Partial<Record<Riesgo, number>>;
  suscripcion?: string | null;
  version?: number;
}

export interface Presupuesto {
  tokens_max?: number | null;
  segundos_max?: number | null;
  costo_usd_max?: number | null;
}

export interface PresupuestoConfig {
  org: string;
  workspace: string | null;
  por_unidad: Presupuesto;
  por_fase?: Partial<Record<Fase, Presupuesto>>;
  mensual_usd?: number | null;
  version: number;
  auditoria: Auditoria;
}

export const ROLES_CONTEXTO = ["gobernanza", "grafo-de-codigo", "memoria", "documentacion"] as const;
export type RolContexto = (typeof ROLES_CONTEXTO)[number];

export interface ProveedorContexto {
  org: string;
  workspace: string | null;
  rol: RolContexto;
  nombre: string;
  url: string;
  /** Solo la reciben org-admin y plataforma; el resto ve `credencial_configurada`. */
  credencial_ref?: string | null;
  credencial_configurada?: boolean;
  politica_fallo: "estricta" | "blanda";
  fases?: Fase[];
  presupuesto_tokens?: number | null;
  version: number;
  auditoria: Auditoria;
}

export interface EscrituraProveedorContexto {
  url: string;
  credencial_ref?: string | null;
  politica_fallo: "estricta" | "blanda";
  fases?: Fase[];
  presupuesto_tokens?: number | null;
  version?: number;
}

// ---------------------------------------------------------------- suscripciones de modelos (1.6)

export const AUTENTICACIONES = ["api-key", "identidad-servidor"] as const;
export type Autenticacion = (typeof AUTENTICACIONES)[number];

export const PROTOCOLOS_COMPATIBLE = ["anthropic-messages", "openai-chat"] as const;
export type ProtocoloCompatible = (typeof PROTOCOLOS_COMPATIBLE)[number];

/** Tarifa en USD por millón de tokens (1.8). */
export interface PrecioModelo {
  entrada: number;
  salida: number;
  cache_lectura?: number;
}

/** Un servicio conocido de proveedores compatibles: el servidor fija sus endpoints. */
export interface ServicioCompatible {
  id: string;
  nombre: string;
  endpoint: string | null;
  endpoint_mensajes: string | null;
  nota: string;
}

export interface ModeloSuscripcion {
  modelo: string;
  despliegue?: string | null;
  sku?: string | null;
  region?: string | null;
  capacidades: ModeloCatalogo["capacidades"];
  origen: "descubierto" | "declarado";
  seleccionado: boolean;
  /** Estaba elegido y la última lectura ya no lo trae: no se sirve hasta descubrir de nuevo o declararlo. */
  ausente: boolean;
  visto_en?: string | null;
  /** Despliegue o id del modelo: lo que se elige en un perfil. */
  clave: string;
  hosting: "azure" | "anthropic" | "externo";
  /** Puede servir a restringido/interno (Azure y con región conocida que no sea global). */
  restringible: boolean;
  /** Solo compatibles (1.8): la API que habla el modelo y su tarifa si se declaró. */
  protocolo?: ProtocoloCompatible | null;
  precio_usd_mtok?: PrecioModelo | null;
}

export interface LecturaSuscripcion {
  en: string;
  por?: string | null;
  resultado: "ok" | "error";
  modelos: number;
  error_codigo?: string | null;
  error_detalle?: string | null;
}

/** Nunca lleva la clave: solo `clave_configurada`. */
export interface Suscripcion {
  org: string;
  id: string;
  nombre: string;
  proveedor: Proveedor;
  endpoint?: string | null;
  /** Solo compatibles (1.8): URL base de la API de mensajes de Anthropic. */
  endpoint_mensajes?: string | null;
  /** Solo compatibles (1.8): id del servicio conocido; sin él, los endpoints son propios. */
  servicio?: string | null;
  proyecto?: string | null;
  region?: string | null;
  zona_datos?: string | null;
  autenticacion: Autenticacion;
  clave_configurada: boolean;
  clave_actualizada_en?: string | null;
  habilitada: boolean;
  modelos: ModeloSuscripcion[];
  ultima_lectura?: LecturaSuscripcion | null;
  perfiles: { nombre: Perfil; workspace: string | null }[];
  version: number;
  auditoria: Auditoria;
}

export interface ListaSuscripciones {
  cifrado: { disponible: boolean; variable: string };
  servicios_compatibles?: ServicioCompatible[];
  suscripciones: Suscripcion[];
}

export interface EscrituraSuscripcion {
  proveedor: Proveedor;
  nombre: string;
  autenticacion: Autenticacion;
  endpoint?: string | null;
  endpoint_mensajes?: string | null;
  servicio?: string | null;
  proyecto?: string | null;
  region?: string | null;
  zona_datos?: string | null;
  habilitada: boolean;
  /** Solo escritura. Vacía o ausente = conservar la guardada. */
  clave?: string | null;
  version?: number;
}

export interface DeclaracionModelo {
  modelo: string;
  /** Foundry: nombre del despliegue y su SKU. */
  despliegue?: string;
  sku?: string;
  /** Compatibles (1.8): la API que habla el modelo y, si se sabe, su tarifa. */
  protocolo?: ProtocoloCompatible;
  precio_usd_mtok?: PrecioModelo | null;
  structured_outputs?: boolean | null;
  contexto?: number | null;
  version: number;
}

export interface ResultadoDescubrimiento {
  resultado: "ok" | "error";
  modelos: number;
  suscripcion: Suscripcion;
  codigo?: string;
  detalle?: string;
}
