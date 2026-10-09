import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { claves, operacion } from "../../api/endpoints";
import type { ClaveMetricas, ClaveMetricasCreada, Operacion as DatosOperacion, PeticionesOperacion } from "../../api/tipos";
import { Aviso, Cargando, Encabezado, ErrorVista, Vacio } from "../../componentes/Estados";
import { Badge } from "../../componentes/ui/badge";
import { Button } from "../../componentes/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../componentes/ui/card";
import { Campo, Input } from "../../componentes/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";
import { ErrorGuardado, useGuardar } from "../../lib/mutaciones";
import { useRol } from "../../lib/sesion";
import { fecha, numero, porcentaje } from "../../lib/utiles";

/** Cada cuánto se refresca el estado: lo bastante para verlo en vivo sin martillar el servidor. */
export const REFRESCO_MS = 30_000;

const NOMBRE_GRUPO: Record<string, string> = {
  consola_api: "API de la consola",
  consola: "Consola (páginas)",
  mcp: "MCP (arnés)",
  v1: "API /v1",
  sondas: "Sondas y métricas",
  otras: "Otras rutas",
};

export interface FilaPeticiones {
  grupo: string;
  ok: number;
  cliente: number;
  servidor: number;
  total: number;
}

/** Suma los métodos de cada superficie: 2xx (y 3xx), 4xx y 5xx. */
export function porGrupo(peticiones: PeticionesOperacion[]): FilaPeticiones[] {
  const filas = new Map<string, FilaPeticiones>();
  for (const p of peticiones) {
    const f = filas.get(p.grupo) ?? { grupo: p.grupo, ok: 0, cliente: 0, servidor: 0, total: 0 };
    if (p.estado === "5xx") f.servidor += p.total;
    else if (p.estado === "4xx") f.cliente += p.total;
    else f.ok += p.total;
    f.total += p.total;
    filas.set(p.grupo, f);
  }
  return [...filas.values()].sort((a, b) => b.total - a.total);
}

/** «3 d 4 h», «5 h 12 min», «8 min». */
export function tiempoArriba(desde: string, hasta: string): string {
  const min = Math.max(0, Math.floor((new Date(hasta).getTime() - new Date(desde).getTime()) / 60_000));
  const d = Math.floor(min / 1440);
  const h = Math.floor((min % 1440) / 60);
  if (d > 0) return `${d} d ${h} h`;
  if (h > 0) return `${h} h ${min % 60} min`;
  return `${min} min`;
}

export function Operacion() {
  const { plataformaAdmin } = useRol();
  if (!plataformaAdmin) {
    return (
      <div className="p-6">
        <Vacio titulo="Solo quien administra la plataforma ve la operación del servidor" />
      </div>
    );
  }
  return (
    <>
      <Encabezado
        titulo="Operación del servidor"
        descripcion="Lo mismo que publica /metrics, en vivo. Los contadores son de esta réplica y vuelven a cero cuando el servicio se reinicia o duerme."
      />
      <Estado />
      <ClavesDeMetricas />
    </>
  );
}

function Estado() {
  const consulta = useQuery({ queryKey: claves.operacion, queryFn: operacion.ver, refetchInterval: REFRESCO_MS });
  if (consulta.isPending) return <Cargando />;
  if (consulta.isError) return <ErrorVista error={consulta.error} reintentar={() => void consulta.refetch()} />;
  return <Resumen datos={consulta.data} />;
}

