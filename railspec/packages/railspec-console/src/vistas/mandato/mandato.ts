import type {
  AlcanceWorkspace,
  CausaParada,
  Delegacion,
  EstadoMandato,
  Mandato,
  MandateProposeEntrada,
  MandatoContenido,
  ModoConMandato,
  Presupuesto,
  TipoDelegacion,
} from "../../api/tipos";
import type { Tono } from "../../componentes/ui/badge";

// Lógica pura de la pantalla de mandatos (contrato 1.11, docs/mandato.md): textos, estado visible y el
// formulario, con las mismas validaciones que el contrato para no mandar lo que el servidor rechaza.

/** `aprobado` con la vigencia pasada se muestra como «caducado» (el servidor aún no lo ha parado). */
export type EstadoVisible = EstadoMandato | "caducado";

export function estadoVisible(estado: EstadoMandato, vigente: boolean): EstadoVisible {
  return estado === "aprobado" && !vigente ? "caducado" : estado;
}

export const TONO_ESTADO_MANDATO: Record<EstadoVisible, Tono> = {
  propuesto: "info",
  aprobado: "exito",
  parado: "aviso",
  caducado: "aviso",
  revocado: "peligro",
};

export const TEXTO_TIPO_DELEGACION: Record<TipoDelegacion, string> = {
  "pre-decidida": "pre-decidida: ya está decidido, se aplica",
  "con-criterio": "con criterio: se decide dentro del criterio",
  reservada: "reservada: nunca por criterio, decide una persona",
};

/** Ámbito y qué hacer, por causa de parada (tabla «Paradas tipificadas» de docs/mandato.md). */
export const EXPLICACION_CAUSA: Record<CausaParada, { ambito: "mandato" | "unidad"; texto: string }> = {
  "gate-escalado": {
    ambito: "mandato",
    texto:
      "Un gate escaló. En supervisado congela el mandato entero y la unidad espera su decisión: resuelve el checkpoint de la unidad y renueva el mandato.",
  },
  "plan-incompleto": {
    ambito: "mandato",
    texto: "En desatendido, un gate escaló por una causa que afecta a todas las unidades (p. ej. sin gobernanza). Renueva el mandato.",
  },
  "presupuesto-mandato": {
    ambito: "mandato",
    texto: "El consumo total alcanzó el tope de presupuesto del mandato. Edita el tope (vuelve a propuesto) y apruébalo.",
  },
  "mandato-caducado": { ambito: "mandato", texto: "Pasó la vigencia de la aprobación. Renueva el mandato." },
  "mandato-revocado": { ambito: "mandato", texto: "Una persona lo revocó. Es definitivo: redacta otro mandato." },
  "unidad-amparada-fallida": {
    ambito: "unidad",
    texto:
      "En desatendido el gate escaló y la unidad se difirió: espera aquí mientras las demás siguen. Rehabilita el gate o pide cambios.",
  },
  "fuera-de-alcance": {
    ambito: "unidad",
    texto: "Un snapshot tocó rutas que el mandato no permite. Decide: reintentar con indicaciones o rechazar.",
  },
  "decision-reservada": {
    ambito: "unidad",
    texto: "El arnés necesita una decisión que el mandato no delega (reservada o sin delegación). Decídela tú.",
  },
  "reintentos-agotados": {
    ambito: "unidad",
    texto: "La orden falló y no quedan reintentos delegados. Decide cómo seguir.",
  },
};

/** Texto de la entrega de una delegación a una decisión: `reintento` lo aplica el servidor. */
export const etiquetaDelegacion = (d: string): string => (d === "reintento" ? "reintento (automático)" : d);

// ---------------------------------------------------------------- formulario

export interface FilaDelegacion {
  id: string;
  tipo: TipoDelegacion;
  texto: string;
}

/** Todo texto, como lo teclea la persona; `validarMandato` lo convierte al contenido del contrato. */
export interface FormMandato {
  id: string;
  titulo: string;
  objetivo: string;
  modo: ModoConMandato;
  repositorios: string[];
  max_unidades: string;
  /** Una por línea. */
  rutas: string;
  tokens_max: string;
  costo_usd_max: string;
  segundos_max: string;
  llamadas_max: string;
  reintentos_parada: string;
  vigencia_horas: string;
  delegaciones: FilaDelegacion[];
}

