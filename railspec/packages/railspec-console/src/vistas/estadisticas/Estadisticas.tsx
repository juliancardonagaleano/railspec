import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { claves, telemetria } from "../../api/endpoints";
import { GATES, type ClaveTelemetria, type ResumenWorkspace } from "../../api/tipos";
import { Cargando, Encabezado, ErrorVista, Vacio } from "../../componentes/Estados";
import { NOMBRE_FASE } from "../../componentes/Etiquetas";
import { Grafico } from "../../componentes/Grafico";
import { Badge } from "../../componentes/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../../componentes/ui/card";
import { Select } from "../../componentes/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";
import { useWorkspace } from "../../lib/sesion";
import { cn, commitCorto, numero, usd } from "../../lib/utiles";
import { estadoGasto, rangoDias, totalesPor, type Total } from "./agregar";

const RANGOS = [7, 30, 90] as const;

function opcionBarras(totales: Total[], titulo: string) {
  return {
    tooltip: { trigger: "axis" },
    legend: { data: ["Costo (USD)", "Tokens"] },
    grid: { left: 60, right: 60, bottom: 60, top: 40 },
    xAxis: { type: "category", data: totales.map((t) => t.clave), axisLabel: { rotate: totales.length > 5 ? 30 : 0 } },
    yAxis: [
      { type: "value", name: "USD" },
      { type: "value", name: "tokens" },
    ],
    series: [
      { name: "Costo (USD)", type: "bar", data: totales.map((t) => Number(t.costo_usd.toFixed(4))) },
      {
        name: "Tokens",
        type: "bar",
        yAxisIndex: 1,
        data: totales.map((t) => t.tokens_entrada + t.tokens_salida),
      },
    ],
    aria: { enabled: true, description: titulo },
  };
}

function useAgrupado(org: string, ws: string, dias: number, clave: ClaveTelemetria) {
  return useQuery({
    queryKey: [...claves.telemetria(org, ws), dias, clave],
    queryFn: () => {
      const { desde, hasta } = rangoDias(dias);
      return telemetria.consultar({ org, workspace: ws, desde, hasta, agrupar_por: [clave], filtros: {} });
    },
    select: (s) => totalesPor(s.filas, clave),
  });
}

function TarjetaGrafico({ titulo, consulta }: { titulo: string; consulta: ReturnType<typeof useAgrupado> }) {
  const opcion = useMemo(() => opcionBarras(consulta.data ?? [], titulo), [consulta.data, titulo]);
  return (
    <Card>
      <CardHeader>
        <CardTitle>{titulo}</CardTitle>
      </CardHeader>
      <CardContent>
        {consulta.isPending ? <Cargando /> : null}
        {consulta.isError ? <ErrorVista error={consulta.error} reintentar={() => void consulta.refetch()} /> : null}
        {consulta.isSuccess && consulta.data.length === 0 ? <Vacio titulo="Sin llamadas en el rango" /> : null}
        {consulta.isSuccess && consulta.data.length > 0 ? <Grafico opcion={opcion} etiqueta={titulo} /> : null}
      </CardContent>
    </Card>
  );
}

