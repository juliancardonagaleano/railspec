import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { claves, organizaciones } from "../../api/endpoints";
import type { Organizacion } from "../../api/tipos";
import { Cargando, Encabezado, ErrorVista, Vacio } from "../../componentes/Estados";
import { Button } from "../../componentes/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";
import { AvisoGuardado, useGuardado } from "../../lib/mutaciones";
import { alcanza, rolEnOrg } from "../../lib/roles";
import { useYo } from "../../lib/sesion";
import { fecha } from "../../lib/utiles";
import { FormularioOrganizacion } from "./FormularioOrganizacion";

const CLAVE_AVISO = "organizaciones";

export function Organizaciones() {
  const { data: yo } = useYo();
  const lista = useQuery({ queryKey: claves.organizaciones, queryFn: organizaciones.listar });
  const [creando, setCreando] = useState(false);
  const [editando, setEditando] = useState<Organizacion | null>(null);
  const guardado = useGuardado(CLAVE_AVISO);
  return (
    <>
      <Encabezado
        titulo="Organizaciones"
        descripcion="Organizaciones visibles para ti."
        acciones={yo?.plataforma_admin ? <Button onClick={() => setCreando(true)}>Nueva organización</Button> : null}
      />
      <AvisoGuardado guardado={guardado} className="mb-3" />
      {lista.isPending ? <Cargando /> : null}
      {lista.isError ? <ErrorVista error={lista.error} reintentar={() => void lista.refetch()} /> : null}
      {lista.isSuccess && lista.data.length === 0 ? <Vacio titulo="No hay organizaciones" /> : null}
      {lista.isSuccess && lista.data.length > 0 ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Organización</TableHead>
              <TableHead>GitHub</TableHead>
              <TableHead>Región</TableHead>
              <TableHead>Actualizada</TableHead>
              <TableHead>
                <span className="sr-only">Acciones</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {lista.data.map((o) => (
              <TableRow key={o.id}>
                <TableCell>
                  <Link to="/$org" params={{ org: o.id }} className="font-medium text-primario">
                    {o.nombre}
                  </Link>
                  <p className="font-mono text-xs text-suave">{o.id}</p>
                </TableCell>
                <TableCell>{o.github_org ?? "—"}</TableCell>
                <TableCell>{o.region_datos}</TableCell>
                <TableCell>{fecha(o.auditoria.actualizado_en)}</TableCell>
                <TableCell>
                  {alcanza(rolEnOrg(yo, o.id), "org-admin") || yo?.plataforma_admin ? (
                    <Button variante="secundario" tamano="pequeno" onClick={() => setEditando(o)}>
                      Editar
                    </Button>
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
      {creando ? <FormularioOrganizacion abierto alCerrar={() => setCreando(false)} claveAviso={CLAVE_AVISO} /> : null}
      {editando ? (
        <FormularioOrganizacion key={editando.version} org={editando} abierto alCerrar={() => setEditando(null)} claveAviso={CLAVE_AVISO} />
      ) : null}
    </>
  );
}
