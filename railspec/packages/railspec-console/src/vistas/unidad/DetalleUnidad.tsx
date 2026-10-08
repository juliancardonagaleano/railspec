import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { claves, unidades } from "../../api/endpoints";
import type { EstadoUnidad } from "../../api/tipos";
import { Cargando, ErrorVista } from "../../componentes/Estados";
import { EtiquetaEstado, EtiquetaModo, EtiquetaRiesgo, NOMBRE_FASE } from "../../componentes/Etiquetas";
import { Badge } from "../../componentes/ui/badge";
import { Button } from "../../componentes/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../componentes/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../componentes/ui/tabs";
import { alcanza } from "../../lib/roles";
import { useRol, useWorkspace } from "../../lib/sesion";
import { fecha, nombreActor, usd } from "../../lib/utiles";
import { DagFases } from "./DagFases";
import { DialogoIntegrar } from "./DialogoIntegrar";
import { DialogoModo } from "./DialogoModo";
import { DecisionesMandato } from "../mandato/DecisionesMandato";
import { InsigniaMandato } from "../mandato/EtiquetasMandato";
import { LineaDeTiempo } from "./LineaDeTiempo";
import { PanelCheckpoint } from "./PanelCheckpoint";
import { PestanaGates } from "./PestanaGates";
import { PestanaImpacto } from "./PestanaImpacto";
import { PestanaTrazabilidad } from "./PestanaTrazabilidad";
import { useEventosUnidad, type EstadoConexion } from "./useEventosUnidad";

const TEXTO_CONEXION: Record<EstadoConexion, string> = {
  conectando: "conectando…",
  "en-vivo": "en vivo",
  desconectado: "sin actualizaciones en vivo",
};

/** ¿Algún gate escaló en desatendido y difirió la unidad? */
export const estaDiferida = (estado: EstadoUnidad): boolean => Object.values(estado.gates ?? {}).some((g) => g?.diferido === true && !g.rehabilitado);

function Cabecera({
  estado,
  conexion,
  acciones,
}: {
  estado: EstadoUnidad;
  conexion: EstadoConexion;
  acciones: React.ReactNode;
}) {
  const plan = estado.unidad.plan;
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <p className="font-mono text-xs text-suave">{estado.unidad.unidad}</p>
        <h1 className="text-xl font-semibold">{estado.titulo}</h1>
        <div className="mt-2 flex flex-wrap items-center gap-1">
          <Badge tono="info">{NOMBRE_FASE[estado.fase]}</Badge>
          <EtiquetaEstado estado={estado.estado} />
          <EtiquetaModo modo={estado.modo} />
          {plan ? <InsigniaMandato org={estado.unidad.org} ws={estado.unidad.workspace} mandato={plan} /> : null}
          {estaDiferida(estado) ? <Badge tono="aviso">Diferida (desatendido)</Badge> : null}
          <EtiquetaRiesgo riesgo={estado.riesgo} />
          <Badge>perfil {estado.perfil}</Badge>
          {estado.integracion ? <Badge tono="exito">integrada</Badge> : null}
          <Badge tono={conexion === "en-vivo" ? "exito" : "neutro"} aria-live="polite">
            {TEXTO_CONEXION[conexion]}
          </Badge>
        </div>
        <p className="mt-2 text-xs text-suave">
          Dueño {nombreActor(estado.dueno)} · repos {estado.repositorios.map((r) => `${r.repositorio} (${r.rol})`).join(", ")} ·
          actualizada {fecha(estado.actualizado_en)} · versión {estado.version}
          {estado.consumo?.costo_usd !== undefined ? ` · consumo ${usd(estado.consumo.costo_usd)}` : ""}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">{acciones}</div>
    </div>
  );
}

function TarjetaIntegracion({ estado }: { estado: EstadoUnidad }) {
  const i = estado.integracion;
  if (!i) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Integración</CardTitle>
      </CardHeader>
      <CardContent className="text-sm">
        <p>
          Especificación viva: <span className="font-mono">{i.especificacion_viva}</span>
        </p>
        {i.pr_url ? (
          <p>
            PR:{" "}
            <a href={i.pr_url} target="_blank" rel="noreferrer noopener" className="text-primario underline">
              {i.pr_url}
            </a>
          </p>
        ) : null}
        <p className="text-xs text-suave">
          Por {nombreActor(i.actor)} el {fecha(i.en)}
        </p>
      </CardContent>
    </Card>
  );
}

function PestanaLinea({ org, ws, u }: { org: string; ws: string; u: string }) {
  const q = useQuery({ queryKey: [...claves.unidad(org, ws, u), "linea"], queryFn: () => unidades.lineaDeTiempo(org, ws, u) });
  if (q.isPending) return <Cargando />;
  if (q.isError) return <ErrorVista error={q.error} reintentar={() => void q.refetch()} />;
  return <LineaDeTiempo datos={q.data} />;
}

