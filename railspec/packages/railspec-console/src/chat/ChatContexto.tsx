// Chat de contexto de Railspec: preguntas sobre los repositorios del alcance,
// respuestas que ya pasaron el gate de salida y exportación a insumo.
// Todo el texto se renderiza como texto plano (nunca dangerouslySetInnerHTML).

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { crearClienteChat, ErrorChat } from "./api";
import type { ClienteChat } from "./api";
import { comandoPull, detalleReferencia, etiquetaReferencia, explicarRegla, shaCorto } from "./referencias";
import type { Conversacion, DatosProgreso, Insumo, MensajeChat, NivelEfectivo } from "./tipos";
import "./chat.css";

export interface PropsChatContexto {
  /** Base del API; "" = mismo origen (rutas /v1/...). */
  apiBase: string;
  /** Token Bearer vigente. Se renueva desde el shell; cada petición usa el valor actual. */
  token: string;
  org: string;
  workspace: string;
  /** Si falta (y no hay conversacionId) se pide al usuario antes de crear la conversación. */
  repositorios?: string[];
  /** Si se indica, se carga esa conversación en lugar de crear una nueva. */
  conversacionId?: string;
  /** Se invoca cuando se crea una conversación nueva (p. ej. para reflejar el id en la URL). */
  alCrearConversacion?: (id: string) => void;
}

function mensajeDeError(e: unknown): string {
  if (e instanceof ErrorChat) {
    switch (e.estado) {
      case 401:
        return "Sesión no válida o expirada. Vuelve a iniciar sesión.";
      case 403:
        return `Sin permiso para esta operación: ${e.detalle}`;
      case 404:
        return "La conversación no existe o expiró.";
      case 429:
        return `Conversación limitada tras bloqueos repetidos del gate: ${e.detalle}`;
      default:
        return e.detalle;
    }
  }
  if (e instanceof Error) return e.message;
  return String(e);
}

const ETIQUETA_NIVEL: Record<NivelEfectivo, string> = {
  restringido: "Restringido",
  interno: "Interno",
  abierto: "Abierto",
};

function lineas(texto: string): string[] {
  return texto
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l !== "");
}

// ---------------------------------------------------------------------------

function MedidorFuga({ conversacion }: { conversacion: Conversacion }) {
  const { caracteres, tope } = conversacion.consumo_fuga;
  const pct = tope > 0 ? Math.min(100, Math.round((caracteres / tope) * 100)) : 0;
  const usuario = conversacion.consumo_fuga_usuario;
  const clase = pct >= 90 ? "rs-chat-medidor-alto" : pct >= 70 ? "rs-chat-medidor-medio" : "";
  return (
    <div className="rs-chat-medidor">
      <label className="rs-chat-medidor-etiqueta">
        Presupuesto de fuga: {caracteres.toLocaleString("es")} / {tope.toLocaleString("es")} caracteres
        <meter
          className={`rs-chat-medidor-barra ${clase}`}
          min={0}
          max={tope || 1}
          low={tope * 0.7}
          high={tope * 0.9}
          optimum={0}
          value={caracteres}
        >
          {pct}%
        </meter>
      </label>
      <span className="rs-chat-sutil">
        Tu consumo global: {usuario.caracteres.toLocaleString("es")} / {usuario.tope.toLocaleString("es")}
      </span>
    </div>
  );
}

function AvisoBloqueo({ mensaje }: { mensaje: MensajeChat }) {
  const reglas =
    mensaje.aviso_bloqueo && mensaje.aviso_bloqueo.length > 0
      ? mensaje.aviso_bloqueo
      : (mensaje.veredicto_gate?.reglas ?? []).filter((r) => r.resultado === "bloquea").map((r) => r.regla);
  return (
    <div className="rs-chat-bloqueo" role="note">
      <strong>Respuesta retenida por el gate de salida</strong>
      {reglas.length > 0 ? (
        <ul>
          {reglas.map((r) => (
            <li key={r}>
              <code>{r}</code>: {explicarRegla(r)}
            </li>
          ))}
        </ul>
      ) : (
        <p>El servidor no indicó qué reglas fallaron.</p>
      )}
      <p className="rs-chat-sutil">Reformula la pregunta pidiendo explicaciones o referencias en lugar de código.</p>
    </div>
  );
}

