import { useState } from "react";
import { Encabezado } from "../../componentes/Estados";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../componentes/ui/tabs";
import { alcanza } from "../../lib/roles";
import { useOrg, useRol, useWorkspace } from "../../lib/sesion";
import { Avisos } from "./Avisos";
import { Catalogo } from "./Catalogo";
import { Perfiles } from "./Perfiles";
import { Presupuestos } from "./Presupuestos";
import { Proveedores } from "./Proveedores";
import { Suscripciones } from "./Suscripciones";

function PestanasConfig({ org, ws, editable, orgAdmin }: { org: string; ws?: string; editable: boolean; orgAdmin: boolean }) {
  const [pestana, setPestana] = useState("perfiles");
  const conWs = ws ? { ws } : {};
  return (
    <Tabs valor={pestana} alCambiar={setPestana}>
      <TabsList etiqueta="Configuración">
        <TabsTrigger valor="perfiles">Perfiles</TabsTrigger>
        <TabsTrigger valor="presupuestos">Presupuestos</TabsTrigger>
        <TabsTrigger valor="proveedores">Proveedores de contexto</TabsTrigger>
        <TabsTrigger valor="suscripciones">Suscripciones</TabsTrigger>
        <TabsTrigger valor="catalogo">Catálogo de modelos</TabsTrigger>
        {orgAdmin ? <TabsTrigger valor="avisos">Avisos e informes</TabsTrigger> : null}
      </TabsList>
      <TabsContent valor="perfiles">
        <Perfiles org={org} {...conWs} editable={editable} />
      </TabsContent>
      <TabsContent valor="presupuestos">
        <Presupuestos org={org} {...conWs} editable={editable} />
      </TabsContent>
      <TabsContent valor="proveedores">
        <Proveedores org={org} {...conWs} editable={editable} />
      </TabsContent>
      <TabsContent valor="suscripciones">
        <Suscripciones org={org} editable={orgAdmin} />
      </TabsContent>
      <TabsContent valor="catalogo">
        <Catalogo org={org} puedeSincronizar={orgAdmin} />
      </TabsContent>
      {orgAdmin ? (
        <TabsContent valor="avisos">
          <Avisos org={org} />
        </TabsContent>
      ) : null}
    </Tabs>
  );
}

export function ConfiguracionOrg() {
  const org = useOrg();
  const { rol } = useRol();
  const admin = alcanza(rol, "org-admin");
  return (
    <>
      <Encabezado
        titulo="Configuración de la organización"
        descripcion={admin ? "Valores por defecto para todos los workspaces." : "Solo lectura: necesitas ser admin. de organización para cambiarla."}
      />
      <PestanasConfig org={org} editable={admin} orgAdmin={admin} />
    </>
  );
}

export function ConfiguracionWorkspace() {
  const { org, ws } = useWorkspace();
  const { rol } = useRol();
  return (
    <>
      <Encabezado titulo="Configuración del workspace" descripcion={`Ajustes propios de ${ws}; lo demás se hereda de ${org}.`} />
      <PestanasConfig org={org} ws={ws} editable={alcanza(rol, "workspace-admin")} orgAdmin={alcanza(rol, "org-admin")} />
    </>
  );
}
