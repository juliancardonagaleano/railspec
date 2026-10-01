import { useQuery } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { claves, obtenerYo } from "../api/endpoints";
import { rolEnOrg, rolEnWorkspace } from "./roles";
import type { Rol } from "../api/tipos";

export function useYo() {
  return useQuery({ queryKey: claves.yo, queryFn: obtenerYo, staleTime: 60_000 });
}

/** Parámetros `$org` y `$ws` de la ruta actual (vistas de workspace). */
export function useWorkspace(): { org: string; ws: string } {
  const p = useParams({ strict: false }) as { org?: string; ws?: string };
  if (!p.org || !p.ws) throw new Error("Vista de workspace fuera de /$org/$ws");
  return { org: p.org, ws: p.ws };
}

export function useOrg(): string {
  const p = useParams({ strict: false }) as { org?: string };
  if (!p.org) throw new Error("Vista de organización fuera de /$org");
  return p.org;
}

/** Rol efectivo del usuario donde está: en el workspace si hay `$ws`, si no en la org. */
export function useRol(): { rol: Rol | null; plataformaAdmin: boolean } {
  const { data } = useYo();
  const p = useParams({ strict: false }) as { org?: string; ws?: string };
  const rol = p.org ? (p.ws ? rolEnWorkspace(data, p.org, p.ws) : rolEnOrg(data, p.org)) : null;
  return { rol, plataformaAdmin: data?.plataforma_admin ?? false };
}
