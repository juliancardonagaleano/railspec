import { useState, type FormEvent } from "react";
import { claves, workspaces } from "../../api/endpoints";
import { PERFILES, type Perfil, type Workspace } from "../../api/tipos";
import { Button } from "../../componentes/ui/button";
import { Dialog } from "../../componentes/ui/dialog";
import { Campo, Input } from "../../componentes/ui/input";
import { opcionesDe, Select } from "../../componentes/ui/select";
import { ErrorGuardado, useGuardar } from "../../lib/mutaciones";

/** Crea (sin `ws`) o edita (con `ws`) un workspace de `org`. */
export function FormularioWorkspace({
  org,
  ws,
  abierto,
  alCerrar,
  claveAviso,
}: {
  org: string;
  ws?: Workspace;
  abierto: boolean;
  alCerrar: () => void;
  /** Dónde queda el «Guardado»: el diálogo se cierra al guardar y lo muestra quien lo abrió. */
  claveAviso: string;
}) {
  const [id, setId] = useState(ws?.alcance.workspace ?? "");
  const [nombre, setNombre] = useState(ws?.nombre ?? "");
  const [zona, setZona] = useState(ws?.zona_datos_azure ?? "");
  const [perfil, setPerfil] = useState<Perfil>(ws?.perfil_por_defecto ?? "estandar");
  const guardar = useGuardar(
    () => {
      const comun = { nombre: nombre.trim(), zona_datos_azure: zona.trim() || null, perfil_por_defecto: perfil };
      return ws
        ? workspaces.editar(org, ws.alcance.workspace, { ...comun, version: ws.version })
        : workspaces.crear(org, { workspace: id.trim(), ...comun });
    },
    [claves.workspaces(org), claves.yo],
    alCerrar,
    { clave: claveAviso },
  );
  const enviar = (e: FormEvent) => {
    e.preventDefault();
    guardar.mutate(undefined);
  };
  return (
    <Dialog abierto={abierto} alCerrar={alCerrar} titulo={ws ? `Editar ${ws.nombre}` : "Nuevo workspace"}>
      <form onSubmit={enviar} className="flex flex-col gap-3">
        {!ws ? (
          <Campo etiqueta="Identificador" htmlFor="ws-id" ayuda="Minúsculas, dígitos y guiones. No se puede cambiar.">
            <Input id="ws-id" required pattern="^[a-z0-9][a-z0-9\-]{0,62}$" value={id} onChange={(e) => setId(e.target.value)} />
          </Campo>
        ) : null}
        <Campo etiqueta="Nombre" htmlFor="ws-nombre">
          <Input id="ws-nombre" required maxLength={200} value={nombre} onChange={(e) => setNombre(e.target.value)} />
        </Campo>
        <Campo etiqueta="Zona de datos Azure (opcional)" htmlFor="ws-zona" ayuda="Dato informativo: no restringe qué proveedor o modelo se usa.">
          <Input id="ws-zona" maxLength={40} value={zona} onChange={(e) => setZona(e.target.value)} />
        </Campo>
        <Campo etiqueta="Perfil por defecto" htmlFor="ws-perfil">
          <Select id="ws-perfil" value={perfil} onChange={(e) => setPerfil(e.target.value as Perfil)} opciones={opcionesDe(PERFILES)} />
        </Campo>
        <ErrorGuardado error={guardar.error} />
        <div className="flex justify-end gap-2">
          <Button variante="secundario" onClick={alCerrar}>
            Cancelar
          </Button>
          <Button type="submit" disabled={guardar.isPending}>
            {guardar.isPending ? "Guardando…" : "Guardar"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
