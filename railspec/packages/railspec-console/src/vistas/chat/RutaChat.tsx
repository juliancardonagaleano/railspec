import { Component, Suspense, useCallback, useMemo, useState, type ComponentType, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { claves, repositorios } from "../../api/endpoints";
import type { Insumo } from "../../chat/tipos";
import { Aviso, Cargando, Encabezado, ErrorVista } from "../../componentes/Estados";
import { alcanza } from "../../lib/roles";
import {
  guardarUltimaConversacion,
  leerUltimaConversacion,
  olvidarUltimaConversacion,
} from "../../lib/conversacionChat";
import { useRol, useWorkspace } from "../../lib/sesion";
import { DialogoNuevaUnidad } from "../unidad/DialogoNuevaUnidad";
import { ChatPerezoso, type PropsChat } from "./cargador";
import { useTokenChat } from "./token";

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
 * así no se manda un nombre que el servidor rechazaría con 422), la última conversación para retomarla y
 * el paso de un insumo exportado a una unidad nueva.
 */
function ChatConEstado({ Chat, token, org, ws }: { Chat: ComponentType<PropsChat>; token: string; org: string; ws: string }) {
  const { rol } = useRol();
  const vinculos = useQuery({ queryKey: claves.repositorios(org, ws), queryFn: () => repositorios.listar(org, ws) });
  // Se lee una vez al montar; crear o descartar conversaciones no cambia lo que el chat recibe.
  const [inicial, setInicial] = useState(() => leerUltimaConversacion(org, ws));
  const [aviso, setAviso] = useState<string | null>(null);
  const [insumo, setInsumo] = useState<Insumo | null>(null);

  const disponibles = useMemo(
    () => (vinculos.data ?? []).map((v) => ({ id: v.alcance.repositorio, nivel: v.nivel_codigo })),
    [vinculos.data],
  );
  const alCrear = useCallback((id: string) => guardarUltimaConversacion(org, ws, id), [org, ws]);
  const alNoDisponible = useCallback(() => {
    olvidarUltimaConversacion(org, ws);
    setInicial(undefined);
    setAviso("La conversación anterior ya no está disponible (expiró o no es tuya). Elige los repositorios para empezar otra.");
  }, [org, ws]);
  const alNueva = useCallback(() => {
    olvidarUltimaConversacion(org, ws);
    setInicial(undefined);
    setAviso(null);
  }, [org, ws]);

  if (vinculos.isPending) return <Cargando texto="Cargando repositorios vinculados…" />;
  if (vinculos.isError) return <ErrorVista error={vinculos.error} reintentar={() => void vinculos.refetch()} />;

  return (
    <>
      {aviso ? <Aviso tono="aviso" className="mb-3">{aviso}</Aviso> : null}
      <Chat
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