function TablaUnidades({ consulta }: { consulta: ReturnType<typeof useAgrupado> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Unidades más caras</CardTitle>
      </CardHeader>
      <CardContent>
        {consulta.isPending ? <Cargando /> : null}
        {consulta.isError ? <ErrorVista error={consulta.error} /> : null}
        {consulta.isSuccess && consulta.data.length === 0 ? <Vacio titulo="Sin datos" /> : null}
        {consulta.isSuccess && consulta.data.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Unidad</TableHead>
                <TableHead className="text-right">Llamadas</TableHead>
                <TableHead className="text-right">Tokens entrada</TableHead>
                <TableHead className="text-right">Tokens salida</TableHead>
                <TableHead className="text-right">Costo</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {consulta.data.slice(0, 15).map((t) => (
                <TableRow key={t.clave}>
                  <TableCell className="font-mono text-xs">{t.clave}</TableCell>
                  <TableCell className="text-right">{numero(t.llamadas)}</TableCell>
                  <TableCell className="text-right">{numero(t.tokens_entrada)}</TableCell>
                  <TableCell className="text-right">{numero(t.tokens_salida)}</TableCell>
                  <TableCell className="text-right">{usd(t.costo_usd)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </CardContent>
    </Card>
  );
}

function Pares({ datos }: { datos: Record<string, number | undefined> }) {
  const entradas = Object.entries(datos).filter(([, v]) => v);
  if (entradas.length === 0) return <p className="text-sm text-suave">—</p>;
  return (
    <ul className="flex flex-wrap gap-1">
      {entradas.map(([k, v]) => (
        <li key={k}>
          <Badge>
            {k in NOMBRE_FASE ? NOMBRE_FASE[k as keyof typeof NOMBRE_FASE] : k}: {v}
          </Badge>
        </li>
      ))}
    </ul>
  );
}

function TarjetasResumen({ r }: { r: ResumenWorkspace }) {
  const gasto = estadoGasto(r.gasto.mes_usd, r.gasto.presupuesto_mensual_usd);
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <Card>
        <CardHeader>
          <CardTitle>Unidades</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 text-sm">
          <p className="text-2xl font-semibold">{r.unidades.total}</p>
          <p className="text-suave">
            {r.unidades.integradas} integradas · {r.unidades.checkpoints_pendientes} checkpoints pendientes
          </p>
          <p className="text-xs font-medium">Por fase</p>
          <Pares datos={r.unidades.por_fase} />
          <p className="text-xs font-medium">Por estado</p>
          <Pares datos={r.unidades.por_estado} />
        </CardContent>
      </Card>
      <Card
        className={cn(
          gasto.nivel === "alerta" && "border-amber-400 dark:border-amber-700",
          gasto.nivel === "excedido" && "border-rose-500 dark:border-rose-700",
        )}
      >
        <CardHeader>
          <CardTitle>Gasto del mes</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 text-sm">
          <p className="text-2xl font-semibold">{usd(r.gasto.mes_usd)}</p>
          {gasto.pct === null ? (
            <p className="text-suave">Sin presupuesto mensual configurado.</p>
          ) : (
            <>
              <div
                role="meter"
                aria-label="Presupuesto mensual consumido"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(Math.min(gasto.pct, 100))}
                className="h-2 w-full overflow-hidden rounded-full bg-fondo"
              >
                <div
                  className={cn("h-full", gasto.nivel === "ok" ? "bg-emerald-500" : gasto.nivel === "alerta" ? "bg-amber-500" : "bg-rose-600")}
                  style={{ width: `${Math.min(gasto.pct, 100)}%` }}
                />
              </div>
              <p className={cn(gasto.nivel !== "ok" && "font-semibold text-peligro")}>
                {gasto.pct.toFixed(0)} % de {usd(r.gasto.presupuesto_mensual_usd)}
                {gasto.nivel === "alerta" ? " — supera el 80 %" : gasto.nivel === "excedido" ? " — presupuesto excedido" : ""}
              </p>
            </>
          )}
        </CardContent>
      </Card>
      <Card className="xl:col-span-2">
        <CardHeader>
          <CardTitle>Convergencia de gates</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Gate</TableHead>
                <TableHead className="text-right">Aprob.</TableHead>
                <TableHead className="text-right">Refin.</TableHead>
                <TableHead className="text-right">Escal.</TableHead>
                <TableHead className="text-right">Iter. media</TableHead>
                <TableHead>Hallazgos (alta/media/baja)</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {GATES.map((g) => {
                const x = r.gates[g];
                return (
                  <TableRow key={g}>
                    <TableCell>{g}</TableCell>
                    <TableCell className="text-right">{x?.aprobado ?? 0}</TableCell>
                    <TableCell className="text-right">{x?.refinado ?? 0}</TableCell>
                    <TableCell className="text-right">{x?.escalado ?? 0}</TableCell>
                    <TableCell className="text-right">{x?.iteraciones_media?.toFixed(1) ?? "—"}</TableCell>
                    <TableCell>
                      {x ? `${x.hallazgos.alta ?? 0} / ${x.hallazgos.media ?? 0} / ${x.hallazgos.baja ?? 0}` : "—"}
                      {x?.rehabilitados ? <span className="text-xs text-suave"> · {x.rehabilitados} rehab.</span> : null}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      <Card className="md:col-span-2 xl:col-span-4">
        <CardHeader>
          <CardTitle>Frescura del grafo</CardTitle>
        </CardHeader>
        <CardContent>
          {r.grafo.length === 0 ? (
            <p className="text-sm text-suave">Sin repositorios vinculados.</p>
          ) : (
            <ul className="flex flex-wrap gap-2 text-sm">
              {r.grafo.map((g) => (
                <li key={g.repositorio} className="rounded-md border border-borde px-2 py-1">
                  <span className="font-medium">{g.repositorio}</span>{" "}
                  <span className="text-xs text-suave">{g.nivel_codigo}</span>{" "}
                  <Badge tono={g.commit ? "exito" : "aviso"}>{commitCorto(g.commit)}</Badge>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export function Estadisticas() {
  const { org, ws } = useWorkspace();
  const [dias, setDias] = useState<number>(30);
  const resumen = useQuery({ queryKey: [...claves.telemetria(org, ws), "resumen"], queryFn: () => telemetria.resumen(org, ws) });
  const porFase = useAgrupado(org, ws, dias, "fase");
  const porModelo = useAgrupado(org, ws, dias, "modelo");
  const porUnidad = useAgrupado(org, ws, dias, "unidad");

  return (
    <>
      <Encabezado
        titulo="Estadísticas"
        descripcion="Costo, tokens y convergencia del workspace."
        acciones={
          <div className="flex items-center gap-2">
            <label htmlFor="rango" className="text-sm">
              Rango
            </label>
            <Select
              id="rango"
              className="w-36"
              value={String(dias)}
              onChange={(e) => setDias(Number(e.target.value))}
              opciones={RANGOS.map((d) => ({ valor: String(d), etiqueta: `Últimos ${d} días` }))}
            />
          </div>
        }
      />
      <section aria-label="Resumen" className="mb-6">
        {resumen.isPending ? <Cargando texto="Cargando resumen…" /> : null}
        {resumen.isError ? <ErrorVista error={resumen.error} reintentar={() => void resumen.refetch()} /> : null}
        {resumen.data ? <TarjetasResumen r={resumen.data} /> : null}
      </section>
      <section aria-label="Telemetría" className="grid gap-4 lg:grid-cols-2">
        <TarjetaGrafico titulo="Costo y tokens por fase" consulta={porFase} />
        <TarjetaGrafico titulo="Costo y tokens por modelo" consulta={porModelo} />
        <div className="lg:col-span-2">
          <TablaUnidades consulta={porUnidad} />
        </div>
      </section>
    </>
  );
}
