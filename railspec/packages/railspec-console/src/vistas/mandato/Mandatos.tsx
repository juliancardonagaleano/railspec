import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { claves, mandatos } from "../../api/endpoints";
import { Cargando, Encabezado, ErrorVista, Vacio } from "../../componentes/Estados";
import { EtiquetaModo } from "../../componentes/Etiquetas";
import { Badge } from "../../componentes/ui/badge";
import { Button } from "../../componentes/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";
import { alcanza } from "../../lib/roles";
import { useRol, useWorkspace } from "../../lib/sesion";
import { fecha } from "../../lib/utiles";
import { EtiquetaCausaParada, EtiquetaEstadoMandato } from "./EtiquetasMandato";
import { FormularioMandato } from "./FormularioMandato";

/** Lista de los mandatos del workspace (`mandate.list`) y alta de uno nuevo. */
export function Mandatos() {
  const { org, ws } = useWorkspace();
  const { rol } = useRol();
  const [nuevo, setNuevo] = useState(false);
  const lista = useQuery({
    queryKey: claves.mandatos(org, ws),
    queryFn: () => mandatos.listar({ alcance: { org, workspace: ws }, limite: 200 }),
  });
  const puedeEscribir = alcanza(rol, "desarrollador");

  return (
    <>
      <Encabezado
        titulo="Mandatos"
        descripcion="Aprobaciones únicas, acotadas y con caducidad bajo las que corren las unidades supervisadas y desatendidas."
        acciones={puedeEscribir ? <Button onClick={() => setNuevo(true)}>Nuevo mandato</Button> : null}
      />
      {lista.isPending ? <Cargando texto="Cargando mandatos…" /> : null}
      {lista.isError ? <ErrorVista error={lista.error} reintentar={() => void lista.refetch()} /> : null}
      {lista.isSuccess && lista.data.mandatos.length === 0 ? (
        <Vacio titulo="Este workspace aún no tiene mandatos">
          Sin un mandato aprobado no se pueden arrancar unidades supervisadas ni desatendidas.
        </Vacio>
      ) : null}
      {lista.isSuccess && lista.data.mandatos.length > 0 ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Mandato</TableHead>
              <TableHead>Modo</TableHead>
              <TableHead>Estado</TableHead>
              <TableHead>Vigente hasta</TableHead>
              <TableHead>Parada</TableHead>
              <TableHead>Unidades</TableHead>
              <TableHead>Decisiones pendientes</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {lista.data.mandatos.map((m) => (
              <TableRow key={m.id}>
                <TableCell>
                  <Link to="/$org/$ws/mandatos/$mandato" params={{ org, ws, mandato: m.id }} className="font-medium text-primario underline">
                    {m.titulo}
                  </Link>
                  <p className="font-mono text-xs text-suave">{m.id}</p>
                </TableCell>
                <TableCell>
                  <EtiquetaModo modo={m.modo} />
                </TableCell>
                <TableCell>
                  <EtiquetaEstadoMandato estado={m.estado} vigente={m.vigente} />
                </TableCell>
                <TableCell>{fecha(m.vigente_hasta)}</TableCell>
                <TableCell>{m.causa_parada ? <EtiquetaCausaParada causa={m.causa_parada} /> : "—"}</TableCell>
                <TableCell>
                  <span aria-label={`${m.unidades} de ${m.max_unidades} unidades`}>
                    {m.unidades}/{m.max_unidades}
                  </span>
                </TableCell>
                <TableCell>{m.decisiones_pendientes > 0 ? <Badge tono="aviso">{m.decisiones_pendientes}</Badge> : "0"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
      {nuevo ? <FormularioMandato org={org} ws={ws} alCerrar={() => setNuevo(false)} /> : null}
    </>
  );
}
