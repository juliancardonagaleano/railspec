import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, Outlet, useNavigate, useParams } from "@tanstack/react-router";
import { auth } from "../api/endpoints";
import type { Yo } from "../api/tipos";
import { olvidarTodasLasConversaciones } from "../lib/conversacionChat";
import { alcanza, ETIQUETA_ROL, rolEnOrg, rolEnWorkspace } from "../lib/roles";
import { useYo } from "../lib/sesion";
import { cn } from "../lib/utiles";
import { Cargando, ErrorVista } from "./Estados";
import { Button } from "./ui/button";

const CLASE_ENLACE =
  "block rounded-md px-3 py-1.5 text-sm text-suave hover:bg-fondo hover:text-texto [&.active]:bg-fondo [&.active]:font-semibold [&.active]:text-texto";

/** Valor del selector: "org/ws" o "org/" para entrar a la organización. */
function SelectorWorkspace({ yo, org, ws }: { yo: Yo; org?: string; ws?: string }) {
  const navegar = useNavigate();
  const valor = org ? `${org}/${ws ?? ""}` : "";
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor="selector-ws" className="text-xs font-medium text-suave">
        Organización / workspace
      </label>
      <select
        id="selector-ws"
        className="h-9 w-full rounded-md border border-borde bg-superficie px-2 text-sm"
        value={valor}
        onChange={(e) => {
          const [o, w] = e.target.value.split("/");
          if (!o) return;
          if (w) void navegar({ to: "/$org/$ws", params: { org: o, ws: w } });
          else void navegar({ to: "/$org", params: { org: o } });
        }}
      >
        {!org ? <option value="">Elige un workspace…</option> : null}
        {yo.organizaciones.map((o) => (
          <optgroup key={o.id} label={o.nombre}>
            <option value={`${o.id}/`}>{o.nombre} (organización)</option>
            {o.workspaces.map((w) => (
              <option key={w.workspace} value={`${o.id}/${w.workspace}`}>
                {w.nombre}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </div>
  );
}

function Navegacion({ yo, org, ws }: { yo: Yo; org?: string; ws?: string }) {
  const rolWs = org && ws ? rolEnWorkspace(yo, org, ws) : null;
  const rolOrg = org ? rolEnOrg(yo, org) : null;
  return (
    <nav aria-label="Principal" className="flex flex-col gap-4">
      {org && ws ? (
        <div>
          <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-suave">Workspace</p>
          <Link to="/$org/$ws" params={{ org, ws }} className={CLASE_ENLACE} activeOptions={{ exact: true, includeSearch: false }}>
            Tablero de unidades
          </Link>
          <Link to="/$org/$ws/mandatos" params={{ org, ws }} className={CLASE_ENLACE}>
            Mandatos
          </Link>
          <Link to="/$org/$ws/grafo" params={{ org, ws }} className={CLASE_ENLACE} activeOptions={{ exact: true }}>
            Grafo de código
          </Link>
          <Link to="/$org/$ws/grafo/retenidas" params={{ org, ws }} className={CLASE_ENLACE}>
            Retenidas del grafo
          </Link>
          <Link to="/$org/$ws/auditoria" params={{ org, ws }} className={CLASE_ENLACE}>
            Auditoría
          </Link>
          <Link to="/$org/$ws/estadisticas" params={{ org, ws }} className={CLASE_ENLACE}>
            Estadísticas
          </Link>
          <Link to="/$org/$ws/chat" params={{ org, ws }} className={CLASE_ENLACE}>
            Chat
          </Link>
          {alcanza(rolWs, "workspace-admin") ? (
            <>
              <Link to="/$org/$ws/administracion" params={{ org, ws }} className={CLASE_ENLACE}>
                Administración
              </Link>
              <Link to="/$org/$ws/configuracion" params={{ org, ws }} className={CLASE_ENLACE}>
                Configuración
              </Link>
            </>
          ) : null}
        </div>
      ) : null}
      {org ? (
        <div>
          <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-suave">Organización</p>
          <Link to="/$org/administracion" params={{ org }} className={CLASE_ENLACE}>
            Administración
          </Link>
          <Link to="/$org/configuracion" params={{ org }} className={CLASE_ENLACE}>
            Configuración{alcanza(rolOrg, "org-admin") ? "" : " (lectura)"}
          </Link>
        </div>
      ) : null}
      {yo.plataforma_admin ? (
        <div>
          <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-suave">Plataforma</p>
          <Link to="/organizaciones" className={CLASE_ENLACE}>
            Organizaciones
          </Link>
          <Link to="/operacion" className={CLASE_ENLACE}>
            Operación
          </Link>
        </div>
      ) : null}
    </nav>
  );
}

function Usuario({ yo, org, ws }: { yo: Yo; org?: string; ws?: string }) {
  const clienteQuery = useQueryClient();
  const [saliendo, setSaliendo] = useState(false);
  const rol = org ? (ws ? rolEnWorkspace(yo, org, ws) : rolEnOrg(yo, org)) : null;
  const salir = async () => {
    setSaliendo(true);
    try {
      await auth.salir();
    } finally {
      olvidarTodasLasConversaciones();
      clienteQuery.clear();
      window.location.assign("/consola/login");
    }
  };
  return (
    <div className="border-t border-borde pt-3">
      <p className="truncate text-sm font-medium">{yo.login}</p>
      <p className="text-xs text-suave">
        {rol ? ETIQUETA_ROL[rol] : "Sin rol aquí"}
        {yo.plataforma_admin ? " · admin. de plataforma" : ""}
      </p>
      <Button variante="secundario" tamano="pequeno" className="mt-2 w-full" onClick={() => void salir()} disabled={saliendo}>
        {saliendo ? "Saliendo…" : "Salir"}
      </Button>
    </div>
  );
}

/** Marco autenticado: barra lateral + contenido. */
export function Marco() {
  const yo = useYo();
  const params = useParams({ strict: false }) as { org?: string; ws?: string };
  const [abierta, setAbierta] = useState(false);

  if (yo.isPending) return <Cargando texto="Cargando sesión…" className="p-8" />;
  if (yo.isError)
    return (
      <div className="p-8">
        <ErrorVista error={yo.error} reintentar={() => void yo.refetch()} />
      </div>
    );

  return (
    <div className="flex min-h-full flex-col md:flex-row">
      <header className="flex items-center justify-between border-b border-borde bg-superficie p-3 md:hidden">
        <span className="font-semibold">Railspec</span>
        <Button
          variante="secundario"
          tamano="pequeno"
          aria-expanded={abierta}
          aria-controls="barra-lateral"
          onClick={() => setAbierta((a) => !a)}
        >
          Menú
        </Button>
      </header>
      <aside
        id="barra-lateral"
        className={cn(
          "flex-col gap-4 border-r border-borde bg-superficie p-4 md:sticky md:top-0 md:flex md:h-screen md:w-64 md:shrink-0 md:overflow-y-auto",
          abierta ? "flex" : "hidden",
        )}
      >
        <Link to="/" className="hidden text-lg font-semibold md:block">
          Railspec <span className="font-normal text-suave">consola</span>
        </Link>
        <SelectorWorkspace yo={yo.data} {...params} />
        <div className="flex-1">
          <Navegacion yo={yo.data} {...params} />
        </div>
        <Usuario yo={yo.data} {...params} />
      </aside>
      <main className="min-w-0 flex-1 p-4 md:p-6">
        <Outlet />
      </main>
    </div>
  );
}
