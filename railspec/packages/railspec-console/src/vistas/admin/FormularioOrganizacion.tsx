import { useState, type FormEvent } from "react";
import { claves, organizaciones } from "../../api/endpoints";
import type { Organizacion } from "../../api/tipos";
import { Button } from "../../componentes/ui/button";
import { Dialog } from "../../componentes/ui/dialog";
import { Campo, Input } from "../../componentes/ui/input";
import { ErrorGuardado, useGuardar } from "../../lib/mutaciones";

const PATRON_ID = "^[a-z0-9][a-z0-9\\-]{0,62}$";

/** Crea (sin `org`) o edita (con `org`) una organización. */
export function FormularioOrganizacion({
  org,
  abierto,
  alCerrar,
  claveAviso,
}: {
  org?: Organizacion;
  abierto: boolean;
  alCerrar: () => void;
  /** Dónde queda el «Guardado»: el diálogo se cierra al guardar y lo muestra quien lo abrió. */
  claveAviso: string;
}) {
  const [id, setId] = useState(org?.id ?? "");
  const [nombre, setNombre] = useState(org?.nombre ?? "");
  const [githubOrg, setGithubOrg] = useState(org?.github_org ?? "");
  const [region, setRegion] = useState(org?.region_datos ?? "");
  const guardar = useGuardar(
    () => {
      const comun = { nombre: nombre.trim(), github_org: githubOrg.trim() || null, region_datos: region.trim() };
      return org ? organizaciones.editar(org.id, { ...comun, version: org.version }) : organizaciones.crear({ id: id.trim(), ...comun });
    },
    [claves.organizaciones, claves.yo],
    alCerrar,
    { clave: claveAviso },
  );
  const enviar = (e: FormEvent) => {
    e.preventDefault();
    guardar.mutate(undefined);
  };
  return (
    <Dialog abierto={abierto} alCerrar={alCerrar} titulo={org ? `Editar ${org.nombre}` : "Nueva organización"}>
      <form onSubmit={enviar} className="flex flex-col gap-3">
        {!org ? (
          <Campo etiqueta="Identificador" htmlFor="org-id" ayuda="Minúsculas, dígitos y guiones. No se puede cambiar.">
            <Input id="org-id" required pattern={PATRON_ID} value={id} onChange={(e) => setId(e.target.value)} />
          </Campo>
        ) : null}
        <Campo etiqueta="Nombre" htmlFor="org-nombre">
          <Input id="org-nombre" required maxLength={200} value={nombre} onChange={(e) => setNombre(e.target.value)} />
        </Campo>
        <Campo etiqueta="Organización de GitHub (opcional)" htmlFor="org-gh">
          <Input id="org-gh" maxLength={39} value={githubOrg} onChange={(e) => setGithubOrg(e.target.value)} />
        </Campo>
        <Campo etiqueta="Región de datos" htmlFor="org-region" ayuda="P. ej. eu, us, co.">
          <Input id="org-region" required maxLength={40} value={region} onChange={(e) => setRegion(e.target.value)} />
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
