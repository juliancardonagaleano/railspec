import { lazy, type ComponentType, type LazyExoticComponent } from "react";

/** Props que la consola pasa al chat de contexto (contrato de la fase 8). */
export interface PropsChat {
  apiBase: string;
  token: string;
  org: string;
  workspace: string;
}

interface ModuloChat {
  ChatContexto?: ComponentType<PropsChat>;
  default?: ComponentType<PropsChat>;
}

export type CargadoresModulo = Record<string, () => Promise<unknown>>;

/**
 * Dado el resultado de `import.meta.glob`, devuelve el cargador del componente del
 * chat (export `ChatContexto` o, si no, el default) o `null` si el módulo no existe.
 */
export function resolverCargadorChat(modulos: CargadoresModulo): (() => Promise<{ default: ComponentType<PropsChat> }>) | null {
  const cargar = Object.values(modulos)[0];
  if (!cargar) return null;
  return async () => {
    const m = (await cargar()) as ModuloChat;
    const componente = m.ChatContexto ?? m.default;
    if (!componente) throw new Error("El módulo del chat no exporta ChatContexto ni un default.");
    return { default: componente };
  };
}

export function crearChatPerezoso(modulos: CargadoresModulo): LazyExoticComponent<ComponentType<PropsChat>> | null {
  const cargador = resolverCargadorChat(modulos);
  return cargador ? lazy(cargador) : null;
}

// El chat vive en src/chat/ (otro hilo). Si no existe, el glob queda vacío y no rompe el build.
export const ChatPerezoso = crearChatPerezoso(import.meta.glob("../../chat/index.tsx"));