/** Defectos del contrato (`LimitesMandato`). */
export const FORM_MANDATO_VACIO: FormMandato = {
  id: "",
  titulo: "",
  objetivo: "",
  modo: "supervisado",
  repositorios: [],
  max_unidades: "5",
  rutas: "",
  tokens_max: "",
  costo_usd_max: "",
  segundos_max: "",
  llamadas_max: "",
  reintentos_parada: "0",
  vigencia_horas: "24",
  delegaciones: [],
};

export const PATRON_SLUG = /^[a-z0-9][a-z0-9-]{0,62}$/;
export const PATRON_DELEGACION = /^D-[0-9]{1,3}$/;
/** Glob relativo a la raíz: ni absoluto, ni con `..`, ni con NUL (el patrón del contrato). */
const glob_valido = (g: string) => g.length <= 512 && !g.startsWith("/") && !/(^|\/)\.\.(\/|$)/.test(g) && !g.includes("\0");

export function formDesdeMandato(m: Mandato): FormMandato {
  const c = m.contenido;
  const p = c.limites.presupuesto;
  const texto = (n: number | null | undefined) => (n === null || n === undefined ? "" : String(n));
  return {
    id: m.id,
    titulo: c.titulo,
    objetivo: c.objetivo,
    modo: c.modo,
    repositorios: [...c.limites.repositorios],
    max_unidades: String(c.limites.max_unidades),
    rutas: c.limites.rutas_permitidas.join("\n"),
    tokens_max: texto(p.tokens_max),
    costo_usd_max: texto(p.costo_usd_max),
    segundos_max: texto(p.segundos_max),
    llamadas_max: texto(p.llamadas_max),
    reintentos_parada: String(c.limites.reintentos_parada),
    vigencia_horas: String(c.limites.vigencia_horas),
    delegaciones: c.delegaciones.map((d) => ({ id: d.id, tipo: d.tipo, texto: d.texto })),
  };
}

/** El siguiente `D-n` libre para una delegación nueva. */
export function siguienteIdDelegacion(filas: readonly { id: string }[]): string {
  const usados = new Set(filas.map((f) => f.id.trim()));
  for (let n = 1; n <= 999; n++) if (!usados.has(`D-${n}`)) return `D-${n}`;
  return "D-1";
}

export const lineasDeTexto = (texto: string): string[] => texto.split("\n").map((l) => l.trim()).filter((l) => l !== "");

/** Entero en [min, max]; `null` si no lo es. */
function entero(texto: string, min: number, max = Number.MAX_SAFE_INTEGER): number | null {
  const t = texto.trim();
  if (!/^[0-9]+$/.test(t)) return null;
  const n = Number(t);
  return n >= min && n <= max ? n : null;
}

/** Tope opcional: vacío = sin tope (`ok` sigue verdadero); con texto, número válido o `ok: false`. */
function tope(texto: string, entero_: boolean): { ok: boolean; valor?: number } {
  const t = texto.trim();
  if (!t) return { ok: true };
  if (entero_) {
    const n = entero(t, 1);
    return n === null ? { ok: false } : { ok: true, valor: n };
  }
  const n = Number(t);
  return Number.isFinite(n) && n > 0 ? { ok: true, valor: n } : { ok: false };
}

export function presupuestoDeForm(f: FormMandato): Presupuesto {
  const p: Presupuesto = {};
  const t = tope(f.tokens_max, true).valor;
  const c = tope(f.costo_usd_max, false).valor;
  const s = tope(f.segundos_max, true).valor;
  const l = tope(f.llamadas_max, true).valor;
  if (t !== undefined) p.tokens_max = t;
  if (c !== undefined) p.costo_usd_max = c;
  if (s !== undefined) p.segundos_max = s;
  if (l !== undefined) p.llamadas_max = l;
  return p;
}

export const tienePresupuesto = (p: Presupuesto): boolean =>
  [p.tokens_max, p.segundos_max, p.costo_usd_max, p.llamadas_max].some((v) => v !== null && v !== undefined);

