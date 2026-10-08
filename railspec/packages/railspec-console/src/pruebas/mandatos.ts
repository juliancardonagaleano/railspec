import type {
  Actor,
  DecisionDelegada,
  EstadoUnidad,
  Mandato,
  MandateGetSalida,
  ResumenMandato,
  UnidadDeMandato,
} from "../api/tipos";

// Datos de ejemplo del mandato (contrato 1.11) con la forma de `examples/v1/mandato.json`.

export const HUELLA = "6b33392867f8f8696fd0c72f7e57e17258c6d49cd9ffeb891a19d384ff7de460";

export const julian: Actor = { tipo: "humano", canal: "consola", github_id: 83125327, login: "julian" };
export const agente: Actor = { tipo: "agente", canal: "arnes", agente: "claude-code" };

export const mandato = (o: Partial<Mandato> = {}): Mandato => ({
  alcance: { org: "acme", workspace: "cert" },
  id: "pdf-a",
  version: 2,
  contenido: {
    titulo: "Migrar emisión de certificados a PDF/A",
    objetivo: "Dejar la emisión de certificados en PDF/A con firma, sin tocar el modelo de datos.",
    modo: "desatendido",
    limites: {
      repositorios: ["api"],
      max_unidades: 3,
      rutas_permitidas: ["src/pdf/**", "tests/pdf/**"],
      presupuesto: { costo_usd_max: 25, tokens_max: 2_000_000 },
      reintentos_parada: 1,
      vigencia_horas: 12,
    },
    delegaciones: [
      { id: "D-1", tipo: "pre-decidida", texto: "La biblioteca de PDF es la que ya usa el repositorio." },
      { id: "D-2", tipo: "con-criterio", texto: "Nombres de funciones nuevas: seguir el estilo del módulo vecino." },
      { id: "D-3", tipo: "reservada", texto: "Cualquier cambio de esquema de base de datos." },
    ],
  },
  estado: "aprobado",
  aprobaciones: [
    { actor: julian, en: "2026-09-30T18:00:00Z", caduca_en: "2026-10-01T06:00:00Z", huella: HUELLA, comentario: "Esta noche, solo api." },
  ],
  creado_en: "2026-09-30T17:00:00Z",
  creado_por: julian,
  actualizado_en: "2026-09-30T18:00:00Z",
  actualizado_por: julian,
  ...o,
});

export const decision = (id: string, o: Partial<DecisionDelegada> = {}): DecisionDelegada => ({
  id,
  delegacion: "D-2",
  que: "Llamó `emitir_pdfa` a la función nueva.",
  alternativas: ["emitir_pdf_a"],
  revertir: "Renombrar la función y su uso en src/pdf/emitir.py.",
  fase: "implement",
  orden: "0b8f6c1e-2d3a-4b5c-8d7e-9f0a1b2c3d4e",
  tomada_por: agente,
  en: "2026-09-30T20:00:00Z",
  ...o,
});

export const unidadDeMandato = (o: Partial<UnidadDeMandato> = {}): UnidadDeMandato => ({
  unidad: "0001-emitir-pdfa",
  titulo: "Emitir PDF/A",
  fase: "implement",
  estado: "en-progreso",
  modo: "desatendido",
  consumo: { tokens: 120_000, segundos: 600, costo_usd: 3.5, llamadas: 40 },
  diferida: false,
  decisiones_pendientes: 0,
  actualizado_en: "2026-09-30T21:00:00Z",
  ...o,
});

/** `mandate.get` de un mandato aprobado y vigente con una unidad y una decisión pendiente. */
export const salidaGet = (o: Partial<MandateGetSalida> = {}): MandateGetSalida => ({
  mandato: mandato(),
  huella: HUELLA,
  vigente: true,
  unidades: [unidadDeMandato({ decisiones_pendientes: 1 })],
  consumo: { tokens: 120_000, segundos: 600, costo_usd: 3.5, llamadas: 40 },
  decisiones: [{ unidad: "0001-emitir-pdfa", decision: decision("DD-1") }],
  ...o,
});

export const resumen = (o: Partial<ResumenMandato> = {}): ResumenMandato => ({
  id: "pdf-a",
  titulo: "Migrar emisión de certificados a PDF/A",
  modo: "desatendido",
  estado: "aprobado",
  vigente: true,
  vigente_hasta: "2026-10-01T06:00:00Z",
  unidades: 1,
  max_unidades: 3,
  decisiones_pendientes: 1,
  actualizado_en: "2026-09-30T21:00:00Z",
  ...o,
});

export const estadoUnidad = (o: Partial<EstadoUnidad> = {}): EstadoUnidad => ({
  unidad: { org: "acme", workspace: "cert", unidad: "0001-emitir-pdfa", plan: "pdf-a" },
  version: 7,
  titulo: "Emitir PDF/A",
  dueno: julian,
  repositorios: [{ repositorio: "api", rol: "primario", base_commit: "a".repeat(40) }],
  fase: "implement",
  estado: "en-progreso",
  modo: "desatendido",
  riesgo: "medio",
  perfil: "estandar",
  creado_en: "2026-09-30T10:00:00Z",
  actualizado_en: "2026-09-30T11:00:00Z",
  actualizado_por: julian,
  ...o,
});
