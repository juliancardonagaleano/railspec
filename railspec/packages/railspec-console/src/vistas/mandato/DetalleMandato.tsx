import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { claves, mandatos, unidades } from "../../api/endpoints";
import type { Mandato, MandateGetSalida } from "../../api/tipos";
import { Aviso, Cargando, ErrorVista } from "../../componentes/Estados";
import { EtiquetaEstado, EtiquetaModo, NOMBRE_FASE } from "../../componentes/Etiquetas";
import { Badge } from "../../componentes/ui/badge";
import { Button } from "../../componentes/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../componentes/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";
import { alcanza } from "../../lib/roles";
import { useRol, useWorkspace } from "../../lib/sesion";
import { fecha, nombreActor, usd } from "../../lib/utiles";
import { ContenidoMandato } from "./ContenidoMandato";
import { DecisionesMandato } from "./DecisionesMandato";
import { DialogoAprobarMandato } from "./DialogoAprobarMandato";
import { DialogoRevocarMandato } from "./DialogoRevocarMandato";
import { EtiquetaCausaParada, EtiquetaEstadoMandato } from "./EtiquetasMandato";
import { FormularioMandato } from "./FormularioMandato";
import { EXPLICACION_CAUSA } from "./mandato";

function TarjetaEstado({ m, salida }: { m: Mandato; salida: MandateGetSalida }) {
  const parada = m.parada;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Estado y vigencia</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-sm">
        <p className="flex flex-wrap items-center gap-2">
          <EtiquetaEstadoMandato estado={m.estado} vigente={salida.vigente} />
          {salida.vigente ? "Ampara trabajo ahora." : "No ampara trabajo ahora: el motor no avanza sus unidades."}
        </p>
        {salida.motivo_no_vigente ? <p className="text-suave">{salida.motivo_no_vigente}</p> : null}
        {m.aprobaciones.length > 0 ? (
          <p>
            {salida.vigente ? "Vigente hasta" : "La última aprobación valía hasta"} <strong>{fecha(m.aprobaciones[m.aprobaciones.length - 1]?.caduca_en)}</strong>.
          </p>
        ) : null}
        {parada ? (
          <Aviso tono="aviso">
            <p className="flex flex-wrap items-center gap-2">
              Parada: <EtiquetaCausaParada causa={parada.causa} /> el {fecha(parada.en)}
              {parada.unidad ? ` (unidad ${parada.unidad})` : ""}
            </p>
            <p className="mt-1">{parada.detalle}</p>
            <p className="mt-1 text-xs">{EXPLICACION_CAUSA[parada.causa].texto}</p>
          </Aviso>
        ) : null}
        {m.revocacion ? (
          <Aviso tono="aviso">
            Revocado por {nombreActor(m.revocacion.actor)} el {fecha(m.revocacion.en)}: {m.revocacion.motivo}
          </Aviso>
        ) : null}
        <p className="text-xs text-suave">
          Versión {m.version} · creado por {nombreActor(m.creado_por)} el {fecha(m.creado_en)} · actualizado por {nombreActor(m.actualizado_por)} el{" "}
          {fecha(m.actualizado_en)}
        </p>
      </CardContent>
    </Card>
  );
}

