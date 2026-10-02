import { Component, Suspense, useCallback, useMemo, useRef, useState, type ComponentType, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { claves, repositorios } from "../../api/endpoints";
import { crearClienteChat } from "../../chat/api";
import type { Insumo } from "../../chat/tipos";
import { Aviso, Cargando, Encabezado, ErrorVista } from "../../componentes/Estados";
import { Button } from "../../componentes/ui/button";
import { alcanza } from "../../lib/roles";
import {
  guardarUltimaConversacion,
  leerUltimaConversacion,
  olvidarUltimaConversacion,
} from "../../lib/conversacionChat";
import { useRol, useWorkspace } from "../../lib/sesion";
import { DialogoNuevaUnidad } from "../unidad/DialogoNuevaUnidad";
import { ChatPerezoso, type PropsChat } from "./cargador";
import { SelectorConversaciones } from "./SelectorConversaciones";
import { useTokenChat } from "./token";

/** Las conversaciones de la persona en un workspace: la lista que pide el selector (la credencial no es parte de la clave). */
const claveConversaciones = (org: string, ws: string) => ["chat-conversaciones", org, ws] as const;

class LimiteError extends Component<{ children: ReactNode }, { error: unknown }> {
  override state = { error: null as unknown };
  static getDerivedStateFromError(error: unknown) {
    return { error };
  }
  override render() {
    return this.state.error ? <ErrorVista error={this.state.error} /> : this.props.children;
  }
}

/**
 * Chat con lo que el shell le da: los repositorios vinculados para elegir (el selector ya no es texto libre,
 * así no se manda un nombre que el servidor rechazaría con 422), la última conversación para retomarla, un
 * selector para retomar cualquiera de las suyas y el paso de un insumo exportado a una unidad nueva.
 */
function ChatConEstado({ Chat, token, org, ws }: { Chat: ComponentType<PropsChat>; token: string; org: string; ws: string }) {
  const { rol } = useRol();
  const vinculos = useQuery({ queryKey: claves.repositorios(org, ws), queryFn: () => repositorios.listar(org, ws) });
  // Se lee una vez al montar; crear o descartar conversaciones no cambia lo que el chat recibe.
  const [inicial, setInicial] = useState(() => leerUltimaConversacion(org, ws));
  // La que está abierta ahora (retomada, elegida o recién creada); el chat la remonta al elegir otra.
  const [actual, setActual] = useState(inicial);
  const [generacion, setGeneracion] = useState(0);
  const [aviso, setAviso] = useState<string | null>(null);
  const [insumo, setInsumo] = useState<Insumo | null>(null);

  const disponibles = useMemo(
    () => (vinculos.data ?? []).map((v) => ({ id: v.alcance.repositorio, nivel: v.nivel_codigo })),
    [vinculos.data],
  );
  // El token se renueva desde arriba; el cliente lee el vigente en cada petición.
  const tokenRef = useRef(token);
  tokenRef.current = token;
  const cliente = useMemo(() => crearClienteChat({ apiBase: "", obtenerToken: () => tokenRef.current }), []);
  const clienteQuery = useQueryClient();
  const conversaciones = useQuery({
    queryKey: claveConversaciones(org, ws),
    queryFn: () => cliente.listarConversaciones({ org, workspace: ws }),
    // Es un extra de la ruta: no reintentar solo, el aviso trae su botón.
    retry: false,
  });
  const recargarLista = useCallback(
    () => void clienteQuery.invalidateQueries({ queryKey: claveConversaciones(org, ws) }),
    [clienteQuery, org, ws],
  );

  const alCrear = useCallback(
    (id: string) => {
      guardarUltimaConversacion(org, ws, id);
      setActual(id);
      recargarLista();
    },
    [org, ws, recargarLista],
  );
  const alNoDisponible = useCallback(() => {
    olvidarUltimaConversacion(org, ws);
    setInicial(undefined);
    setActual(undefined);
    recargarLista();
    setAviso("La conversación anterior ya no está disponible (expiró o no es tuya). Elige los repositorios para empezar otra.");
  }, [org, ws, recargarLista]);
  const alNueva = useCallback(() => {
    olvidarUltimaConversacion(org, ws);
    setInicial(undefined);
    setActual(undefined);
    setAviso(null);
  }, [org, ws]);
  // Desde el selector la conversación abierta cambia por fuera del chat: se remonta para que cargue la elegida
  // (o vuelva a pedir repositorios), porque dentro del chat «nueva conversación» no se deshace.
  const alElegir = useCallback(
    (id: string) => {
      guardarUltimaConversacion(org, ws, id);
      setInicial(id);
      setActual(id);
      setAviso(null);
      setGeneracion((g) => g + 1);
    },
    [org, ws],
  );
  const alEmpezarOtra = useCallback(() => {
    alNueva();
    setGeneracion((g) => g + 1);
  }, [alNueva]);

  if (vinculos.isPending) return <Cargando texto="Cargando repositorios vinculados…" />;
  if (vinculos.isError) return <ErrorVista error={vinculos.error} reintentar={() => void vinculos.refetch()} />;

  return (
    <>
      {aviso ? <Aviso tono="aviso" className="mb-3">{aviso}</Aviso> : null}
      {conversaciones.isError ? (
        <Aviso tono="aviso" className="mb-3">
          No se pudo listar tus conversaciones ({conversaciones.error instanceof Error ? conversaciones.error.message : "error desconocido"}).{" "}
          <Button variante="fantasma" tamano="pequeno" onClick={() => void conversaciones.refetch()}>
            Reintentar
          </Button>
        </Aviso>
      ) : null}
      {conversaciones.data && conversaciones.data.length > 0 ? (
        <SelectorConversaciones conversaciones={conversaciones.data} actual={actual} alElegir={alElegir} alNueva={alEmpezarOtra} />
      ) : null}
      <Chat
        key={generacion}
        apiBase=""
        token={token}
        org={org}
        workspace={ws}
        repositoriosDisponibles={disponibles}
        {...(inicial ? { conversacionId: inicial } : {})}
        alCrearConversacion={alCrear}
        alConversacionNoDisponible={alNoDisponible}
        alNuevaConversacion={alNueva}
        {...(alcanza(rol, "desarrollador") ? { alCrearUnidad: setInsumo } : {})}
      />
      {insumo ? <DialogoNuevaUnidad org={org} ws={ws} desdeInsumo={insumo} alCerrar={() => setInsumo(null)} /> : null}
    </>
  );
}

function ChatConToken({ Chat, org, ws }: { Chat: ComponentType<PropsChat>; org: string; ws: string }) {
  const { token, error, cargando, reintentar } = useTokenChat();
  if (!token) {
    if (error) return <ErrorVista error={error} reintentar={reintentar} />;
    return cargando ? <Cargando texto="Obteniendo credencial del chat…" /> : null;
  }
  return <ChatConEstado Chat={Chat} token={token} org={org} ws={ws} />;
}

/** Contenedor del chat: límite de errores, carga diferida y credencial. */
export function ContenedorChat({ Chat, org, ws }: { Chat: ComponentType<PropsChat>; org: string; ws: string }) {
  return (
    <LimiteError>
      <Suspense fallback={<Cargando texto="Cargando chat…" />}>
        <ChatConToken Chat={Chat} org={org} ws={ws} />
      </Suspense>
    </LimiteError>
  );
}

export function RutaChat() {
  const { org, ws } = useWorkspace();
  return (
    <>
      <Encabezado titulo="Chat de contexto" />
      <ContenedorChat Chat={ChatPerezoso} org={org} ws={ws} />
    </>
  );
}
