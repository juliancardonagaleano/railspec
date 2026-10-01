import { useMemo } from "react";
import type { EventoSync, LineaDeTiempo as Datos, ResumenOrden } from "../../api/tipos";
import { Vacio } from "../../componentes/Estados";
import { Badge } from "../../componentes/ui/badge";
import { fecha, nombreActor } from "../../lib/utiles";

type Entrada = { clase: "evento"; en: string; evento: EventoSync } | { clase: "orden"; en: string; orden: ResumenOrden };

/** Mezcla eventos y órdenes en orden cronológico. */
export function mezclarLinea(datos: Datos): Entrada[] {
  const entradas: Entrada[] = [
    ...datos.eventos.map((evento): Entrada => ({ clase: "evento", en: evento.emitido_en, evento })),
    ...datos.ordenes.map((orden): Entrada => ({ clase: "orden", en: orden.emitida_en, orden })),
  ];
  return entradas.sort((a, b) => a.en.localeCompare(b.en));
}

function resumenCarga(e: EventoSync): string {
  const c = e.carga;
  const partes: string[] = [];
  for (const clave of ["fase", "estado", "gate", "veredicto", "repositorio", "commit", "canal", "especificacion_viva"]) {
    const v = c[clave];
    if (typeof v === "string" || typeof v === "number") partes.push(`${clave}: ${clave === "commit" ? String(v).slice(0, 7) : v}`);
  }
  return partes.join(" · ");
}

function ItemEvento({ e }: { e: EventoSync }) {
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tono="info">{e.carga.tipo}</Badge>
        <span className="text-xs text-suave">
          {e.direccion === "local-a-remoto" ? "local → remoto" : "remoto → local"} · #{e.secuencia}
        </span>
      </div>
      <p className="mt-1 text-sm">{resumenCarga(e) || "—"}</p>
      <p className="text-xs text-suave">
        {nombreActor(e.actor)} ({e.actor.tipo}) por {e.actor.canal}
      </p>
    </>
  );
}

function ItemOrden({ o }: { o: ResumenOrden }) {
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tono="violeta">orden {o.tipo}</Badge>
        <span className="text-xs text-suave">
          #{o.secuencia} · fase {o.fase} · {o.repositorio}
          {o.grupo ? ` · grupo ${o.grupo}` : ""}
        </span>
      </div>
      {o.tareas?.length ? (
        <ul className="mt-1 list-inside list-disc text-sm">
          {o.tareas.map((t) => (
            <li key={t.id}>
              <span className="font-mono text-xs">{t.id}</span> {t.descripcion}
            </li>
          ))}
        </ul>
      ) : null}
      {o.reporte ? (
        <p className="mt-1 text-xs text-suave">
          Reporte: {o.reporte.resultado} ({fecha(o.reporte.reportado_en)}) · {o.reporte.archivos.length} archivos ·{" "}
          {o.reporte.tareas_completadas.length} tareas completadas
        </p>
      ) : (
        <p className="mt-1 text-xs text-suave">Sin reporte todavía.</p>
      )}
      <p className="text-xs text-suave">Emitida por el servidor</p>
    </>
  );
}

export function LineaDeTiempo({ datos }: { datos: Datos }) {
  const entradas = useMemo(() => mezclarLinea(datos), [datos]);
  if (entradas.length === 0) return <Vacio titulo="Aún no hay eventos ni órdenes" />;
  return (
    <ol className="relative flex flex-col gap-3 border-l border-borde pl-4">
      {entradas.map((x) => (
        <li key={x.clase === "evento" ? x.evento.id : x.orden.id} className="relative">
          <span className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full bg-primario" aria-hidden="true" />
          <time className="text-xs text-suave" dateTime={x.en}>
            {fecha(x.en)}
          </time>
          {x.clase === "evento" ? <ItemEvento e={x.evento} /> : <ItemOrden o={x.orden} />}
        </li>
      ))}
    </ol>
  );
}