function TarjetaAprobaciones({ m }: { m: Mandato }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Historial de aprobaciones</CardTitle>
        <CardDescription>Solo crece: cada aprobación o renovación queda ligada a la huella del contenido que se vio.</CardDescription>
      </CardHeader>
      <CardContent>
        {m.aprobaciones.length === 0 ? (
          <p className="text-sm text-suave">Nunca se aprobó.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Cuándo</TableHead>
                <TableHead>Quién</TableHead>
                <TableHead>Caducaba</TableHead>
                <TableHead>Huella</TableHead>
                <TableHead>Comentario</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {[...m.aprobaciones].reverse().map((a) => (
                <TableRow key={`${a.en}-${a.huella}`}>
                  <TableCell>{fecha(a.en)}</TableCell>
                  <TableCell>{nombreActor(a.actor)}</TableCell>
                  <TableCell>{fecha(a.caduca_en)}</TableCell>
                  <TableCell className="font-mono text-xs" title={a.huella}>
                    {a.huella.slice(0, 12)}…
                  </TableCell>
                  <TableCell>{a.comentario ?? "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function TarjetaUnidades({ org, ws, salida }: { org: string; ws: string; salida: MandateGetSalida }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          Unidades del mandato ({salida.unidades.length}/{salida.mandato.contenido.limites.max_unidades})
        </CardTitle>
      </CardHeader>
      <CardContent>
        {salida.unidades.length === 0 ? (
          <p className="text-sm text-suave">Ninguna unidad usa este mandato todavía.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Unidad</TableHead>
                <TableHead>Fase</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead>Consumo</TableHead>
                <TableHead>Avisos</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {salida.unidades.map((u) => (
                <TableRow key={u.unidad}>
                  <TableCell>
                    <Link to="/$org/$ws/unidades/$unidad" params={{ org, ws, unidad: u.unidad }} className="font-medium text-primario underline">
                      {u.titulo}
                    </Link>
                    <p className="font-mono text-xs text-suave">{u.unidad}</p>
                  </TableCell>
                  <TableCell>{NOMBRE_FASE[u.fase]}</TableCell>
                  <TableCell>
                    <EtiquetaEstado estado={u.estado} />
                  </TableCell>
                  <TableCell>{usd(u.consumo.costo_usd ?? 0)}</TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {u.diferida ? <Badge tono="aviso">diferida</Badge> : null}
                      {u.causa_parada ? <EtiquetaCausaParada causa={u.causa_parada} /> : null}
                      {u.decisiones_pendientes > 0 ? <Badge tono="info">{u.decisiones_pendientes} decisión(es) pendiente(s)</Badge> : null}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

/** Detalle de un mandato (`mandate.get`): contenido, estado, historial, unidades, decisiones y acciones. */
export function DetalleMandato() {
  const { org, ws } = useWorkspace();
  const { mandato: id } = useParams({ strict: false }) as { mandato: string };
  const { rol } = useRol();
  const [dialogo, setDialogo] = useState<"aprobar" | "revocar" | "editar" | null>(null);
  const q = useQuery({
    queryKey: claves.mandato(org, ws, id),
    queryFn: () => mandatos.obtener({ org, workspace: ws }, id),
  });
  const atras = (
    <Link to="/$org/$ws/mandatos" params={{ org, ws }} className="mb-3 inline-block text-sm text-primario">
      ← Mandatos
    </Link>
  );

  if (q.isPending) return <Cargando texto="Cargando mandato…" />;
  if (q.isError)
    return (
      <>
        {atras}
        <ErrorVista error={q.error} reintentar={() => void q.refetch()} />
      </>
    );

  const salida = q.data;
  const m = salida.mandato;
  const puedeEscribir = alcanza(rol, "desarrollador");
  const cerrado = m.estado === "revocado";
  const renovar = m.estado !== "propuesto";
  // `mandate.get` no trae la versión del estado de cada unidad: se lee al revisar una decisión.
  const versionUnidad = async (unidad: string) => (await unidades.detalle(org, ws, unidad)).estado.version;

  return (
    <>
      {atras}
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-mono text-xs text-suave">{m.id}</p>
          <h1 className="text-xl font-semibold">{m.contenido.titulo}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-1">
            <EtiquetaModo modo={m.contenido.modo} />
            <EtiquetaEstadoMandato estado={m.estado} vigente={salida.vigente} />
            {m.parada ? <EtiquetaCausaParada causa={m.parada.causa} /> : null}
          </div>
        </div>
        {puedeEscribir && !cerrado ? (
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => setDialogo("aprobar")}>{renovar ? "Renovar" : "Aprobar"}</Button>
            <Button variante="secundario" onClick={() => setDialogo("editar")}>
              Editar
            </Button>
            <Button variante="peligro" onClick={() => setDialogo("revocar")}>
              Revocar
            </Button>
          </div>
        ) : null}
      </div>

      <div className="flex flex-col gap-4">
        <TarjetaEstado m={m} salida={salida} />
        <Card>
          <CardHeader>
            <CardTitle>Contenido</CardTitle>
            <CardDescription>Esto es lo que se aprueba; cambiarlo devuelve el mandato a «propuesto».</CardDescription>
          </CardHeader>
          <CardContent>
            <ContenidoMandato contenido={m.contenido} consumo={salida.consumo} />
          </CardContent>
        </Card>
        <TarjetaAprobaciones m={m} />
        <TarjetaUnidades org={org} ws={ws} salida={salida} />
        <Card>
          <CardHeader>
            <CardTitle>Decisiones delegadas ({salida.decisiones.length})</CardTitle>
            <CardDescription>
              Lo que el arnés decidió apoyándose en una delegación. Revertir no deshace el código: deja constancia y lo revierte una persona.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <DecisionesMandato
              org={org}
              ws={ws}
              decisiones={salida.decisiones}
              puedeRevisar={puedeEscribir}
              versionUnidad={versionUnidad}
              enlazarUnidad
            />
          </CardContent>
        </Card>
      </div>

      {dialogo === "aprobar" ? <DialogoAprobarMandato salida={salida} org={org} ws={ws} alCerrar={() => setDialogo(null)} /> : null}
      {dialogo === "revocar" ? <DialogoRevocarMandato mandato={m} org={org} ws={ws} alCerrar={() => setDialogo(null)} /> : null}
      {dialogo === "editar" ? <FormularioMandato org={org} ws={ws} mandato={m} alCerrar={() => setDialogo(null)} /> : null}
    </>
  );
}