function MensajeAsistente({
  mensaje,
  alCambiarConservar,
  guardando,
}: {
  mensaje: MensajeChat;
  alCambiarConservar: (m: MensajeChat, v: boolean) => void;
  guardando: boolean;
}) {
  const idCheck = useId();
  const permitido = !mensaje.aviso_bloqueo?.length && mensaje.respuesta != null;
  const recortes = (mensaje.veredicto_gate?.reglas ?? []).filter((r) => r.resultado === "recorta");
  return (
    <article className="rs-chat-mensaje rs-chat-asistente" aria-label="Respuesta del asistente">
      {permitido && mensaje.respuesta ? (
        <>
          <ul className="rs-chat-afirmaciones">
            {mensaje.respuesta.afirmaciones.map((a, i) => (
              <li key={i} className="rs-chat-afirmacion">
                <p>{a.texto}</p>
                {a.referencias.length > 0 && (
                  <ul className="rs-chat-referencias" aria-label="Referencias">
                    {a.referencias.map((ref, j) => (
                      <li key={j} className={`rs-chat-chip rs-chat-chip-${ref.tipo}`} title={detalleReferencia(ref)}>
                        {etiquetaReferencia(ref)}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
          {mensaje.respuesta.preguntas_abiertas.length > 0 && (
            <div className="rs-chat-abiertas">
              <h4>Preguntas abiertas</h4>
              <ul>
                {mensaje.respuesta.preguntas_abiertas.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </div>
          )}
          {recortes.length > 0 && (
            <p className="rs-chat-sutil">
              El gate recortó parte de la respuesta ({recortes.map((r) => r.regla).join(", ")}).
            </p>
          )}
          <div className="rs-chat-conservar">
            <input
              id={idCheck}
              type="checkbox"
              checked={mensaje.conservar_en_insumo}
              disabled={guardando}
              onChange={(e) => alCambiarConservar(mensaje, e.target.checked)}
            />
            <label htmlFor={idCheck}>Conservar en insumo</label>
            {guardando && <span className="rs-chat-sutil"> guardando…</span>}
          </div>
        </>
      ) : (
        <AvisoBloqueo mensaje={mensaje} />
      )}
      {(mensaje.modelo || mensaje.llamadas_tool.length > 0) && (
        <p className="rs-chat-meta">
          {mensaje.modelo ? `${mensaje.proveedor ? mensaje.proveedor + " · " : ""}${mensaje.modelo}` : ""}
          {mensaje.llamadas_tool.length > 0
            ? `${mensaje.modelo ? " · " : ""}${mensaje.llamadas_tool.length} consulta(s): ${mensaje.llamadas_tool
                .map((l) => l.tool)
                .join(", ")}`
            : ""}
        </p>
      )}
    </article>
  );
}

function PanelExportar({ cliente, conversacion, deshabilitado }: {
  cliente: ClienteChat;
  conversacion: Conversacion;
  deshabilitado: boolean;
}) {
  const idObjetivo = useId();
  const idRestricciones = useId();
  const idPreguntas = useId();
  const [objetivo, setObjetivo] = useState("");
  const [restricciones, setRestricciones] = useState("");
  const [preguntas, setPreguntas] = useState("");
  const [exportando, setExportando] = useState(false);
  const [error, setError] = useState<{ texto: string; reglas: string[] } | null>(null);
  const [insumo, setInsumo] = useState<Insumo | null>(null);
  const [copiado, setCopiado] = useState<string>("");

  async function exportar(e: FormEvent) {
    e.preventDefault();
    if (objetivo.trim() === "") return;
    setExportando(true);
    setError(null);
    setCopiado("");
    try {
      const ins = await cliente.exportarInsumo(conversacion.id, {
        objetivo: objetivo.trim(),
        restricciones: lineas(restricciones),
        preguntas_abiertas: lineas(preguntas),
      });
      setInsumo(ins);
    } catch (err) {
      setError({
        texto: mensajeDeError(err),
        reglas: err instanceof ErrorChat ? err.reglasFallidas : [],
      });
    } finally {
      setExportando(false);
    }
  }

  async function copiar(texto: string) {
    try {
      await navigator.clipboard.writeText(texto);
      setCopiado("Comando copiado al portapapeles.");
    } catch {
      setCopiado("No se pudo copiar; selecciona el comando y cópialo a mano.");
    }
  }

  function descargar(ins: Insumo) {
    const blob = new Blob([JSON.stringify(ins, null, 2) + "\n"], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `insumo-${ins.id}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  return (
    <section className="rs-chat-exportar" aria-label="Exportar insumo">
      <h3>Exportar insumo</h3>
      <p className="rs-chat-sutil">
        El insumo contiene el objetivo, los hallazgos marcados con «Conservar en insumo» y sus referencias
        (repositorio, ruta, commit, símbolo). Nunca lleva texto de código.
      </p>
      <form onSubmit={exportar}>
        <label htmlFor={idObjetivo}>Objetivo (obligatorio)</label>
        <textarea
          id={idObjetivo}
          required
          rows={2}
          value={objetivo}
          onChange={(e) => setObjetivo(e.target.value)}
        />
        <label htmlFor={idRestricciones}>Restricciones (una por línea)</label>
        <textarea
          id={idRestricciones}
          rows={3}
          value={restricciones}
          onChange={(e) => setRestricciones(e.target.value)}
        />
        <label htmlFor={idPreguntas}>Preguntas abiertas (una por línea)</label>
        <textarea id={idPreguntas} rows={3} value={preguntas} onChange={(e) => setPreguntas(e.target.value)} />
        <button type="submit" disabled={deshabilitado || exportando || objetivo.trim() === ""}>
          {exportando ? "Exportando…" : "Exportar insumo"}
        </button>
      </form>
      {error && (
        <div className="rs-chat-error" role="alert">
          <p>{error.texto}</p>
          {error.reglas.length > 0 && (
            <ul>
              {error.reglas.map((r) => (
                <li key={r}>
                  <code>{r}</code>: {explicarRegla(r)}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {insumo && (
        <div className="rs-chat-insumo" role="status">
          <dl>
            <dt>Id</dt>
            <dd>
              <code>{insumo.id}</code>
            </dd>
            <dt>sha256</dt>
            <dd>
              <code title={insumo.sha256}>{shaCorto(insumo.sha256)}</code>
            </dd>
            <dt>Hallazgos</dt>
            <dd>{insumo.hallazgos.length}</dd>
          </dl>
          <div className="rs-chat-comando">
            <code>{comandoPull(insumo)}</code>
            <button type="button" onClick={() => void copiar(comandoPull(insumo))}>
              Copiar comando
            </button>
            <button type="button" onClick={() => descargar(insumo)}>
              Descargar JSON
            </button>
          </div>
          {copiado && <p className="rs-chat-sutil">{copiado}</p>}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------

type PropsConversacion = Omit<PropsChatContexto, "repositorios"> & { repositorios: string[] };

function ChatConversacion({
  apiBase,
  token,
  org,
  workspace,
  repositorios,
  conversacionId,
  alCrearConversacion,
}: PropsConversacion) {
  // El token se lee del último prop en cada petición, sin recrear el cliente.
  const tokenRef = useRef(token);
  tokenRef.current = token;
  const alCrearRef = useRef(alCrearConversacion);
  alCrearRef.current = alCrearConversacion;

  const cliente = useMemo(
    () => crearClienteChat({ apiBase, obtenerToken: () => tokenRef.current }),
    [apiBase],
  );

  const [conversacion, setConversacion] = useState<Conversacion | null>(null);
  const [mensajes, setMensajes] = useState<MensajeChat[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const [progreso, setProgreso] = useState<DatosProgreso | null>(null);
  const [texto, setTexto] = useState("");
  const [guardando, setGuardando] = useState<Record<string, boolean>>({});
  const abortRef = useRef<AbortController | null>(null);
  const finRef = useRef<HTMLDivElement | null>(null);
  const idEntrada = useId();

  const claveRepos = repositorios.join("\n");
  // Evita crear dos conversaciones en el doble montaje de React.StrictMode.
  const inicioRef = useRef<{ clave: string; promesa: Promise<{ conversacion: Conversacion; mensajes: MensajeChat[] }> } | null>(null);

  useEffect(() => {
    let vigente = true;
    const clave = [apiBase, org, workspace, claveRepos, conversacionId ?? ""].join("\u0000");
    if (!inicioRef.current || inicioRef.current.clave !== clave) {
      const promesa = conversacionId
        ? cliente.obtenerConversacion(conversacionId)
        : cliente
            .crearConversacion({ org, workspace }, claveRepos ? claveRepos.split("\n") : [])
            .then((c) => {
              alCrearRef.current?.(c.id);
              return { conversacion: c, mensajes: [] as MensajeChat[] };
            });
      inicioRef.current = { clave, promesa };
    }
    setCargando(true);
    setError(null);
    setConversacion(null);
    setMensajes([]);
    inicioRef.current.promesa.then(
      (r) => {
        if (!vigente) return;
        setConversacion(r.conversacion);
        setMensajes(r.mensajes);
        setCargando(false);
      },
      (e: unknown) => {
        if (!vigente) return;
        inicioRef.current = null;
        setError(mensajeDeError(e));
        setCargando(false);
      },
    );
    return () => {
      vigente = false;
      abortRef.current?.abort();
    };
  }, [cliente, apiBase, org, workspace, claveRepos, conversacionId]);

  useEffect(() => {
    finRef.current?.scrollIntoView?.({ block: "end" });
  }, [mensajes.length, progreso]);

  const refrescar = useCallback(
    async (id: string) => {
      try {
        const r = await cliente.obtenerConversacion(id);
        setConversacion(r.conversacion);
      } catch {
        // el refresco de contadores es best-effort
      }
    },
    [cliente],
  );

  const enviar = useCallback(async () => {
    const pregunta = texto.trim();
    if (!conversacion || !pregunta || enviando || conversacion.limitada) return;
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setEnviando(true);
    setError(null);
    setProgreso(null);
    setTexto("");
    try {
      for await (const ev of cliente.preguntar(conversacion.id, pregunta, ctrl.signal)) {
        switch (ev.tipo) {
          case "pregunta":
          case "respuesta": {
            const m = ev.mensaje;
            setMensajes((prev) => [...prev.filter((x) => x.id !== m.id), m]);
            break;
          }
          case "progreso":
            setProgreso(ev.progreso);
            break;
          case "error":
            setError(`${ev.error.detalle} (${ev.error.codigo})`);
            break;
          case "fin":
            break;
        }
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        setError("Pregunta cancelada.");
      } else {
        setError(mensajeDeError(e));
        if (e instanceof ErrorChat && e.estado !== 429) setTexto(pregunta);
      }
    } finally {
      abortRef.current = null;
      setEnviando(false);
      setProgreso(null);
      void refrescar(conversacion.id);
    }
  }, [cliente, conversacion, enviando, refrescar, texto]);

  const cambiarConservar = useCallback(
    async (m: MensajeChat, valor: boolean) => {
      if (!conversacion) return;
      setGuardando((g) => ({ ...g, [m.id]: true }));
      try {
        const nuevo = await cliente.marcarConservar(conversacion.id, m.id, valor);
        setMensajes((prev) => prev.map((x) => (x.id === nuevo.id ? nuevo : x)));
      } catch (e) {
        setError(mensajeDeError(e));
      } finally {
        setGuardando((g) => {
          const { [m.id]: _omitido, ...resto } = g;
          return resto;
        });
      }
    },
    [cliente, conversacion],
  );

  function alTeclear(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void enviar();
    }
  }

  if (cargando) {
    return (
      <div className="rs-chat" aria-busy="true">
        <p className="rs-chat-sutil" role="status">
          Preparando conversación…
        </p>
      </div>
    );
  }

  if (!conversacion) {
    return (
      <div className="rs-chat">
        <div className="rs-chat-error" role="alert">
          {error ?? "No se pudo abrir la conversación."}
        </div>
      </div>
    );
  }

  const limitada = conversacion.limitada;
  const conservados = mensajes.filter((m) => m.rol === "asistente" && m.conservar_en_insumo).length;

  return (
    <div className="rs-chat">
      <header className="rs-chat-cabecera">
        <div>
          <h2>Chat de contexto</h2>
          <p className="rs-chat-sutil">
            {conversacion.alcance.org} / {conversacion.alcance.workspace} · {conversacion.repositorios.join(", ") || "sin repositorios"}
          </p>
          <p className="rs-chat-sutil">
            Expira: {new Date(conversacion.expira_en).toLocaleString("es")}
            {conversacion.bloqueos > 0 ? ` · bloqueos: ${conversacion.bloqueos}` : ""}
          </p>
        </div>
        <div className="rs-chat-estado">
          <span
            className={`rs-chat-nivel rs-chat-nivel-${conversacion.nivel_efectivo}`}
            title="Nivel efectivo de confidencialidad de la conversación"
          >
            Nivel: {ETIQUETA_NIVEL[conversacion.nivel_efectivo] ?? conversacion.nivel_efectivo}
          </span>
          <MedidorFuga conversacion={conversacion} />
        </div>
      </header>

      <section className="rs-chat-lista" aria-label="Mensajes">
        {mensajes.length === 0 && (
          <p className="rs-chat-sutil">
            Pregunta sobre la arquitectura, los flujos o los símbolos de los repositorios del alcance. Las respuestas
            traen afirmaciones con referencias, nunca código.
          </p>
        )}
        {mensajes.map((m) =>
          m.rol === "usuario" ? (
            <article key={m.id} className="rs-chat-mensaje rs-chat-usuario" aria-label="Tu pregunta">
              <p>{m.pregunta ?? ""}</p>
            </article>
          ) : (
            <MensajeAsistente
              key={m.id}
              mensaje={m}
              guardando={!!guardando[m.id]}
              alCambiarConservar={(msg, v) => void cambiarConservar(msg, v)}
            />
          ),
        )}
        <div ref={finRef} />
      </section>

      <p className="rs-chat-progreso" aria-live="polite" role="status">
        {enviando ? (progreso ? `Paso ${progreso.paso}: ${progreso.texto}` : "Pensando…") : ""}
      </p>

      {error && (
        <div className="rs-chat-error" role="alert">
          {error}
        </div>
      )}

      {limitada && (
        <div className="rs-chat-bloqueo" role="note">
          Esta conversación quedó limitada tras bloqueos repetidos del gate de salida. Ya no admite preguntas; puedes
          exportar lo conservado o abrir una conversación nueva.
        </div>
      )}

      <form
        className="rs-chat-entrada"
        onSubmit={(e) => {
          e.preventDefault();
          void enviar();
        }}
      >
        <label htmlFor={idEntrada}>Pregunta</label>
        <textarea
          id={idEntrada}
          rows={3}
          value={texto}
          disabled={enviando || limitada}
          placeholder="Enter envía · Shift+Enter nueva línea"
          onChange={(e) => setTexto(e.target.value)}
          onKeyDown={alTeclear}
        />
        <div className="rs-chat-acciones">
          <button type="submit" disabled={enviando || limitada || texto.trim() === ""}>
            Enviar
          </button>
          {enviando && (
            <button type="button" onClick={() => abortRef.current?.abort()}>
              Cancelar
            </button>
          )}
        </div>
      </form>

      <PanelExportar
        cliente={cliente}
        conversacion={conversacion}
        deshabilitado={enviando}
      />
      <p className="rs-chat-sutil">Hallazgos marcados para el insumo: {conservados}</p>
    </div>
  );
}

/** Pide los repositorios del alcance cuando el shell no los indica. */
function SelectorRepositorios({ alConfirmar }: { alConfirmar: (repos: string[]) => void }) {
  const id = useId();
  const [valor, setValor] = useState("");
  const repos = valor
    .split(",")
    .map((r) => r.trim())
    .filter((r) => r !== "");
  return (
    <div className="rs-chat">
      <h2>Chat de contexto</h2>
      <form
        className="rs-chat-entrada"
        onSubmit={(e) => {
          e.preventDefault();
          if (repos.length > 0) alConfirmar(repos);
        }}
      >
        <label htmlFor={id}>Repositorios a consultar (separados por coma)</label>
        <input
          id={id}
          type="text"
          required
          placeholder="acme/api, acme/web"
          value={valor}
          onChange={(e) => setValor(e.target.value)}
        />
        <button type="submit" disabled={repos.length === 0}>
          Iniciar conversación
        </button>
      </form>
    </div>
  );
}

export function ChatContexto(props: PropsChatContexto) {
  const [elegidos, setElegidos] = useState<string[] | null>(null);
  const repositorios = props.repositorios ?? elegidos ?? (props.conversacionId ? [] : null);
  if (repositorios === null) return <SelectorRepositorios alConfirmar={setElegidos} />;
  return <ChatConversacion {...props} repositorios={repositorios} />;
}

export default ChatContexto;