function PestanaTraza({ org, ws, u }: { org: string; ws: string; u: string }) {
  const q = useQuery({ queryKey: [...claves.unidad(org, ws, u), "traza"], queryFn: () => unidades.trazabilidad(org, ws, u) });
  if (q.isPending) return <Cargando />;
  if (q.isError) return <ErrorVista error={q.error} reintentar={() => void q.refetch()} />;
  return <PestanaTrazabilidad datos={q.data} org={org} ws={ws} unidad={u} />;
}

export function DetalleUnidad() {
  const { org, ws } = useWorkspace();
  const { unidad: u } = useParams({ strict: false }) as { unidad: string };
  const { rol } = useRol();
  const [pestana, setPestana] = useState("linea");
  const [integrando, setIntegrando] = useState(false);
  const [cambiandoModo, setCambiandoModo] = useState(false);
  const conexion = useEventosUnidad(org, ws, u);
  const detalle = useQuery({ queryKey: [...claves.unidad(org, ws, u), "detalle"], queryFn: () => unidades.detalle(org, ws, u) });

  if (detalle.isPending) return <Cargando texto="Cargando unidad…" />;
  if (detalle.isError)
    return (
      <>
        <Link to="/$org/$ws" params={{ org, ws }} className="mb-3 inline-block text-sm text-primario">
          ← Tablero
        </Link>
        <ErrorVista error={detalle.error} reintentar={() => void detalle.refetch()} />
      </>
    );

  const { estado, orden_vigente } = detalle.data;
  const desarrollador = alcanza(rol, "desarrollador");
  const puedeIntegrar = desarrollador && estado.fase === "done" && !estado.integracion;
  const alcance = { org: estado.unidad.org, workspace: estado.unidad.workspace, unidad: estado.unidad.unidad };

  return (
    <>
      <Link to="/$org/$ws" params={{ org, ws }} className="mb-3 inline-block text-sm text-primario">
        ← Tablero
      </Link>
      <Cabecera
        estado={estado}
        conexion={conexion}
        acciones={
          <>
            {desarrollador ? (
              <Button variante="secundario" onClick={() => setCambiandoModo(true)}>
                Cambiar modo
              </Button>
            ) : null}
            {puedeIntegrar ? <Button onClick={() => setIntegrando(true)}>Integrar</Button> : null}
          </>
        }
      />
      <div className="flex flex-col gap-4">
        <DagFases estado={estado} />
        {estado.checkpoint_pendiente ? (
          <PanelCheckpoint alcance={alcance} checkpoint={estado.checkpoint_pendiente} puedeResolver={desarrollador} />
        ) : null}
        <TarjetaIntegracion estado={estado} />
        {estado.decisiones && estado.decisiones.length > 0 ? (
          <Card>
            <CardHeader>
              <CardTitle>Decisiones delegadas ({estado.decisiones.length})</CardTitle>
              <CardDescription>
                Lo que el arnés decidió apoyándose en una delegación del mandato{estado.unidad.plan ? ` ${estado.unidad.plan}` : ""}. Revertir no deshace el
                código: deja constancia y lo revierte una persona.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <DecisionesMandato
                org={org}
                ws={ws}
                decisiones={estado.decisiones.map((decision) => ({ unidad: estado.unidad.unidad, decision }))}
                puedeRevisar={desarrollador}
                versionUnidad={async () => estado.version}
              />
            </CardContent>
          </Card>
        ) : null}
        {orden_vigente ? (
          <p className="text-sm text-suave">
            Orden vigente: {orden_vigente.tipo} #{orden_vigente.secuencia} (fase {orden_vigente.fase}, {orden_vigente.repositorio}) emitida{" "}
            {fecha(orden_vigente.emitida_en)}
          </p>
        ) : null}
        <Tabs valor={pestana} alCambiar={setPestana}>
          <TabsList etiqueta="Detalle de la unidad">
            <TabsTrigger valor="linea">Línea de tiempo</TabsTrigger>
            <TabsTrigger valor="gates">Gates</TabsTrigger>
            <TabsTrigger valor="traza">Trazabilidad CA-NN</TabsTrigger>
            <TabsTrigger valor="impacto">Impacto</TabsTrigger>
          </TabsList>
          <TabsContent valor="linea">
            <PestanaLinea org={org} ws={ws} u={u} />
          </TabsContent>
          <TabsContent valor="gates">
            <PestanaGates estado={estado} />
          </TabsContent>
          <TabsContent valor="traza">
            <PestanaTraza org={org} ws={ws} u={u} />
          </TabsContent>
          <TabsContent valor="impacto">
            <PestanaImpacto org={org} ws={ws} unidad={u} />
          </TabsContent>
        </Tabs>
      </div>
      <DialogoIntegrar alcance={alcance} abierto={integrando} alCerrar={() => setIntegrando(false)} />
      {cambiandoModo ? <DialogoModo estado={estado} abierto alCerrar={() => setCambiandoModo(false)} /> : null}
    </>
  );
}
