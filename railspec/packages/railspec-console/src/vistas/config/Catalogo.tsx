import { useQuery } from "@tanstack/react-query";
import { ErrorApi } from "../../api/cliente";
import { catalogo, claves } from "../../api/endpoints";
import { Aviso, Cargando, ErrorVista, Vacio } from "../../componentes/Estados";
import { Badge } from "../../componentes/ui/badge";
import { Button } from "../../componentes/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../componentes/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";
import { useGuardar } from "../../lib/mutaciones";
import { fecha, numero } from "../../lib/utiles";

export function Catalogo({ org, puedeSincronizar }: { org: string; puedeSincronizar: boolean }) {
  const lista = useQuery({ queryKey: claves.catalogo(org), queryFn: () => catalogo.listar(org) });
  const sincronizar = useGuardar(() => catalogo.sincronizar(org), [claves.catalogo(org)]);
  const noImplementado = sincronizar.error instanceof ErrorApi && sincronizar.error.status === 501;
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between">
        <div>
          <CardTitle>Catálogo de modelos</CardTitle>
          <CardDescription>Modelos disponibles en los proveedores de la organización.</CardDescription>
        </div>
        {puedeSincronizar ? (
          <Button tamano="pequeno" variante="secundario" onClick={() => sincronizar.mutate(undefined)} disabled={sincronizar.isPending}>
            {sincronizar.isPending ? "Sincronizando…" : "Sincronizar"}
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {sincronizar.isSuccess ? <Aviso tono="exito">Catálogo sincronizado: {sincronizar.data.modelos} modelos.</Aviso> : null}
        {noImplementado ? (
          <Aviso tono="info">
            El servidor todavía no sabe leer el catálogo de los proveedores automáticamente. Mientras tanto, el catálogo se mantiene
            desde la configuración del servidor.
          </Aviso>
        ) : sincronizar.isError ? (
          <ErrorVista error={sincronizar.error} />
        ) : null}
        {lista.isPending ? <Cargando /> : null}
        {lista.isError ? <ErrorVista error={lista.error} reintentar={() => void lista.refetch()} /> : null}
        {lista.isSuccess && lista.data.length === 0 ? <Vacio titulo="El catálogo está vacío" /> : null}
        {lista.isSuccess && lista.data.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Proveedor</TableHead>
                <TableHead>Modelo</TableHead>
                <TableHead>Despliegue</TableHead>
                <TableHead>Hosting / región</TableHead>
                <TableHead>Efforts</TableHead>
                <TableHead>Capacidades</TableHead>
                <TableHead className="text-right">Contexto</TableHead>
                <TableHead>Leído</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {lista.data.map((m) => (
                <TableRow key={`${m.proveedor}/${m.modelo}/${m.despliegue ?? ""}`}>
                  <TableCell>{m.proveedor}</TableCell>
                  <TableCell className="font-mono text-xs">{m.modelo}</TableCell>
                  <TableCell>{m.despliegue ?? "—"}</TableCell>
                  <TableCell>
                    {m.hosting}
                    {m.region ? ` / ${m.region}` : ""}
                  </TableCell>
                  <TableCell className="text-xs">{m.capacidades.efforts?.join(", ") || "—"}</TableCell>
                  <TableCell className="flex flex-wrap gap-1">
                    {m.capacidades.thinking ? <Badge tono="info">thinking</Badge> : null}
                    {m.capacidades.structured_outputs ? <Badge tono="info">structured</Badge> : null}
                  </TableCell>
                  <TableCell className="text-right">{numero(m.capacidades.contexto_max_tokens)}</TableCell>
                  <TableCell className="text-xs">{fecha(m.leido_en)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </CardContent>
    </Card>
  );
}
