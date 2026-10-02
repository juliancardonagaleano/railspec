import { lazy } from "react";

export type { PropsChatContexto as PropsChat } from "../../chat/ChatContexto";

/** El chat se carga aparte (chunk propio): la consola lo incluye siempre, pero no pesa en el resto de vistas. */
export const ChatPerezoso = lazy(async () => ({ default: (await import("../../chat/ChatContexto")).ChatContexto }));
