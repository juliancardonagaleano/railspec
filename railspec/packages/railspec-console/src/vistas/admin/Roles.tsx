import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { claves, roles, workspaces } from "../../api/endpoints";
import { ROLES, type AsignacionRol, type Rol, type SujetoRolNuevo } from "../../api/tipos";
import { Cargando, ErrorVista, Vacio } from "../../componentes/Estados";
import { Badge } from "../../componentes/ui/badge";
import { Button } from "../../componentes/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../componentes/ui/card";
import { Dialog } from "../../componentes/ui/dialog";
import { Campo, Input } from "../../componentes/ui/input";
import { Select } from "../../componentes/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";
import { AvisoGuardado, ErrorGuardado, useGuardado, useGuardar } from "../../lib/mutaciones";
import { ETIQUETA_ROL } from "../../lib/roles";

function textoSujeto(a: AsignacionRol): string {
  const s = a.sujeto;
  if (s.tipo === "usuario") return s.login ? `@${s.login}` : `usuario #${s.github_id}`;
  return `equipo ${s.github_org}/${s.equipo}`;
}

/** Dónde queda el aviso: el diálogo se cierra al asignar y la tarjeta lo anuncia. */
const claveAviso = (org: string, ws: string | undefined) => `roles:${org}:${ws ?? ""}`;

