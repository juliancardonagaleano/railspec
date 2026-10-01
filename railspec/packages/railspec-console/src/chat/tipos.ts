// Tipos del API de chat de Railspec. Espejo exacto de los nombres JSON del
// servidor (railspec-server, /v1/chat/...).

export type NivelEfectivo = "restringido" | "interno" | "abierto";

export interface Alcance {
  org: string;
  workspace: string;
}

/** Actor autenticado. El servidor puede añadir campos; sólo se tipan los usuales. */
export interface Actor {
  id?: string;
  tipo?: string;
  nombre?: string;
  [campo: string]: unknown;
}

export interface Consumo {
  caracteres: number;
  tope: number;
}

export interface Conversacion {
  id: string;
  alcance: Alcance;
  repositorios: string[];
  autor: Actor;
  nivel_efectivo: NivelEfectivo;
  creada_en: string;
  expira_en: string;
  consumo_fuga: Consumo;
  consumo_fuga_usuario: Consumo;
  bloqueos: number;
  limitada: boolean;
  version_contrato?: string;
}

export type ReglaGate =
  | "esquema"
  | "huella-contexto"
  | "normalizacion"
  | "forma-codigo"
  | "secretos"
  | "alcance"
  | "presupuesto-fuga";

export type ResultadoRegla = "pasa" | "recorta" | "bloquea";

export interface EvaluacionRegla {
  regla: ReglaGate;
  resultado: ResultadoRegla;
  huellas_coincidentes: string[];
  referencias_eliminadas: number;
}

export interface VeredictoGateSalida {
  version_gate: string;
  permitido: boolean;
  reglas: EvaluacionRegla[];
  evaluado_en: string;
}

export interface ReferenciaSimbolo {
  tipo: "simbolo";
  repositorio: string;
  commit: string;
  simbolo: string;
  nombre: string;
  tipo_simbolo: string;
  ruta: string;
}

export interface ReferenciaArchivo {
  tipo: "archivo";
  repositorio: string;
  commit: string;
  ruta: string;
  linea_inicio?: number | null;
  linea_fin?: number | null;
}

export interface ReferenciaNodoGrafo {
  tipo: "nodo-grafo";
  repositorio: string;
  commit: string;
  clase: "cluster" | "proceso";
  id: string;
  nombre: string;
}

export interface ReferenciaUnidad {
  tipo: "unidad";
  workspace: string;
  unidad: string;
}

export interface ReferenciaCriterio {
  tipo: "criterio";
  workspace: string;
  unidad: string;
  criterio: string;
}

export interface ReferenciaGobernanza {
  tipo: "gobernanza";
  id: string;
  proveedor: string;
  hash_version?: string | null;
}

export interface ReferenciaDecision {
  tipo: "decision";
  workspace: string;
  id: string;
}

export type Referencia =
  | ReferenciaSimbolo
  | ReferenciaArchivo
  | ReferenciaNodoGrafo
  | ReferenciaUnidad
  | ReferenciaCriterio
  | ReferenciaGobernanza
  | ReferenciaDecision;

export interface Afirmacion {
  texto: string;
  referencias: Referencia[];
}

export interface RespuestaChat {
  afirmaciones: Afirmacion[];
  preguntas_abiertas: string[];
}

export interface LlamadaTool {
  tool: string;
  entrada_sha256: string;
  duracion_ms: number;
  fragmentos_leidos: number;
}

export type RolMensaje = "usuario" | "asistente";

export interface MensajeChat {
  id: string;
  conversacion: string;
  alcance: Alcance;
  rol: RolMensaje;
  autor: Actor;
  creado_en: string;
  pregunta?: string | null;
  respuesta?: RespuestaChat | null;
  aviso_bloqueo?: ReglaGate[] | null;
  veredicto_gate?: VeredictoGateSalida | null;
  llamadas_tool: LlamadaTool[];
  proveedor?: string | null;
  modelo?: string | null;
  conservar_en_insumo: boolean;
}

export interface RepositorioInsumo {
  repositorio: string;
  rol: string;
  base_commit: string;
}

export interface Insumo {
  formato: "railspec.insumo/v1";
  id: string;
  alcance: Alcance;
  repositorios: RepositorioInsumo[];
  autor: Actor;
  creado_en: string;
  nivel_efectivo: NivelEfectivo;
  conversacion?: string | null;
  objetivo: string;
  hallazgos: Afirmacion[];
  preguntas_abiertas: string[];
  restricciones: string[];
  transcripcion_resumida?: string | null;
  veredicto_gate: VeredictoGateSalida;
  sha256: string;
}

// ---- Cuerpos de petición ----

export interface PeticionCrearConversacion {
  alcance: Alcance;
  repositorios: string[];
}

export interface PeticionExportarInsumo {
  objetivo: string;
  restricciones: string[];
  preguntas_abiertas: string[];
}

// ---- Eventos del stream de una pregunta ----

export interface DatosProgreso {
  paso: number;
  tool: string | null;
  texto: string;
}

export interface DatosErrorStream {
  codigo: string;
  detalle: string;
}

export type EventoChat =
  | { tipo: "pregunta"; mensaje: MensajeChat }
  | { tipo: "progreso"; progreso: DatosProgreso }
  | { tipo: "respuesta"; mensaje: MensajeChat }
  | { tipo: "error"; error: DatosErrorStream }
  | { tipo: "fin" };
