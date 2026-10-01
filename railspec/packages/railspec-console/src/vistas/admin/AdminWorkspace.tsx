import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { claves, workspaces } from "../../api/endpoints";
import { Cargando, Encabezado, ErrorVista } from "../../componentes/Estados";
import { Button } from "../../componentes/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../componentes/ui/card";
import { alcanza } from "../../lib/roles";
import { useRol, useWorkspace } from "../../lib/sesion";
import { FormularioWorkspace } from "./FormularioWorkspace";
import { Repositorios } from "./Repositorios";
import { Roles } from "./Roles";

function DatosWorkspace({ org, ws, puedeEditar }: { org: string; ws: string; puedeEditar: boolean }) {
  const lista = useQuery({ queryKey: claves.workspaces(org), queryFn: () => workspaces.listar(org) });
  const [editando, setEditando] = useState(false);
  const w = lista.data?.find((x) => x.alcance.workspace === ws);
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between">
        <CardTitle>Workspace</CardTitle>
        {puedeEditar && w ? (
          <Button tamano="pequeno" variante="secundario" onClick={() => setEditando(true)}>
            Editar
          </Button>
        ) : null}
      </CardHeader>
      <CardContent>
        {lista.isPending ? <Cargando /> : null}
        {lista.isError ? <ErrorVista error={lista.error} /> : null}
        {w ? (
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-suave">Nombre</dt>
            <dd>{w.nombre}</dd>
            <dt className="text-suave">Perfil por defecto</dt>
            <dd>{w.perfil_por_defecto}</dd>
            <dt className="text-suave">Zona de datos Azure</dt>
            <dd>{w.zona_datos_azure ?? "—"}</dd>
            <dt className="text-suave">Versión</dt>
            <dd>{w.version}</dd>
          </dl>
        ) : null}
      </CardContent>
      {editando && w ? <FormularioWorkspace key={w.version} org={org} ws={w} abierto alCerrar={() => setEditando(false)} /> : null}
    </Card>
  );
}

export function AdminWorkspace() {
  const { org, ws } = useWorkspace();
  const { rol } = useRol();
  const admin = alcanza(rol, "workspace-admin");
  return (
    <>
      <Encabezado titulo="Administración del workspace" descripcion={`${org} / ${ws}`} />
      <div className="flex flex-col gap-4">
        <DatosWorkspace org={org} ws={ws} puedeEditar={admin} />
        <Repositorios org={org} ws={ws} puedeEditar={admin} />
        <Roles org={org} ws={ws} puedeEditar={admin} />
      </div>
    </>
  );
}
