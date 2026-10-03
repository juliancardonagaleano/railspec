import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { claves, organizaciones, workspaces } from "../../api/endpoints";
import { Cargando, Encabezado, ErrorVista, Vacio } from "../../componentes/Estados";
import { Button } from "../../componentes/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../componentes/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";
import { AvisoGuardado, useGuardado } from "../../lib/mutaciones";
import { alcanza } from "../../lib/roles";
import { useOrg, useRol } from "../../lib/sesion";
import { fecha } from "../../lib/utiles";
import { FormularioOrganizacion } from "./FormularioOrganizacion";
import { FormularioWorkspace } from "./FormularioWorkspace";
import { Roles } from "./Roles";

function DatosOrganizacion({ org, puedeEditar }: { org: string; puedeEditar: boolean }) {
  const lista = useQuery({ queryKey: claves.organizaciones, queryFn: organizaciones.listar });
  const [editando, setEditando] = useState(false);
  const claveAviso = `organizacion:${org}`;
  const guardado = useGuardado(claveAviso);
  const o = lista.data?.find((x) => x.id === org);
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between">
        <div>
          <CardTitle>Organización</CardTitle>
          <CardDescription>Datos generales y región de datos.</CardDescription>
        </div>
        {puedeEditar && o ? (
          <Button tamano="pequeno" variante="secundario" onClick={() => setEditando(true)}>
            Editar
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <AvisoGuardado guardado={guardado} />
        {lista.isPending ? <Cargando /> : null}
        {lista.isError ? <ErrorVista error={lista.error} reintentar={() => void lista.refetch()} /> : null}
        {lista.isSuccess && !o ? <Vacio titulo="No puedes ver esta organización" /> : null}
        {o ? (
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-suave">Nombre</dt>
            <dd>{o.nombre}</dd>
            <dt className="text-suave">Identificador</dt>
            <dd className="font-mono">{o.id}</dd>
            <dt className="text-suave">GitHub</dt>
            <dd>{o.github_org ?? "—"}</dd>
            <dt className="text-suave">Región de datos</dt>
            <dd>{o.region_datos}</dd>
            <dt className="text-suave">Versión</dt>
            <dd>
              {o.version} · actualizada {fecha(o.auditoria.actualizado_en)}
            </dd>
          </dl>
        ) : null}
      </CardContent>
      {editando && o ? <FormularioOrganizacion key={o.version} org={o} abierto alCerrar={() => setEditando(false)} claveAviso={claveAviso} /> : null}
    </Card>
  );
}

function Workspaces({ org, puedeCrear }: { org: string; puedeCrear: boolean }) {
  const lista = useQuery({ queryKey: claves.workspaces(org), queryFn: () => workspaces.listar(org) });
  const [creando, setCreando] = useState(false);
  // Se guarda el id y no el registro: tras un 409 la lista se recarga y el formulario debe abrirse con la versión vigente.
  const [editandoId, setEditandoId] = useState<string | null>(null);
  const editando = lista.data?.find((w) => w.alcance.workspace === editandoId) ?? null;
  const claveAviso = `workspaces:${org}`;
  const guardado = useGuardado(claveAviso);
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between">
        <div>
          <CardTitle>Workspaces</CardTitle>
          <CardDescription>Workspaces visibles para ti en esta organización.</CardDescription>
        </div>
        {puedeCrear ? (
          <Button tamano="pequeno" onClick={() => setCreando(true)}>
            Nuevo workspace
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <AvisoGuardado guardado={guardado} />
        {lista.isPending ? <Cargando /> : null}
        {lista.isError ? <ErrorVista error={lista.error} reintentar={() => void lista.refetch()} /> : null}
        {lista.isSuccess && lista.data.length === 0 ? <Vacio titulo="Sin workspaces" /> : null}
        {lista.isSuccess && lista.data.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Workspace</TableHead>
                <TableHead>Perfil por defecto</TableHead>
                <TableHead>Zona de datos</TableHead>
                <TableHead>
                  <span className="sr-only">Acciones</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {lista.data.map((w) => (
                <TableRow key={w.alcance.workspace}>
                  <TableCell>
                    <Link to="/$org/$ws" params={{ org, ws: w.alcance.workspace }} className="font-medium text-primario">
                      {w.nombre}
                    </Link>
                    <p className="font-mono text-xs text-suave">{w.alcance.workspace}</p>
                  </TableCell>
                  <TableCell>{w.perfil_por_defecto}</TableCell>
                  <TableCell>{w.zona_datos_azure ?? "—"}</TableCell>
                  <TableCell>
                    {puedeCrear ? (
                      <Button variante="secundario" tamano="pequeno" onClick={() => setEditandoId(w.alcance.workspace)}>
                        Editar
                      </Button>
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </CardContent>
      {creando ? <FormularioWorkspace org={org} abierto alCerrar={() => setCreando(false)} claveAviso={claveAviso} /> : null}
      {editando ? (
        <FormularioWorkspace key={editando.version} org={org} ws={editando} abierto alCerrar={() => setEditandoId(null)} claveAviso={claveAviso} />
      ) : null}
    </Card>
  );
}

export function AdminOrganizacion() {
  const org = useOrg();
  const { rol } = useRol();
  const admin = alcanza(rol, "org-admin");
  return (
    <>
      <Encabezado
        titulo="Administración de la organización"
        descripcion={admin ? undefined : "Solo un admin. de organización puede cambiar estos datos; el servidor decide en cada acción."}
      />
      <div className="flex flex-col gap-4">
        <DatosOrganizacion org={org} puedeEditar={admin} />
        <Workspaces org={org} puedeCrear={admin} />
        <Roles org={org} puedeEditar={admin} />
      </div>
    </>
  );
}
