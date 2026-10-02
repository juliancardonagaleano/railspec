import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { claves, presupuestos } from "../../api/endpoints";
import { FASES, type Fase, type Presupuesto, type PresupuestoConfig } from "../../api/tipos";
import { Cargando, ErrorVista } from "../../componentes/Estados";
import { NOMBRE_FASE } from "../../componentes/Etiquetas";
import { Button } from "../../componentes/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../componentes/ui/card";
import { Campo, Input } from "../../componentes/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";
import { AvisoGuardado, ErrorGuardado, numeroOpcional, useGuardar } from "../../lib/mutaciones";

type Textos = { tokens_max: string; segundos_max: string; costo_usd_max: string };
const CAMPOS: (keyof Textos)[] = ["tokens_max", "segundos_max", "costo_usd_max"];
const ETIQUETA: Record<keyof Textos, string> = { tokens_max: "Tokens máx.", segundos_max: "Segundos máx.", costo_usd_max: "Costo USD máx." };

const aTextos = (p: Presupuesto | undefined): Textos => ({
  tokens_max: p?.tokens_max?.toString() ?? "",
  segundos_max: p?.segundos_max?.toString() ?? "",
  costo_usd_max: p?.costo_usd_max?.toString() ?? "",
});

/** Solo incluye los topes con valor (el esquema no admite 0). */
export function aPresupuesto(t: Textos): Presupuesto {
  const r: Presupuesto = {};
  for (const c of CAMPOS) {
    const n = numeroOpcional(t[c]);
    if (n !== null && n > 0) r[c] = n;
  }
  return r;
}

function FormularioPresupuesto({ org, ws, actual, editable }: { org: string; ws?: string; actual?: PresupuestoConfig; editable: boolean }) {
  const [porUnidad, setPorUnidad] = useState<Textos>(aTextos(actual?.por_unidad));
  const [porFase, setPorFase] = useState<Record<Fase, Textos>>(
    Object.fromEntries(FASES.map((f) => [f, aTextos(actual?.por_fase?.[f])])) as Record<Fase, Textos>,
  );
  const [mensual, setMensual] = useState(actual?.mensual_usd?.toString() ?? "");
  const guardar = useGuardar(
    () => {
      const por_fase: Partial<Record<Fase, Presupuesto>> = {};
      for (const f of FASES) {
        const p = aPresupuesto(porFase[f]);
        if (Object.keys(p).length) por_fase[f] = p;
      }
      const m = numeroOpcional(mensual);
      return presupuestos.guardar(
        org,
        {
          por_unidad: aPresupuesto(porUnidad),
          por_fase,
          mensual_usd: m && m > 0 ? m : null,
          ...(actual ? { version: actual.version } : {}),
        },
        ws,
      );
    },
    [["presupuestos", org]],
    undefined,
    { clave: `presupuestos:${org}:${ws ?? ""}` },
  );
  const enviar = (e: FormEvent) => {
    e.preventDefault();
    guardar.mutate(undefined);
  };
  return (
    <form onSubmit={enviar} className="flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-4">
        <Campo etiqueta="Presupuesto mensual (USD)" htmlFor="pres-mensual">
          <Input id="pres-mensual" type="number" min={0} step="0.01" disabled={!editable} value={mensual} onChange={(e) => setMensual(e.target.value)} />
        </Campo>
        {CAMPOS.map((c) => (
          <Campo key={c} etiqueta={`Por unidad: ${ETIQUETA[c]}`} htmlFor={`pres-u-${c}`}>
            <Input
              id={`pres-u-${c}`}
              type="number"
              min={0}
              step={c === "costo_usd_max" ? "0.01" : "1"}
              disabled={!editable}
              value={porUnidad[c]}
              onChange={(e) => setPorUnidad((p) => ({ ...p, [c]: e.target.value }))}
            />
          </Campo>
        ))}
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Fase</TableHead>
            {CAMPOS.map((c) => (
              <TableHead key={c}>{ETIQUETA[c]}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {FASES.map((f) => (
            <TableRow key={f}>
              <TableCell>{NOMBRE_FASE[f]}</TableCell>
              {CAMPOS.map((c) => (
                <TableCell key={c}>
                  <Input
                    aria-label={`${ETIQUETA[c]} en ${NOMBRE_FASE[f]}`}
                    type="number"
                    min={0}
                    step={c === "costo_usd_max" ? "0.01" : "1"}
                    className="h-8"
                    disabled={!editable}
                    value={porFase[f][c]}
                    onChange={(e) => setPorFase((p) => ({ ...p, [f]: { ...p[f], [c]: e.target.value } }))}
                  />
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <AvisoGuardado guardado={guardar.guardado} />
      <ErrorGuardado error={guardar.error} />
      {editable ? (
        <div>
          <Button type="submit" disabled={guardar.isPending}>
            {guardar.isPending ? "Guardando…" : "Guardar presupuesto"}
          </Button>
        </div>
      ) : null}
    </form>
  );
}

export function Presupuestos({ org, ws, editable }: { org: string; ws?: string; editable: boolean }) {
  const lista = useQuery({ queryKey: claves.presupuestos(org, ws), queryFn: () => presupuestos.listar(org, ws) });
  const actual = lista.data?.find((p) => (p.workspace ?? undefined) === ws);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Presupuestos</CardTitle>
        <CardDescription>
          Topes que aplica el servidor; al agotarse escala al humano. Campos vacíos = sin tope.
          {ws ? " Sin presupuesto propio, el workspace usa el de la organización." : ""}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {lista.isPending ? <Cargando /> : null}
        {lista.isError ? <ErrorVista error={lista.error} reintentar={() => void lista.refetch()} /> : null}
        {lista.isSuccess ? (
          <FormularioPresupuesto key={actual?.version ?? 0} org={org} {...(ws ? { ws } : {})} {...(actual ? { actual } : {})} editable={editable} />
        ) : null}
      </CardContent>
    </Card>
  );
}
