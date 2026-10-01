import { Link } from "@tanstack/react-router";

/** Enlace al navegador del grafo con el símbolo seleccionado. */
export function EnlaceSimbolo({
  org,
  ws,
  simbolo,
  nombre,
  repositorio,
  ruta,
}: {
  org: string;
  ws: string;
  simbolo: string;
  nombre: string;
  repositorio: string;
  ruta?: string;
}) {
  return (
    <Link
      to="/$org/$ws/grafo"
      params={{ org, ws }}
      search={{ simbolo, nombre, repositorio }}
      className="text-primario underline-offset-2 hover:underline"
      title={ruta ? `${repositorio}/${ruta}` : repositorio}
    >
      {nombre}
    </Link>
  );
}