function FormularioAsignar({
  org,
  ws,
  abierto,
  alCerrar,
}: {
  org: string;
  ws?: string;
  abierto: boolean;
  alCerrar: () => void;
}) {
  const [tipo, setTipo] = useState<"usuario" | "equipo">("usuario");
  const [login, setLogin] = useState("");
  const [githubOrg, setGithubOrg] = useState("");
  const [equipo, setEquipo] = useState("");
  const [equipoId, setEquipoId] = useState("");
  const [rol, setRol] = useState<Rol>(ws ? "desarrollador" : "org-admin");
  const [workspace, setWorkspace] = useState(ws ?? "");
  const listaWs = useQuery({ queryKey: claves.workspaces(org), queryFn: () => workspaces.listar(org), enabled: !ws });

  const rolesPosibles = ws ? ROLES.filter((r) => r !== "org-admin") : ROLES;
  const necesitaWs = rol !== "org-admin";
  const asignar = useGuardar(
    () => {
      const sujeto: SujetoRolNuevo =
        tipo === "usuario"
          ? { tipo: "usuario", login: login.trim().replace(/^@/, "") }
          : { tipo: "equipo", github_org: githubOrg.trim(), equipo: equipo.trim(), equipo_id: Number(equipoId) };
      return roles.asignar(org, { workspace: necesitaWs ? workspace : null, rol, sujeto });
    },
    [["roles", org]],
    alCerrar,
    // La versión de una asignación no le dice nada a quien asigna.
    { clave: claveAviso(org, ws), version: null },
  );
  const enviar = (e: FormEvent) => {
    e.preventDefault();
    if (necesitaWs && !workspace) return;
    asignar.mutate(undefined);
  };
  return (
    <Dialog abierto={abierto} alCerrar={alCerrar} titulo="Asignar rol">
      <form onSubmit={enviar} className="flex flex-col gap-3">
        <fieldset className="flex gap-4">
          <legend className="mb-1 text-sm font-medium">Sujeto</legend>
          <label className="flex items-center gap-1 text-sm">
            <input type="radio" name="tipo-sujeto" checked={tipo === "usuario"} onChange={() => setTipo("usuario")} /> Usuario
          </label>
          <label className="flex items-center gap-1 text-sm">
            <input type="radio" name="tipo-sujeto" checked={tipo === "equipo"} onChange={() => setTipo("equipo")} /> Equipo de GitHub
          </label>
        </fieldset>
        {tipo === "usuario" ? (
          <Campo etiqueta="Login de GitHub" htmlFor="rol-login" ayuda="El servidor resuelve su id de GitHub.">
            <Input id="rol-login" required value={login} onChange={(e) => setLogin(e.target.value)} />
          </Campo>
        ) : (
          <div className="grid gap-3 sm:grid-cols-3">
            <Campo etiqueta="Organización GitHub" htmlFor="rol-ghorg">
              <Input id="rol-ghorg" required maxLength={39} value={githubOrg} onChange={(e) => setGithubOrg(e.target.value)} />
            </Campo>
            <Campo etiqueta="Equipo (slug)" htmlFor="rol-equipo">
              <Input id="rol-equipo" required maxLength={100} value={equipo} onChange={(e) => setEquipo(e.target.value)} />
            </Campo>
            <Campo etiqueta="Id del equipo" htmlFor="rol-equipo-id">
              <Input id="rol-equipo-id" required type="number" min={1} value={equipoId} onChange={(e) => setEquipoId(e.target.value)} />
            </Campo>
          </div>
        )}
        <Campo etiqueta="Rol" htmlFor="rol-rol">
          <Select
            id="rol-rol"
            value={rol}
            onChange={(e) => setRol(e.target.value as Rol)}
            opciones={rolesPosibles.map((r) => ({ valor: r, etiqueta: ETIQUETA_ROL[r] }))}
          />
        </Campo>
        {necesitaWs && !ws ? (
          <Campo etiqueta="Workspace" htmlFor="rol-ws">
            <Select
              id="rol-ws"
              required
              vacio="Elige…"
              value={workspace}
              onChange={(e) => setWorkspace(e.target.value)}
              opciones={(listaWs.data ?? []).map((w) => ({ valor: w.alcance.workspace, etiqueta: w.nombre }))}
            />
          </Campo>
        ) : null}
        <ErrorGuardado error={asignar.error} />
        <div className="flex justify-end gap-2">
          <Button variante="secundario" onClick={alCerrar}>
            Cancelar
          </Button>
          <Button type="submit" disabled={asignar.isPending}>
            {asignar.isPending ? "Asignando…" : "Asignar"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

/** Asignaciones de rol de la org (sin `ws`) o de un workspace. */
export function Roles({ org, ws, puedeEditar }: { org: string; ws?: string; puedeEditar: boolean }) {
  const lista = useQuery({ queryKey: claves.roles(org, ws), queryFn: () => roles.listar(org, ws) });
  const [asignando, setAsignando] = useState(false);
  const [quitando, setQuitando] = useState<AsignacionRol | null>(null);
  const quitar = useGuardar((a: AsignacionRol) => roles.quitar(org, a.id), [["roles", org]], () => setQuitando(null));
  const asignado = useGuardado(claveAviso(org, ws));

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between">
        <div>
          <CardTitle>Roles</CardTitle>
          <CardDescription>{ws ? `Asignaciones del workspace ${ws}.` : "Asignaciones de toda la organización."}</CardDescription>
        </div>
        {puedeEditar ? (
          <Button tamano="pequeno" onClick={() => setAsignando(true)}>
            Asignar rol
          </Button>
        ) : null}
      </CardHeader>
      <CardContent>
        <AvisoGuardado guardado={asignado} texto="Rol asignado" className="mb-3" />
        {lista.isPending ? <Cargando /> : null}
        {lista.isError ? <ErrorVista error={lista.error} reintentar={() => void lista.refetch()} /> : null}
        {lista.isSuccess && lista.data.length === 0 ? <Vacio titulo="Sin asignaciones" /> : null}
        {lista.isSuccess && lista.data.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Sujeto</TableHead>
                <TableHead>Rol</TableHead>
                <TableHead>Workspace</TableHead>
                <TableHead>
                  <span className="sr-only">Acciones</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {lista.data.map((a) => (
                <TableRow key={a.id}>
                  <TableCell>{textoSujeto(a)}</TableCell>
                  <TableCell>
                    <Badge tono={a.rol === "org-admin" ? "violeta" : "info"}>{ETIQUETA_ROL[a.rol]}</Badge>
                  </TableCell>
                  <TableCell>{a.workspace ?? "toda la org"}</TableCell>
                  <TableCell className="text-right">
                    {puedeEditar ? (
                      <Button variante="fantasma" tamano="pequeno" onClick={() => setQuitando(a)}>
                        Quitar
                      </Button>
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </CardContent>
      {asignando ? <FormularioAsignar org={org} {...(ws ? { ws } : {})} abierto alCerrar={() => setAsignando(false)} /> : null}
      <Dialog
        abierto={quitando !== null}
        alCerrar={() => setQuitando(null)}
        titulo="Quitar rol"
        descripcion={quitando ? `¿Quitar ${ETIQUETA_ROL[quitando.rol]} a ${textoSujeto(quitando)}?` : undefined}
        pie={
          <>
            <Button variante="secundario" onClick={() => setQuitando(null)}>
              Cancelar
            </Button>
            <Button variante="peligro" disabled={quitar.isPending} onClick={() => quitando && quitar.mutate(quitando)}>
              Quitar
            </Button>
          </>
        }
      >
        <ErrorGuardado error={quitar.error} />
      </Dialog>
    </Card>
  );
}
