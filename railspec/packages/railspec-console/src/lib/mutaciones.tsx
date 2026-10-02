import { useMutation, useQuery, useQueryClient, type QueryClient, type QueryKey } from "@tanstack/react-query";
import { ErrorApi } from "../api/cliente";
import { Aviso, ErrorVista } from "../componentes/Estados";

/** Último guardado de una pantalla: lo que se muestra como «Guardado · versión N». */
export interface Guardado {
  /** Versión que devolvió el servidor; `null` si el registro no la tiene o no importa. */
  version: number | null;
  /** Avisos del servidor sobre lo guardado (el guardado fue válido, pero conviene leerlos). */
  avisos: string[];
  /** Milisegundos desde la época: cuándo terminó el guardado. */
  en: number;
}

/** Cómo anuncia `useGuardar` el éxito. */
export interface OpcionesGuardado<R> {
  /**
   * Identifica el aviso. Se guarda en la caché de consultas y no en el componente: los formularios
   * se remontan al subir la versión (`key={version}`) y los diálogos se cierran al guardar, así que
   * el aviso lo muestra quien lo lea con `useGuardado(clave)` aunque el formulario ya no exista.
   */
  clave: string;
  /** Versión nueva. Por defecto `r.version` si es un número; `null` la omite. */
  version?: ((r: R) => number | undefined) | null;
  avisos?: (r: R) => string[];
}

const claveGuardado = (clave: string): QueryKey => ["guardado", clave];

function fijarGuardado(cliente: QueryClient, clave: string, guardado: Guardado | null) {
  cliente.setQueryData(claveGuardado(clave), guardado);
}

/**
 * Lee el último guardado de `clave`. No consulta nada: la entrada la escribe `useGuardar` y se
 * descarta sola poco después de que nadie la mire.
 */
export function useGuardado(clave: string | undefined): Guardado | null {
  const consulta = useQuery<Guardado | null>({
    queryKey: claveGuardado(clave ?? ""),
    queryFn: () => null,
    enabled: false,
    staleTime: Infinity,
  });
  return clave ? (consulta.data ?? null) : null;
}

function versionDe<R>(r: R, opciones: OpcionesGuardado<R>): number | null {
  if (opciones.version === null) return null;
  if (opciones.version) return opciones.version(r) ?? null;
  const v = (r as { version?: unknown } | null | undefined)?.version;
  return typeof v === "number" ? v : null;
}

/**
 * Mutación de escritura con bloqueo optimista: al terminar invalida las claves
 * dadas; ante un 409 también las invalida para recargar la versión vigente.
 * Con `aviso` deja constancia del guardado (versión y avisos) bajo su `clave`; el resultado
 * se expone como `guardado` y lo anuncia `<AvisoGuardado>`. Empezar otro guardado lo borra.
 */
export function useGuardar<V, R>(
  fn: (v: V) => Promise<R>,
  invalidar: QueryKey[],
  alExito?: (r: R) => void,
  aviso?: OpcionesGuardado<R>,
) {
  const clienteQuery = useQueryClient();
  const refrescar = () => invalidar.forEach((k) => void clienteQuery.invalidateQueries({ queryKey: k }));
  const guardado = useGuardado(aviso?.clave);
  const mutacion = useMutation({
    mutationFn: fn,
    onMutate: () => {
      if (aviso) fijarGuardado(clienteQuery, aviso.clave, null);
    },
    onSuccess: (r) => {
      if (aviso) fijarGuardado(clienteQuery, aviso.clave, { version: versionDe(r, aviso), avisos: aviso.avisos?.(r) ?? [], en: Date.now() });
      refrescar();
      alExito?.(r);
    },
    onError: (e) => {
      if (e instanceof ErrorApi && e.status === 409) refrescar();
    },
  });
  return { ...mutacion, guardado };
}

const formatoHora = new Intl.DateTimeFormat("es", { timeStyle: "short" });

/** «Guardado · versión N» (o «Guardado con avisos:» y la lista, si el servidor los dio). */
export function AvisoGuardado({ guardado, texto = "Guardado", className }: { guardado: Guardado | null; texto?: string; className?: string }) {
  if (!guardado) return null;
  const detalle = [guardado.version === null ? null : `versión ${guardado.version}`, formatoHora.format(guardado.en)].filter(Boolean).join(" · ");
  if (guardado.avisos.length > 0)
    return (
      <Aviso tono="aviso" {...(className ? { className } : {})}>
        <p className="font-medium">
          {texto} con avisos: <span className="text-xs font-normal">{detalle}</span>
        </p>
        <ul className="list-inside list-disc">
          {guardado.avisos.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
      </Aviso>
    );
  return (
    <Aviso tono="exito" {...(className ? { className } : {})}>
      {texto} · {detalle}
    </Aviso>
  );
}

/** Error de guardado: los 409 se explican como conflicto de versión. */
export function ErrorGuardado({ error }: { error: unknown }) {
  if (!error) return null;
  if (error instanceof ErrorApi && error.status === 409 && !error.codigo) {
    return (
      <Aviso tono="aviso">
        Otra persona modificó este registro mientras lo editabas
        {error.versionActual ? ` (versión actual ${error.versionActual})` : ""}. Se recargaron los datos: cierra, revisa y vuelve a
        guardar. <span className="text-xs">({error.detalle})</span>
      </Aviso>
    );
  }
  return <ErrorVista error={error} />;
}

/** Convierte un texto de número opcional en number | null. */
export function numeroOpcional(texto: string): number | null {
  const t = texto.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}
