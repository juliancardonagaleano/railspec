import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { claves, perfiles } from "../../api/endpoints";
import { PERFILES, type EscrituraPerfil, type Perfil, type PerfilConfig } from "../../api/tipos";
import { Cargando, ErrorVista, Vacio } from "../../componentes/Estados";
import { Badge } from "../../componentes/ui/badge";
import { Button } from "../../componentes/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../componentes/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../componentes/ui/tabs";
import { fecha } from "../../lib/utiles";
import { EditorPerfil } from "./EditorPerfil";

const PLANTILLA: EscrituraPerfil = {
  roles: { redactor: { modelo: {}, structured_outputs: false } },
  gate: {
    bajo: { criticos: 1, iteraciones: 1, adversarial: false },
    medio: { criticos: 2, iteraciones: 2, adversarial: false },
    alto: { criticos: 3, iteraciones: 3, adversarial: true },
  },
  exploradores: { bajo: 0, medio: 1, alto: 2 },
};

const aEscritura = (p: PerfilConfig, conVersion: boolean): EscrituraPerfil => ({
  roles: p.roles,
  gate: p.gate,
  exploradores: p.exploradores,
  ...(p.suscripcion ? { suscripcion: p.suscripcion } : {}),
  ...(conVersion ? { version: p.version } : {}),
});

function PanelPerfil({
  org,
  ws,
  nombre,
  propio,
  heredado,
  editable,
}: {
  org: string;
  ws?: string;
  nombre: Perfil;
  propio: PerfilConfig | undefined;
  heredado: PerfilConfig | undefined;
  editable: boolean;
}) {
  const [creando, setCreando] = useState(false);
  if (propio)
    return (
      <div className="flex flex-col gap-2">
        <p className="text-xs text-suave">
          Versión {propio.version} · actualizado {fecha(propio.auditoria.actualizado_en)}
        </p>
        <EditorPerfil key={propio.version} org={org} {...(ws ? { ws } : {})} nombre={nombre} inicial={aEscritura(propio, true)} editable={editable} />
      </div>
    );
  if (creando)
    return (
      <EditorPerfil
        org={org}
        {...(ws ? { ws } : {})}
        nombre={nombre}
        inicial={heredado ? aEscritura(heredado, false) : PLANTILLA}
        editable={editable}
      />
    );
  return (
    <Vacio titulo={ws ? `El workspace usa el perfil «${nombre}» de la organización` : `Sin perfil «${nombre}» en la organización`}>
      {editable ? (
        <Button className="mt-2" variante="secundario" tamano="pequeno" onClick={() => setCreando(true)}>
          {ws ? "Crear ajuste propio del workspace" : "Crear perfil"}
        </Button>
      ) : null}
    </Vacio>
  );
}

/** Perfiles ligero/estandar/profundo de la org (sin `ws`) o los propios del workspace. */
export function Perfiles({ org, ws, editable }: { org: string; ws?: string; editable: boolean }) {
  const lista = useQuery({ queryKey: claves.perfiles(org, ws), queryFn: () => perfiles.listar(org, ws) });
  const [pestana, setPestana] = useState<string>("estandar");
  return (
    <Card>
      <CardHeader>
        <CardTitle>Perfiles de esfuerzo</CardTitle>
        <CardDescription>
          Modelo por proveedor y effort por rol, presupuesto de gate por riesgo y exploradores.
          {ws ? " Lo que no se ajuste aquí se hereda de la organización." : ""}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {lista.isPending ? <Cargando /> : null}
        {lista.isError ? <ErrorVista error={lista.error} reintentar={() => void lista.refetch()} /> : null}
        {lista.isSuccess ? (
          <Tabs valor={pestana} alCambiar={setPestana}>
            <TabsList etiqueta="Perfiles">
              {PERFILES.map((p) => {
                const propio = lista.data.find((x) => x.nombre === p && (x.workspace ?? undefined) === ws);
                return (
                  <TabsTrigger key={p} valor={p}>
                    {p} {ws && propio ? <Badge tono="violeta">propio</Badge> : null}
                  </TabsTrigger>
                );
              })}
            </TabsList>
            {PERFILES.map((p) => (
              <TabsContent key={p} valor={p}>
                <PanelPerfil
                  org={org}
                  {...(ws ? { ws } : {})}
                  nombre={p}
                  propio={lista.data.find((x) => x.nombre === p && (x.workspace ?? undefined) === ws)}
                  heredado={lista.data.find((x) => x.nombre === p && x.workspace === null)}
                  editable={editable}
                />
              </TabsContent>
            ))}
          </Tabs>
        ) : null}
      </CardContent>
    </Card>
  );
}
