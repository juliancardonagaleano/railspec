import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { claves, mandatos } from "../../api/endpoints";
import { Aviso } from "../../componentes/Estados";

/**
 * Aviso del tablero: cuántos mandatos están parados, para que no pasen inadvertidos (sus unidades están
 * retenidas hasta renovarlos). Es un extra: si la consulta falla o no hay ninguno, no pinta nada.
 */
export function MandatosParados({ org, ws }: { org: string; ws: string }) {
  const q = useQuery({
    queryKey: [...claves.mandatos(org, ws), "parados"],
    queryFn: () => mandatos.listar({ alcance: { org, workspace: ws }, estado: ["parado"], limite: 50 }),
  });
  const n = q.data?.mandatos.length ?? 0;
  if (n === 0) return null;
  return (
    <Aviso tono="aviso" className="mb-3">
      {n === 1 ? "1 mandato está parado" : `${n} mandatos están parados`}: sus unidades esperan a que lo renueves.{" "}
      <Link to="/$org/$ws/mandatos" params={{ org, ws }} className="font-medium underline">
        Ver mandatos
      </Link>
    </Aviso>
  );
}
