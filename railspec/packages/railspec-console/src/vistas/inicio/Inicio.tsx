import { Link, Navigate } from "@tanstack/react-router";
import { Encabezado, Vacio } from "../../componentes/Estados";
import { useOrg, useYo } from "../../lib/sesion";

/** `/`: lleva al primer workspace visible. */
export function Inicio() {
  const { data: yo } = useYo();
  if (!yo) return null;
  const primera = yo.organizaciones.find((o) => o.workspaces.length > 0);
  const ws = primera?.workspaces[0];
  if (primera && ws) return <Navigate to="/$org/$ws" params={{ org: primera.id, ws: ws.workspace }} replace />;
  const org = yo.organizaciones[0];
  if (org) return <Navigate to="/$org" params={{ org: org.id }} replace />;
  return (
    <>
      <Encabezado titulo={`Hola, ${yo.login}`} />
      <Vacio titulo="Aún no perteneces a ninguna organización">
        {yo.plataforma_admin ? (
          <Link to="/organizaciones" className="text-primario underline">
            Crear una organización
          </Link>
        ) : (
          "Pide a un administrador que te asigne un rol."
        )}
      </Vacio>
    </>
  );
}

/** `/$org`: lista los workspaces de la organización. */
export function InicioOrg() {
  const org = useOrg();
  const { data: yo } = useYo();
  const o = yo?.organizaciones.find((x) => x.id === org);
  if (!yo) return null;
  if (!o) return <Vacio titulo="No tienes acceso a esta organización" />;
  return (
    <>
      <Encabezado titulo={o.nombre} descripcion="Elige un workspace." />
      {o.workspaces.length === 0 ? (
        <Vacio titulo="Esta organización aún no tiene workspaces visibles para ti">
          <Link to="/$org/administracion" params={{ org }} className="text-primario underline">
            Ir a administración
          </Link>
        </Vacio>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {o.workspaces.map((w) => (
            <li key={w.workspace}>
              <Link
                to="/$org/$ws"
                params={{ org, ws: w.workspace }}
                className="block rounded-lg border border-borde bg-superficie p-4 hover:border-primario"
              >
                <p className="font-medium">{w.nombre}</p>
                <p className="text-xs text-suave">
                  {w.workspace} · {w.rol}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
