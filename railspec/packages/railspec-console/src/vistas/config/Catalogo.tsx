import { useQuery } from "@tanstack/react-query";
import { catalogo, claves } from "../../api/endpoints";
import type { EstadoProveedorCatalogo, Proveedor, ResultadoSincronizacion } from "../../api/tipos";
import { Aviso, Cargando, ErrorVista, Vacio } from "../../componentes/Estados";
import { Badge } from "../../componentes/ui/badge";
import { Button } from "../../componentes/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../componentes/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";
import { useGuardar } from "../../lib/mutaciones";
import { fecha, numero } from "../../lib/utiles";

const FUENTES: Record<string, string> = {
  declarados: "despliegues declarados",
  proyecto: "proyecto de Foundry",
  api: "API del proveedor",
};

/** Resumen de una sincronización recién hecha (el estado persistido queda en la lista de proveedores). */
function ResumenSincronizacion({ r }: { r: ResultadoSincronizacion }) {
  const fallidos = r.proveedores.filter((p) => p.resultado === "error");
  const reutilizados = r.proveedores.filter((p) => p.reutilizada && p.resultado === "ok");
  if (fallidos.length > 0) {
    return (
      <Aviso tono="aviso">
        Sincronización parcial: {fallidos.map((p) => p.proveedor).join(", ")} no se pudo leer y se conserva su catálogo anterior. Hay{" "}
        {r.modelos} modelos en total.
      </Aviso>
    );
  }
  return (
    <Aviso tono="exito">
      Catálogo sincronizado: {r.modelos} modelos.
      {reutilizados.length > 0 ? " Se reutilizó una lectura reciente para no sobrecargar al proveedor." : ""}
    </Aviso>
  );
}

function FilaProveedor({
  p,
  puedeSincronizar,
  ocupado,
  alSincronizar,
}: {
  p: EstadoProveedorCatalogo;
  puedeSincronizar: boolean;
  ocupado: boolean;
  alSincronizar: (proveedor: Proveedor) => void;
}) {
  const intento = p.ultimo_intento;
  return (
    <li className="flex flex-col gap-2 rounded-lg border border-borde p-3" data-proveedor={p.proveedor}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{p.proveedor}</span>
          {p.configurado ? <Badge tono="info">configurado</Badge> : <Badge tono="neutro">no configurado</Badge>}
          {intento === null ? (
            <Badge tono="neutro">sin sincronizar</Badge>
          ) : intento.resultado === "ok" ? (
            <Badge tono="exito">al día</Badge>
          ) : (
            <Badge tono="peligro">falló la última lectura</Badge>
          )}
        </div>
        {puedeSincronizar && p.configurado ? (
          <Button
            tamano="pequeno"
            variante="secundario"
            aria-label={`Sincronizar ${p.proveedor}`}
            onClick={() => alSincronizar(p.proveedor)}
            disabled={ocupado}
          >
            Sincronizar
          </Button>
        ) : null}
      </div>
      <p className="text-xs text-suave">
        {numero(p.modelos)} modelos guardados · leído {fecha(p.leido_en)}
        {p.fuentes.length > 0 ? ` · fuente: ${p.fuentes.map((f) => FUENTES[f] ?? f).join(" y ")}` : ""}
      </p>
      {intento ? (
        <p className="text-xs text-suave">
          Último intento {fecha(intento.intento_en)}
          {intento.origen === "consola" && intento.por ? ` por ${intento.por}` : intento.origen === "automatica" ? " (automático)" : ""}
          {intento.reutilizada ? " · reutilizó una lectura reciente" : ""}
        </p>
      ) : null}
      {intento?.error ? (
        <div role="alert" className="rounded-md bg-rose-50 p-2 text-sm text-rose-900 dark:bg-rose-950/40 dark:text-rose-100">
          {intento.error.detalle}{" "}
          <span className="text-xs opacity-70">({intento.error.codigo})</span>
          {p.modelos > 0 ? <span className="block text-xs">Se conserva el catálogo anterior.</span> : null}
        </div>
      ) : null}
      {p.aviso ? <Aviso tono="aviso">{p.aviso}</Aviso> : null}
    </li>
  );
}

export function Catalogo({ org, puedeSincronizar }: { org: string; puedeSincronizar: boolean }) {
  const lista = useQuery({ queryKey: claves.catalogo(org), queryFn: () => catalogo.listar(org) });
  const estado = useQuery({ queryKey: claves.catalogoEstado(org), queryFn: () => catalogo.estado(org) });
  const sincronizar = useGuardar((proveedor: Proveedor | undefined) => catalogo.sincronizar(org, proveedor), [claves.catalogo(org)]);
  const sincronizable = estado.data?.sincronizable ?? false;
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between">
        <div>
          <CardTitle>Catálogo de modelos</CardTitle>
          <CardDescription>Modelos que cada proveedor ofrece al servidor, leídos por su API.</CardDescription>
        </div>
        {puedeSincronizar && sincronizable ? (
          <Button tamano="pequeno" variante="secundario" onClick={() => sincronizar.mutate(undefined)} disabled={sincronizar.isPending}>
            {sincronizar.isPending ? "Sincronizando…" : "Sincronizar todos"}
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {sincronizar.isSuccess ? <ResumenSincronizacion r={sincronizar.data} /> : null}
        {sincronizar.isError ? <ErrorVista error={sincronizar.error} /> : null}
        {estado.isPending ? <Cargando /> : null}
        {estado.isError ? <ErrorVista error={estado.error} reintentar={() => void estado.refetch()} /> : null}
        {estado.isSuccess && !estado.data.sincronizable ? (
          <Aviso tono="info">
            Este servidor no tiene proveedores de modelos configurados (Foundry o Anthropic): no hay nada que sincronizar. Lo que se
            muestra abajo es lo último que se guardó.
          </Aviso>
        ) : null}
        {estado.isSuccess && estado.data.proveedores.length > 0 ? (
          <ul className="flex flex-col gap-2" aria-label="Proveedores">
            {estado.data.proveedores.map((p) => (
              <FilaProveedor
                key={p.proveedor}
                p={p}
                puedeSincronizar={puedeSincronizar}
                ocupado={sincronizar.isPending}
                alSincronizar={(proveedor) => sincronizar.mutate(proveedor)}
              />
            ))}
          </ul>
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
