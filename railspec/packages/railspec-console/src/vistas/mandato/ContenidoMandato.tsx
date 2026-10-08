import type { Consumo, MandatoContenido, Presupuesto } from "../../api/tipos";
import { EtiquetaModo } from "../../componentes/Etiquetas";
import { Badge } from "../../componentes/ui/badge";
import { numero, usd } from "../../lib/utiles";
import { TEXTO_TIPO_DELEGACION } from "./mandato";

const TONO_TIPO = { "pre-decidida": "info", "con-criterio": "violeta", reservada: "peligro" } as const;

interface FilaTope {
  etiqueta: string;
  tope: string;
  consumido?: string;
}

/** Topes del presupuesto total que tienen valor, con lo consumido si se conoce. */
export function filasPresupuesto(p: Presupuesto, consumo?: Consumo): FilaTope[] {
  const filas: FilaTope[] = [];
  if (p.tokens_max != null) filas.push({ etiqueta: "Tokens", tope: numero(p.tokens_max), ...(consumo ? { consumido: numero(consumo.tokens ?? 0) } : {}) });
  if (p.costo_usd_max != null) filas.push({ etiqueta: "Costo", tope: usd(p.costo_usd_max), ...(consumo ? { consumido: usd(consumo.costo_usd ?? 0) } : {}) });
  if (p.segundos_max != null) filas.push({ etiqueta: "Segundos", tope: numero(p.segundos_max), ...(consumo ? { consumido: numero(consumo.segundos ?? 0) } : {}) });
  if (p.llamadas_max != null) filas.push({ etiqueta: "Llamadas al modelo", tope: numero(p.llamadas_max), ...(consumo ? { consumido: numero(consumo.llamadas ?? 0) } : {}) });
  return filas;
}

/**
 * El contenido íntegro de un mandato: lo que una persona aprueba. Lo usan el detalle y el diálogo de
 * aprobación, de modo que lo que se ve al aprobar es lo mismo que se ve al leer.
 */
export function ContenidoMandato({ contenido, consumo }: { contenido: MandatoContenido; consumo?: Consumo }) {
  const l = contenido.limites;
  const topes = filasPresupuesto(l.presupuesto, consumo);
  return (
    <div className="flex flex-col gap-4 text-sm">
      <section aria-label="Objetivo">
        <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-suave">Objetivo</h4>
        <p className="font-medium">{contenido.titulo}</p>
        <p className="mt-1 whitespace-pre-wrap">{contenido.objetivo}</p>
        <p className="mt-2">
          Modo de las unidades: <EtiquetaModo modo={contenido.modo} />
        </p>
      </section>

      <section aria-label="Límites">
        <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-suave">Límites</h4>
        <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-[max-content_1fr]">
          <dt className="text-suave">Repositorios</dt>
          <dd className="font-mono text-xs">{l.repositorios.join(", ")}</dd>
          <dt className="text-suave">Unidades</dt>
          <dd>hasta {l.max_unidades}</dd>
          <dt className="text-suave">Rutas permitidas</dt>
          <dd>
            {l.rutas_permitidas.length === 0 ? (
              <span className="text-suave">sin restricción adicional a la del plan de cada unidad</span>
            ) : (
              <ul className="font-mono text-xs">
                {l.rutas_permitidas.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            )}
          </dd>
          <dt className="text-suave">Presupuesto total</dt>
          <dd>
            {topes.length === 0 ? (
              <span className="text-suave">sin tope</span>
            ) : (
              <ul>
                {topes.map((t) => (
                  <li key={t.etiqueta}>
                    {t.etiqueta}: {t.consumido !== undefined ? `${t.consumido} de ${t.tope}` : `máx. ${t.tope}`}
                  </li>
                ))}
              </ul>
            )}
          </dd>
          <dt className="text-suave">Reintentos de parada</dt>
          <dd>{l.reintentos_parada === 0 ? "ninguno: una parada la ve siempre una persona" : `${l.reintentos_parada} por orden fallida`}</dd>
          <dt className="text-suave">Vigencia de cada aprobación</dt>
          <dd>{l.vigencia_horas} h</dd>
        </dl>
      </section>

      <section aria-label="Delegaciones">
        <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-suave">Delegaciones</h4>
        {contenido.delegaciones.length === 0 ? (
          <p className="text-suave">Ninguna: toda decisión que no esté en el plan la toma una persona.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {contenido.delegaciones.map((d) => (
              <li key={d.id} className="rounded-md border border-borde p-2">
                <p>
                  <span className="font-mono text-xs">{d.id}</span>{" "}
                  <Badge tono={TONO_TIPO[d.tipo]} title={TEXTO_TIPO_DELEGACION[d.tipo]}>
                    {d.tipo}
                  </Badge>
                </p>
                <p className="mt-1 whitespace-pre-wrap">{d.texto}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
