import { GATES, type EstadoUnidad, type Hallazgo, type Severidad } from "../../api/tipos";
import { Vacio } from "../../componentes/Estados";
import { EtiquetaSeveridad, EtiquetaVeredicto } from "../../componentes/Etiquetas";
import { Badge } from "../../componentes/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "../../componentes/ui/card";
import { fecha, nombreActor } from "../../lib/utiles";

const SEVERIDADES: Severidad[] = ["alta", "media", "baja"];

function Hallazgos({ hallazgos }: { hallazgos: Hallazgo[] }) {
  if (hallazgos.length === 0) return <p className="text-sm text-suave">Sin hallazgos sin resolver.</p>;
  return (
    <div className="flex flex-col gap-3">
      {SEVERIDADES.map((s) => {
        const deS = hallazgos.filter((h) => h.severidad === s);
        if (deS.length === 0) return null;
        return (
          <section key={s} aria-label={`Severidad ${s}`}>
            <h4 className="mb-1 flex items-center gap-2 text-sm font-semibold">
              <EtiquetaSeveridad severidad={s} /> {deS.length}
            </h4>
            <ul className="flex flex-col gap-2">
              {deS.map((h) => (
                <li key={h.id} className={h.refutado ? "opacity-60" : undefined}>
                  <p className="text-sm">
                    <span className="font-mono text-xs">{h.id}</span> {h.titulo}{" "}
                    {h.refutado ? <Badge>refutado</Badge> : null}
                    {h.criterio ? <Badge tono="info">{h.criterio}</Badge> : null}
                  </p>
                  <p className="text-xs text-suave">
                    Lente {h.lente}
                    {h.cita.seccion ? ` · sección ${h.cita.seccion}` : ""}
                    {h.cita.ruta ? ` · ${h.cita.ruta}${h.cita.linea_inicio ? `:${h.cita.linea_inicio}` : ""}` : ""}
                  </p>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

export function PestanaGates({ estado }: { estado: EstadoUnidad }) {
  const conResultado = GATES.filter((g) => estado.gates?.[g]);
  if (conResultado.length === 0) return <Vacio titulo="Ningún gate ha cerrado todavía" />;
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {GATES.map((g) => {
        const r = estado.gates?.[g];
        if (!r) return null;
        return (
          <Card key={g}>
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center gap-2">
                Gate {g === "codigo" ? "código" : g} <EtiquetaVeredicto veredicto={r.veredicto} />
                {r.rehabilitado ? <Badge tono="info">rehabilitado</Badge> : null}
                {r.diferido ? <Badge tono="aviso">Diferida (desatendido)</Badge> : null}
              </CardTitle>
              <p className="text-xs text-suave">
                {r.iteraciones} iteraciones · gobernanza {r.gobernanza_consultada} · cerrado {fecha(r.cerrado_en)}
                {r.causa ? ` · causa: ${r.causa}` : ""}
                {r.criticos?.length ? ` · panel: ${r.criticos.join(", ")}` : ""}
              </p>
              {r.rehabilitado ? (
                <p className="text-xs text-suave">
                  Rehabilitado por {nombreActor(r.rehabilitado.actor)}: {r.rehabilitado.motivo}
                </p>
              ) : null}
            </CardHeader>
            <CardContent>
              <Hallazgos hallazgos={r.hallazgos ?? []} />
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