function Resumen({ datos }: { datos: DatosOperacion }) {
  const caidas = datos.sondas.filter((s) => s.estado !== "ok");
  const alineado = datos.esquema.codigo === datos.esquema.almacenado;
  const filas = porGrupo(datos.peticiones);
  return (
    <div className="mb-6 grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle>Estado</CardTitle>
          <CardDescription>Se actualiza cada {REFRESCO_MS / 1000} s.</CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-suave">Bases</dt>
            <dd>
              {datos.sondas.length === 0 ? (
                <span>Sin bases que sondear (estado en memoria)</span>
              ) : (
                <ul className="flex flex-wrap gap-2">
                  {datos.sondas.map((s) => (
                    <li key={s.nombre}>
                      <Badge tono={s.estado === "ok" ? "exito" : "peligro"}>
                        {s.nombre}: {s.estado === "ok" ? "responde" : s.estado}
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
            </dd>
            <dt className="text-suave">Versión</dt>
            <dd className="font-mono">{datos.version}</dd>
            <dt className="text-suave">Esquema del estado</dt>
            <dd>
              {alineado ? (
                <Badge tono="exito">{datos.esquema.codigo}</Badge>
              ) : (
                <Badge tono="aviso">
                  código {datos.esquema.codigo}, base {datos.esquema.almacenado}
                </Badge>
              )}
            </dd>
            <dt className="text-suave">Arriba desde</dt>
            <dd>
              {fecha(datos.inicio)} ({tiempoArriba(datos.inicio, datos.ahora)})
            </dd>
          </dl>
          {caidas.length > 0 ? (
            <Aviso tono="aviso" className="mt-3">
              No responde: {caidas.map((s) => s.nombre).join(", ")}. Mientras siga así, /healthz da 503 y las unidades no avanzan.
            </Aviso>
          ) : null}
          {!alineado ? (
            <Aviso tono="aviso" className="mt-3">
              El esquema guardado no es el que espera este código: revisa el último despliegue.
            </Aviso>
          ) : null}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Peticiones desde el arranque</CardTitle>
          <CardDescription>Por superficie. Los 5xx son fallos del servidor; los 4xx, peticiones rechazadas.</CardDescription>
        </CardHeader>
        <CardContent>
          {filas.length === 0 ? (
            <p className="text-sm text-suave">Todavía no hay peticiones.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Superficie</TableHead>
                  <TableHead className="text-right">Correctas</TableHead>
                  <TableHead className="text-right">4xx</TableHead>
                  <TableHead className="text-right">5xx</TableHead>
                  <TableHead className="text-right">% 5xx</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filas.map((f) => (
                  <TableRow key={f.grupo}>
                    <TableCell>{NOMBRE_GRUPO[f.grupo] ?? f.grupo}</TableCell>
                    <TableCell className="text-right">{numero(f.ok)}</TableCell>
                    <TableCell className="text-right">{numero(f.cliente)}</TableCell>
                    <TableCell className={f.servidor > 0 ? "text-right font-medium text-peligro" : "text-right"}>{numero(f.servidor)}</TableCell>
                    <TableCell className="text-right">{porcentaje((f.servidor / f.total) * 100)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      {datos.token_entorno ? (
        <Aviso className="lg:col-span-2">
          RAILSPEC_METRICAS_TOKEN también está definido en el servidor y sigue abriendo /metrics. Cuando todos los orígenes usen una clave de aquí,
          puedes quitarlo del despliegue.
        </Aviso>
      ) : null}
    </div>
  );
}

function ClavesDeMetricas() {
  const lista = useQuery({ queryKey: claves.clavesMetricas, queryFn: operacion.claves });
  const [nombre, setNombre] = useState("");
  const [creada, setCreada] = useState<ClaveMetricasCreada | null>(null);
  const crear = useGuardar(operacion.crearClave, [claves.clavesMetricas], (r) => {
    setCreada(r);
    setNombre("");
  });
  const enviar = (e: FormEvent) => {
    e.preventDefault();
    if (nombre.trim()) crear.mutate(nombre.trim());
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle>Claves de /metrics</CardTitle>
        <CardDescription>
          Una por cada herramienta que consulte /metrics desde fuera (Prometheus, Grafana, verificar_metricas.py). Se envía como
          «Authorization: Bearer &lt;clave&gt;». Solo se muestra al crearla; revocarla la invalida al instante.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={enviar} className="mb-4 flex flex-wrap items-end gap-2">
          <Campo etiqueta="Origen" htmlFor="clave-metricas-nombre" ayuda="Quién la usará, por ejemplo «Grafana».">
            <Input id="clave-metricas-nombre" value={nombre} maxLength={60} onChange={(e) => setNombre(e.target.value)} />
          </Campo>
          <Button type="submit" disabled={!nombre.trim() || crear.isPending}>
            {crear.isPending ? "Creando…" : "Crear clave"}
          </Button>
        </form>
        <ErrorGuardado error={crear.error} />
        {creada ? <ClaveRecienCreada creada={creada} alCerrar={() => setCreada(null)} /> : null}
        {lista.isPending ? <Cargando /> : null}
        {lista.isError ? <ErrorVista error={lista.error} reintentar={() => void lista.refetch()} /> : null}
        {lista.isSuccess && lista.data.length === 0 ? (
          <p className="text-sm text-suave">No hay claves. Sin ellas ni RAILSPEC_METRICAS_TOKEN, /metrics responde 404.</p>
        ) : null}
        {lista.isSuccess && lista.data.length > 0 ? <TablaClaves claves={lista.data} /> : null}
      </CardContent>
    </Card>
  );
}

function ClaveRecienCreada({ creada, alCerrar }: { creada: ClaveMetricasCreada; alCerrar: () => void }) {
  const [copiado, setCopiado] = useState(false);
  const copiar = async () => {
    try {
      await navigator.clipboard.writeText(creada.secreto);
      setCopiado(true);
    } catch {
      setCopiado(false);
    }
  };
  return (
    <Aviso tono="aviso" className="mb-4">
      <p className="font-medium">Clave de «{creada.clave.nombre}»: cópiala ahora, no se vuelve a mostrar.</p>
      <code aria-label="Clave nueva" className="mt-2 block break-all rounded bg-superficie p-2 font-mono text-xs text-texto">
        {creada.secreto}
      </code>
      <div className="mt-2 flex gap-2">
        <Button variante="secundario" tamano="pequeno" onClick={() => void copiar()}>
          {copiado ? "Copiada" : "Copiar"}
        </Button>
        <Button variante="secundario" tamano="pequeno" onClick={alCerrar}>
          Ya la guardé
        </Button>
      </div>
    </Aviso>
  );
}

function TablaClaves({ claves: lista }: { claves: ClaveMetricas[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Origen</TableHead>
          <TableHead>Clave</TableHead>
          <TableHead>Creada</TableHead>
          <TableHead>Último uso</TableHead>
          <TableHead>Estado</TableHead>
          <TableHead>
            <span className="sr-only">Acciones</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {lista.map((k) => (
          <FilaClave key={k.id} clave={k} />
        ))}
      </TableBody>
    </Table>
  );
}

function FilaClave({ clave }: { clave: ClaveMetricas }) {
  const [confirmando, setConfirmando] = useState(false);
  const revocar = useGuardar(operacion.revocarClave, [claves.clavesMetricas], () => setConfirmando(false));
  return (
    <TableRow>
      <TableCell className="font-medium">{clave.nombre}</TableCell>
      <TableCell className="font-mono text-xs">{clave.prefijo}…</TableCell>
      <TableCell>
        {fecha(clave.creada_en)}
        <p className="text-xs text-suave">por {clave.creada_por}</p>
      </TableCell>
      <TableCell>{clave.ultimo_uso ? fecha(clave.ultimo_uso) : "Nunca"}</TableCell>
      <TableCell>
        {clave.activa ? (
          <Badge tono="exito">Activa</Badge>
        ) : (
          <Badge tono="neutro" title={`Revocada el ${fecha(clave.revocada_en)} por ${clave.revocada_por ?? "—"}`}>
            Revocada
          </Badge>
        )}
      </TableCell>
      <TableCell>
        {clave.activa ? (
          confirmando ? (
            <div className="flex gap-2">
              <Button variante="peligro" tamano="pequeno" disabled={revocar.isPending} onClick={() => revocar.mutate(clave.id)}>
                Confirmar revocación
              </Button>
              <Button variante="secundario" tamano="pequeno" onClick={() => setConfirmando(false)}>
                Cancelar
              </Button>
            </div>
          ) : (
            <Button variante="secundario" tamano="pequeno" onClick={() => setConfirmando(true)} aria-label={`Revocar ${clave.nombre}`}>
              Revocar
            </Button>
          )
        ) : null}
        {revocar.error ? <ErrorGuardado error={revocar.error} /> : null}
      </TableCell>
    </TableRow>
  );
}
