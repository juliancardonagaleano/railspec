import { Link } from "@tanstack/react-router";

/** Enlace al navegador del grafo con el símbolo seleccionado. */
export function EnlaceSimbolo({
  org,
  ws,
  simbolo,
  nombre,
  repositorio,
  ruta,
  unidad,
}: {
  org: string;
  ws: string;
  simbolo: string;
  nombre: string;
  repositorio: string;
  ruta?: string;
  /** Abre el grafo comparando la base contra el snapshot de esta unidad. */
  unidad?: string;
}) {
  return (
    <Link
      to="/$org/$ws/grafo"
      params={{ org, ws }}
      search={{ simbolo, nombre, repositorio, ...(unidad ? { unidad } : {}) }}
      className="text-primario underline-offset-2 hover:underline"
      title={ruta ? `${repositorio}/${ruta}` : repositorio}
    >
      {nombre}
    </Link>
  );
}
