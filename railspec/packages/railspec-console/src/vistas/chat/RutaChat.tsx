import { Component, Suspense, type ComponentType, type ReactNode } from "react";
import { Cargando, Encabezado, ErrorVista } from "../../componentes/Estados";
import { Card, CardDescription, CardHeader, CardTitle } from "../../componentes/ui/card";
import { useWorkspace } from "../../lib/sesion";
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

export function ChatNoDisponible() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>El chat de contexto aún no está disponible</CardTitle>
        <CardDescription>Esta instalación de la consola todavía no incluye el módulo del chat.</CardDescription>
      </CardHeader>
    </Card>
  );
}

function ChatConToken({ Chat, org, ws }: { Chat: ComponentType<PropsChat>; org: string; ws: string }) {
  const { token, error, cargando, reintentar } = useTokenChat();
  if (!token) {
    if (error) return <ErrorVista error={error} reintentar={reintentar} />;
    return cargando ? <Cargando texto="Obteniendo credencial del chat…" /> : null;
  }
  return <Chat apiBase="" token={token} org={org} workspace={ws} />;
}

/** Renderiza el chat si el módulo existe; si no, la tarjeta de "no disponible". */
export function ContenedorChat({ Chat, org, ws }: { Chat: ComponentType<PropsChat> | null; org: string; ws: string }) {
  if (!Chat) return <ChatNoDisponible />;
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
