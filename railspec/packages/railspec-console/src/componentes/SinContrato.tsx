import { esSinContrato14 } from "../api/cliente";
import { Aviso, ErrorVista } from "./Estados";

/** Error de un verbo 1.4 de graph.query: si el servidor aún no lo tiene, un aviso amable. */
export function ErrorContrato14({ error, reintentar }: { error: unknown; reintentar?: () => void }) {
  if (esSinContrato14(error)) return <Aviso tono="info">Disponible cuando el servidor tenga el contrato 1.4.</Aviso>;
  return <ErrorVista error={error} {...(reintentar ? { reintentar } : {})} />;
}
