// Punto de entrada que carga el shell de la consola (import.meta.glob → src/chat/index.tsx).
import { ChatContexto } from "./ChatContexto";

export default ChatContexto;
export { ChatContexto } from "./ChatContexto";
export type { PropsChatContexto } from "./ChatContexto";
export { PaginaPruebaChat } from "./PaginaPruebaChat";
export { crearClienteChat, ErrorChat } from "./api";
export type { ClienteChat, OpcionesClienteChat } from "./api";
export { LectorSse, leerEventos } from "./sse";
export type { EventoSse } from "./sse";
export {
  comandoPull,
  detalleReferencia,
  etiquetaReferencia,
  EXPLICACION_REGLA,
  explicarRegla,
  shaCorto,
} from "./referencias";
export type * from "./tipos";
