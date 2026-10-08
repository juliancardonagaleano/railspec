import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ErrorApi } from "../../api/cliente";
import { archivosRepo, claves } from "../../api/endpoints";
import type { ArchivoRepo, LecturaArchivoRepo, PropuestaArchivo, ValidacionArchivo } from "../../api/tipos";
import { Aviso, Cargando, ErrorVista } from "../../componentes/Estados";
import { Button } from "../../componentes/ui/button";
import { Dialog } from "../../componentes/ui/dialog";
import { Campo, Input, Textarea } from "../../componentes/ui/input";
import { Select } from "../../componentes/ui/select";

const ARCHIVOS = [
  { valor: "contexto", etiqueta: "contexto.yaml" },
  { valor: "ignore", etiqueta: ".railspecignore" },
];

function Hallazgos({ validacion }: { validacion: ValidacionArchivo }) {
  const linea = (l: number | null) => (l ? `línea ${l}: ` : "");
  return (
    <div className="flex flex-col gap-2">
      {validacion.ok ? <Aviso tono="exito">El archivo es válido.</Aviso> : null}
      {validacion.errores.length > 0 ? (
        <div role="alert" className="rounded-lg border border-rose-300 bg-rose-50 p-3 text-sm text-rose-900 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-100">
          <p className="font-medium">Errores</p>
          <ul className="list-inside list-disc">
            {validacion.errores.map((h, i) => (
              <li key={i}>
                {linea(h.linea)}
                {h.mensaje}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {validacion.avisos.length > 0 ? (
        <Aviso tono="aviso">
          <p className="font-medium">Avisos</p>
          <ul className="list-inside list-disc">
            {validacion.avisos.map((h, i) => (
              <li key={i}>
                {linea(h.linea)}
                {h.mensaje}
              </li>
            ))}
          </ul>
        </Aviso>
      ) : null}
    </div>
  );
}

function Resultado({ propuesta }: { propuesta: PropuestaArchivo }) {
  const [copiado, setCopiado] = useState(false);
  if (propuesta.modo === "pr") {
    return (
      <Aviso tono="exito">
        <p>
          Se abrió el PR{" "}
          <a href={propuesta.pr_url} target="_blank" rel="noreferrer noopener" className="font-medium underline">
            #{propuesta.numero}
          </a>{" "}
          en la rama <span className="font-mono text-xs">{propuesta.rama}</span>. Nada cambia en el repositorio hasta que se fusione.
        </p>
      </Aviso>
    );
  }
  const copiar = async () => {
    try {
      await navigator.clipboard.writeText(propuesta.contenido);
      setCopiado(true);
    } catch {
      setCopiado(false);
    }
  };
  return (
    <Aviso tono="aviso">
      <p className="font-medium">No se abrió ningún PR: aplica el cambio a mano.</p>
      <p className="mt-1">{propuesta.motivo_manual}</p>
      <p className="mt-1">
        Para que la consola lo proponga sola, la GitHub App necesita permisos de <b>contenido: escritura</b> y <b>pull requests: escritura</b> en
        este repositorio.
      </p>
      {propuesta.diff ? (
        <pre aria-label="Diff" className="mt-2 max-h-60 overflow-auto rounded bg-superficie p-2 font-mono text-xs text-texto">
          {propuesta.diff}
        </pre>
      ) : null}
      <Button className="mt-2" variante="secundario" tamano="pequeno" onClick={() => void copiar()}>
        {copiado ? "Copiado" : `Copiar ${propuesta.ruta}`}
      </Button>
    </Aviso>
  );
}

function Editor({ org, ws, repo, archivo, lectura, recargar }: { org: string; ws: string; repo: string; archivo: ArchivoRepo; lectura: LecturaArchivoRepo; recargar: () => void }) {
  const inicial = lectura.existe ? lectura.contenido : lectura.plantilla;
  const [texto, setTexto] = useState(inicial);
  const [motivo, setMotivo] = useState("");
  const [validacion, setValidacion] = useState<ValidacionArchivo | null>(null);
  const [propuesta, setPropuesta] = useState<PropuestaArchivo | null>(null);

  const alEditar = (v: string) => {
    setTexto(v);
    setValidacion(null);
    setPropuesta(null);
  };
  const validar = useMutation({
    mutationFn: () => archivosRepo.validar(org, ws, repo, archivo, texto),
    onSuccess: setValidacion,
  });
  const proponer = useMutation({
    mutationFn: () =>
      archivosRepo.proponer(org, ws, repo, archivo, { contenido: texto, sha_base: lectura.sha, ...(motivo.trim() ? { motivo: motivo.trim() } : {}) }),
    onMutate: () => setPropuesta(null),
    onSuccess: setPropuesta,
  });
  const conflicto = proponer.error instanceof ErrorApi && proponer.error.status === 409 ? proponer.error : null;
  const cambio = texto !== inicial;

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (cambio) proponer.mutate();
      }}
    >
      {lectura.modo === "manual" ? (
        <Aviso tono="aviso">
          <p className="font-medium">Edición manual.</p>
          <p className="mt-1">
            {lectura.motivo_manual} La consola valida y te da el diff, pero no puede abrir el PR: para eso la GitHub App necesita{" "}
            <b>contenido: escritura</b> y <b>pull requests: escritura</b>.
          </p>
        </Aviso>
      ) : (
        <Aviso tono="info">
          Los cambios se proponen como un PR contra <span className="font-mono text-xs">{lectura.rama}</span> de {lectura.repositorio}; la
          consola nunca hace commit directo.
        </Aviso>
      )}
      {lectura.existe === false ? <Aviso tono="info">{lectura.ruta} aún no existe en la rama: se propone crearlo a partir de la plantilla.</Aviso> : null}
      <Campo etiqueta={lectura.ruta} htmlFor="archivo-texto">
        <Textarea
          id="archivo-texto"
          spellCheck={false}
          className="min-h-64 font-mono text-xs"
          value={texto}
          onChange={(e) => alEditar(e.target.value)}
        />
      </Campo>
      <Campo etiqueta="Motivo (opcional)" htmlFor="archivo-motivo" ayuda="Va en el PR y en la auditoría.">
        <Input id="archivo-motivo" maxLength={500} value={motivo} onChange={(e) => setMotivo(e.target.value)} />
      </Campo>
      {validacion ? <Hallazgos validacion={validacion} /> : null}
      {validar.isError ? <ErrorVista error={validar.error} /> : null}
      {conflicto ? (
        <Aviso tono="aviso">
          <p>{conflicto.detalle}.</p>
          <Button className="mt-2" variante="secundario" tamano="pequeno" onClick={recargar}>
            Cargar la versión de la rama (descarta tus cambios)
          </Button>
        </Aviso>
      ) : proponer.isError ? (
        <ErrorVista error={proponer.error} />
      ) : null}
      {propuesta ? <Resultado propuesta={propuesta} /> : null}
      <div className="flex justify-end gap-2">
        <Button variante="secundario" disabled={validar.isPending} onClick={() => validar.mutate()}>
          Validar
        </Button>
        <Button type="submit" disabled={!cambio || proponer.isPending}>
          {proponer.isPending ? "Proponiendo…" : lectura.modo === "manual" ? "Ver diff" : "Proponer cambio (abre un PR)"}
        </Button>
      </div>
    </form>
  );
}

/** Edita `contexto.yaml` y `.railspecignore` de un repositorio vinculado; el cambio llega como PR, no como commit. */
export function ArchivosRepositorio({ org, ws, repo, alCerrar }: { org: string; ws: string; repo: string; alCerrar: () => void }) {
  const [archivo, setArchivo] = useState<ArchivoRepo>("contexto");
  const consulta = useQuery({
    queryKey: claves.archivoRepo(org, ws, repo, archivo),
    queryFn: () => archivosRepo.leer(org, ws, repo, archivo),
    // El texto que se está editando no puede cambiar bajo los pies de quien teclea: si el archivo cambió en la
    // rama, el 409 al proponer ofrece recargar (y el editor se remonta con el hash nuevo).
    staleTime: Infinity,
    gcTime: 0,
  });
  return (
    <Dialog
      abierto
      alCerrar={alCerrar}
      titulo={`Archivos de ${repo}`}
      descripcion="Archivos de configuración que viven en el repositorio. Aquí se editan y validan; el cambio se propone como PR."
      className="max-w-3xl"
    >
      <div className="flex flex-col gap-3">
        <Campo etiqueta="Archivo" htmlFor="archivo-nombre">
          <Select id="archivo-nombre" value={archivo} onChange={(e) => setArchivo(e.target.value as ArchivoRepo)} opciones={ARCHIVOS} />
        </Campo>
        {consulta.isPending ? <Cargando /> : null}
        {consulta.isError ? <ErrorVista error={consulta.error} reintentar={() => void consulta.refetch()} /> : null}
        {consulta.isSuccess ? (
          <Editor
            key={`${archivo}:${consulta.data.sha ?? "nuevo"}`}
            org={org}
            ws={ws}
            repo={repo}
            archivo={archivo}
            lectura={consulta.data}
            recargar={() => void consulta.refetch()}
          />
        ) : null}
      </div>
    </Dialog>
  );
}
