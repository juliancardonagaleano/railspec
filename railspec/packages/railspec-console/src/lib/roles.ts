import type { Rol, Yo } from "../api/tipos";

const NIVEL: Record<Rol, number> = {
  lector: 0,
  desarrollador: 1,
  "workspace-admin": 2,
  "org-admin": 3,
};

/** ¿El rol efectivo alcanza el mínimo? `null` = sin rol. Solo decide qué se muestra: autoriza el servidor. */
export function alcanza(rol: Rol | null | undefined, minimo: Rol): boolean {
  if (!rol) return false;
  return NIVEL[rol] >= NIVEL[minimo];
}

export function rolEnOrg(yo: Yo | undefined, org: string): Rol | null {
  return yo?.organizaciones.find((o) => o.id === org)?.rol ?? null;
}

/** Rol efectivo en el workspace (el servidor ya da el mayor entre org y workspace). */
export function rolEnWorkspace(yo: Yo | undefined, org: string, ws: string): Rol | null {
  const o = yo?.organizaciones.find((x) => x.id === org);
  const w = o?.workspaces.find((x) => x.workspace === ws);
  if (w) return w.rol;
  return o?.rol === "org-admin" ? "org-admin" : null;
}

export const ETIQUETA_ROL: Record<Rol, string> = {
  lector: "Lector",
  desarrollador: "Desarrollador",
  "workspace-admin": "Admin. de workspace",
  "org-admin": "Admin. de organización",
};
