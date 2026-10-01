import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { claves, proveedoresContexto } from "../../api/endpoints";
import { FASES, ROLES_CONTEXTO, type Fase, type ProveedorContexto, type RolContexto } from "../../api/tipos";
import { Cargando, ErrorVista, Vacio } from "../../componentes/Estados";
import { Badge } from "../../componentes/ui/badge";
import { Button } from "../../componentes/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../componentes/ui/card";
import { Dialog } from "../../componentes/ui/dialog";
import { Campo, Input } from "../../componentes/ui/input";
import { opcionesDe, Select } from "../../componentes/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";
import { ErrorGuardado, numeroOpcional, useGuardar } from "../../lib/mutaciones";

/** Referencia a un secreto: nunca el valor. */
export const PATRON_CREDENCIAL = /^secret:\/\/[a-z0-9-]+\/[A-Za-z0-9_.-]+$/;

function FormularioProveedor({
  org,
  ws,
  actual,
  alCerrar,
}: {
  org: string;
  ws?: string;
  actual?: ProveedorContexto;
  alCerrar: () => void;
}) {
  const [rol, setRol] = useState<RolContexto>(actual?.rol ?? "gobernanza");
  const [nombre, setNombre] = useState(actual?.nombre ?? "");
  const [url, setUrl] = useState(actual?.url ?? "https://");
  const [credencial, setCredencial] = useState(actual?.credencial_ref ?? "");
  const [politica, setPolitica] = useState<"estricta" | "blanda">(actual?.politica_fallo ?? "blanda");
  const [fases, setFases] = useState<Fase[]>(actual?.fases ?? []);
  const [presupuesto, setPresupuesto] = useState(actual?.presupuesto_tokens?.toString() ?? "");
  const credencialValida = !credencial.trim() || PATRON_CREDENCIAL.test(credencial.trim());

  const guardar = useGuardar(
    () =>
      proveedoresContexto.guardar(
        org,
        rol,
        nombre.trim(),
        {
          url: url.trim(),
          credencial_ref: credencial.trim() || null,
          politica_fallo: politica,
          fases,
          presupuesto_tokens: numeroOpcional(presupuesto),
          ...(actual ? { version: actual.version } : {}),
        },
        ws,
      ),
    [["proveedores-contexto", org]],
    alCerrar,
  );
  const enviar = (e: FormEvent) => {
    e.preventDefault();
    if (credencialValida) guardar.mutate(undefined);
  };
  return (
    <Dialog abierto alCerrar={alCerrar} titulo={actual ? `Editar ${actual.nombre}` : "Nuevo proveedor de contexto"}>
      <form onSubmit={enviar} className="flex flex-col gap-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Campo etiqueta="Rol" htmlFor="prov-rol">
            <Select id="prov-rol" disabled={Boolean(actual)} value={rol} onChange={(e) => setRol(e.target.value as RolContexto)} opciones={opcionesDe(ROLES_CONTEXTO)} />
          </Campo>
          <Campo etiqueta="Nombre" htmlFor="prov-nombre">
            <Input id="prov-nombre" required disabled={Boolean(actual)} maxLength={80} value={nombre} onChange={(e) => setNombre(e.target.value)} />
          </Campo>
        </div>
        <Campo etiqueta="URL" htmlFor="prov-url">
          <Input id="prov-url" type="url" required pattern="^https://.*" maxLength={512} value={url} onChange={(e) => setUrl(e.target.value)} />
        </Campo>
        <Campo
          etiqueta="Credencial (referencia)"
          htmlFor="prov-cred"
          ayuda="Solo la referencia al secreto, con forma secret://secreto/clave. Nunca pegues el valor."
        >
          <Input
            id="prov-cred"
            autoComplete="off"
            placeholder="secret://mi-secreto/clave"
            aria-invalid={!credencialValida}
            value={credencial}
            onChange={(e) => setCredencial(e.target.value)}
          />
        </Campo>
        {!credencialValida ? <p className="text-sm text-peligro">Debe ser una referencia secret://secreto/clave.</p> : null}
        <div className="grid gap-3 sm:grid-cols-2">
          <Campo etiqueta="Política ante fallo" htmlFor="prov-politica">
            <Select
              id="prov-politica"
              value={politica}
              onChange={(e) => setPolitica(e.target.value as "estricta" | "blanda")}
              opciones={opcionesDe(["blanda", "estricta"])}
            />
          </Campo>
          <Campo etiqueta="Presupuesto de tokens (opcional)" htmlFor="prov-pres">
            <Input id="prov-pres" type="number" min={1} value={presupuesto} onChange={(e) => setPresupuesto(e.target.value)} />
          </Campo>
        </div>
        <fieldset>
          <legend className="mb-1 text-sm font-medium">Fases (ninguna = todas)</legend>
          <div className="flex flex-wrap gap-3">
            {FASES.map((f) => (
              <label key={f} className="flex items-center gap-1 text-sm">
                <input
                  type="checkbox"
                  checked={fases.includes(f)}
                  onChange={(e) => setFases((xs) => (e.target.checked ? [...xs, f] : xs.filter((x) => x !== f)))}
                />
                {f}
              </label>
            ))}
          </div>
        </fieldset>
        <ErrorGuardado error={guardar.error} />
        <div className="flex justify-end gap-2">
          <Button variante="secundario" onClick={alCerrar}>
            Cancelar
          </Button>
          <Button type="submit" disabled={guardar.isPending || !credencialValida}>
            {guardar.isPending ? "Guardando…" : "Guardar"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

export function Proveedores({ org, ws, editable }: { org: string; ws?: string; editable: boolean }) {
  const lista = useQuery({ queryKey: claves.proveedores(org, ws), queryFn: () => proveedoresContexto.listar(org, ws) });
  const [editando, setEditando] = useState<ProveedorContexto | "nuevo" | null>(null);
  const [borrando, setBorrando] = useState<ProveedorContexto | null>(null);
  const borrar = useGuardar(
    (p: ProveedorContexto) => proveedoresContexto.borrar(org, p.rol, p.nombre, p.workspace ?? undefined),
    [["proveedores-contexto", org]],
    () => setBorrando(null),
  );
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between">
        <div>
          <CardTitle>Proveedores de contexto</CardTitle>
          <CardDescription>Gobernanza, grafo de código, memoria y documentación por rol.</CardDescription>
        </div>
        {editable ? (
          <Button tamano="pequeno" onClick={() => setEditando("nuevo")}>
            Nuevo proveedor
          </Button>
        ) : null}
      </CardHeader>
      <CardContent>
        {lista.isPending ? <Cargando /> : null}
        {lista.isError ? <ErrorVista error={lista.error} reintentar={() => void lista.refetch()} /> : null}
        {lista.isSuccess && lista.data.length === 0 ? <Vacio titulo="Sin proveedores de contexto" /> : null}
        {lista.isSuccess && lista.data.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Rol</TableHead>
                <TableHead>Nombre</TableHead>
                <TableHead>URL</TableHead>
                <TableHead>Credencial</TableHead>
                <TableHead>Fallo</TableHead>
                <TableHead>Fases</TableHead>
                <TableHead>Ámbito</TableHead>
                <TableHead>
                  <span className="sr-only">Acciones</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {lista.data.map((p) => {
                const propio = (p.workspace ?? undefined) === ws;
                return (
                  <TableRow key={`${p.workspace ?? ""}/${p.rol}/${p.nombre}`}>
                    <TableCell>{p.rol}</TableCell>
                    <TableCell className="font-medium">{p.nombre}</TableCell>
                    <TableCell className="max-w-56 truncate text-xs">{p.url}</TableCell>
                    <TableCell className="font-mono text-xs">{p.credencial_ref ?? "—"}</TableCell>
                    <TableCell>
                      <Badge tono={p.politica_fallo === "estricta" ? "peligro" : "neutro"}>{p.politica_fallo}</Badge>
                    </TableCell>
                    <TableCell className="text-xs">{p.fases?.length ? p.fases.join(", ") : "todas"}</TableCell>
                    <TableCell>{p.workspace ? `workspace ${p.workspace}` : "organización"}</TableCell>
                    <TableCell>
                      {editable && propio ? (
                        <div className="flex gap-1">
                          <Button variante="secundario" tamano="pequeno" onClick={() => setEditando(p)}>
                            Editar
                          </Button>
                          <Button variante="fantasma" tamano="pequeno" onClick={() => setBorrando(p)}>
                            Borrar
                          </Button>
                        </div>
                      ) : null}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        ) : null}
      </CardContent>
      {editando ? (
        <FormularioProveedor
          org={org}
          {...(ws ? { ws } : {})}
          {...(editando === "nuevo" ? {} : { actual: editando })}
          alCerrar={() => setEditando(null)}
        />
      ) : null}
      <Dialog
        abierto={borrando !== null}
        alCerrar={() => setBorrando(null)}
        titulo="Borrar proveedor"
        descripcion={borrando ? `¿Borrar ${borrando.rol}/${borrando.nombre}?` : undefined}
        pie={
          <>
            <Button variante="secundario" onClick={() => setBorrando(null)}>
              Cancelar
            </Button>
            <Button variante="peligro" disabled={borrar.isPending} onClick={() => borrando && borrar.mutate(borrando)}>
              Borrar
            </Button>
          </>
        }
      >
        <ErrorGuardado error={borrar.error} />
      </Dialog>
    </Card>
  );
}