/**
 * Valida el formulario con las reglas del contrato (`MandatoContenido`, `LimitesMandato`) y arma la entrada de
 * `mandate.propose`. `versionVista` presente = edición (el id no cambia).
 */
export function validarMandato(
  f: FormMandato,
  alcance: AlcanceWorkspace,
  versionVista?: number,
): { errores: string[]; entrada: MandateProposeEntrada | null } {
  const errores: string[] = [];
  const id = f.id.trim();
  const titulo = f.titulo.trim();
  const objetivo = f.objetivo.trim();

  if (!PATRON_SLUG.test(id)) errores.push("El id debe ser un slug: minúsculas, dígitos y guiones (máx. 63), sin empezar por guion.");
  if (!titulo) errores.push("El título es obligatorio.");
  else if (titulo.length > 200) errores.push("El título admite hasta 200 caracteres.");
  if (!objetivo) errores.push("El objetivo es obligatorio.");
  else if (objetivo.length > 4000) errores.push("El objetivo admite hasta 4000 caracteres.");

  if (f.repositorios.length === 0) errores.push("Elige al menos un repositorio.");
  if (f.repositorios.length > 20) errores.push("Un mandato admite hasta 20 repositorios.");

  const maxUnidades = entero(f.max_unidades, 1, 50);
  if (maxUnidades === null) errores.push("El máximo de unidades debe ser un entero de 1 a 50.");

  const rutas = lineasDeTexto(f.rutas);
  if (rutas.length > 100) errores.push("Se admiten hasta 100 rutas permitidas.");
  for (const r of rutas)
    if (!glob_valido(r)) errores.push(`La ruta «${r}» no vale: debe ser relativa a la raíz, sin «..», y de hasta 512 caracteres.`);

  if (!tope(f.tokens_max, true).ok) errores.push("El tope de tokens debe ser un entero mayor que 0.");
  if (!tope(f.costo_usd_max, false).ok) errores.push("El tope de costo (USD) debe ser un número mayor que 0.");
  if (!tope(f.segundos_max, true).ok) errores.push("El tope de segundos debe ser un entero mayor que 0.");
  if (!tope(f.llamadas_max, true).ok) errores.push("El tope de llamadas debe ser un entero mayor que 0.");
  const presupuesto = presupuestoDeForm(f);
  if (f.modo === "desatendido" && !tienePresupuesto(presupuesto))
    errores.push("Un mandato desatendido necesita al menos un tope de presupuesto total (tokens, costo, segundos o llamadas).");

  const reintentos = entero(f.reintentos_parada, 0, 3);
  if (reintentos === null) errores.push("Los reintentos de parada son un entero de 0 a 3.");
  const vigencia = entero(f.vigencia_horas, 1, 168);
  if (vigencia === null) errores.push("La vigencia es un entero de 1 a 168 horas.");

  if (f.delegaciones.length > 50) errores.push("Se admiten hasta 50 delegaciones.");
  const delegaciones: Delegacion[] = f.delegaciones.map((d) => ({ id: d.id.trim(), tipo: d.tipo, texto: d.texto.trim() }));
  const vistos = new Set<string>();
  for (const d of delegaciones) {
    if (!PATRON_DELEGACION.test(d.id)) errores.push(`«${d.id || "(vacío)"}» no es un id de delegación (D-1, D-2…).`);
    else if (vistos.has(d.id)) errores.push(`La delegación ${d.id} está repetida.`);
    vistos.add(d.id);
    if (!d.texto) errores.push(`La delegación ${d.id || "(sin id)"} necesita texto.`);
    else if (d.texto.length > 2000) errores.push(`La delegación ${d.id} admite hasta 2000 caracteres.`);
  }

  if (errores.length > 0 || maxUnidades === null || reintentos === null || vigencia === null) return { errores, entrada: null };
  const contenido: MandatoContenido = {
    titulo,
    objetivo,
    modo: f.modo,
    limites: {
      repositorios: f.repositorios,
      max_unidades: maxUnidades,
      rutas_permitidas: rutas,
      presupuesto,
      reintentos_parada: reintentos,
      vigencia_horas: vigencia,
    },
    delegaciones,
  };
  return { errores, entrada: { alcance, id, contenido, ...(versionVista !== undefined ? { version_vista: versionVista } : {}) } };
}
