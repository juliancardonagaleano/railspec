import { useMemo, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { catalogo, claves, perfiles, suscripciones } from "../../api/endpoints";
import {
  EFFORTS,
  PROVEEDORES,
  type Effort,
  type EscrituraPerfil,
  type Perfil,
  type Proveedor,
  type RequisitoRol,
  type Riesgo,
  type TopeGate,
} from "../../api/tipos";
import { Button } from "../../componentes/ui/button";
import { Input } from "../../componentes/ui/input";
import { Campo } from "../../componentes/ui/input";
import { Select } from "../../componentes/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";
import { AvisoGuardado, ErrorGuardado, numeroOpcional, useGuardar } from "../../lib/mutaciones";

const RIESGOS: Riesgo[] = ["bajo", "medio", "alto"];
const TOPE_BASE: TopeGate = { criticos: 1, iteraciones: 1, adversarial: false };

export interface PropsEditorPerfil {
  org: string;
  ws?: string;
  nombre: Perfil;
  inicial: EscrituraPerfil;
  editable: boolean;
  alGuardar?: () => void;
}

function FilaRol({
  nombre,
  req,
  editable,
  proveedorUnico,
  alCambiar,
  alQuitar,
}: {
  nombre: string;
  req: RequisitoRol;
  editable: boolean;
  /** Con suscripción, solo cuenta el modelo de su proveedor; el otro no se puede escribir. */
  proveedorUnico?: Proveedor;
  alCambiar: (r: RequisitoRol) => void;
  alQuitar: () => void;
}) {
  const id = (s: string) => `rol-${nombre}-${s}`;
  return (
    <TableRow>
      <TableCell className="font-mono text-xs">{nombre}</TableCell>
      {PROVEEDORES.map((p) => (
        <TableCell key={p}>
          <Input
            id={id(p)}
            aria-label={`Modelo ${p} para ${nombre}`}
            list={`modelos-${p}`}
            disabled={!editable || (proveedorUnico !== undefined && proveedorUnico !== p)}
            className="h-8 min-w-36 text-xs"
            value={req.modelo[p] ?? ""}
            onChange={(e) => {
              const modelo = { ...req.modelo };
              if (e.target.value.trim()) modelo[p] = e.target.value.trim();
              else delete modelo[p];
              alCambiar({ ...req, modelo });
            }}
          />
        </TableCell>
      ))}
      <TableCell>
        <Select
          aria-label={`Effort para ${nombre}`}
          className="h-8 text-xs"
          disabled={!editable || proveedorUnico === "compatible"}
          vacio="—"
          title={proveedorUnico === "compatible" ? "Los proveedores compatibles no admiten effort." : undefined}
          value={proveedorUnico === "compatible" ? "" : (req.effort ?? "")}
          onChange={(e) => alCambiar({ ...req, effort: (e.target.value || null) as Effort | null })}
          opciones={EFFORTS.map((x) => ({ valor: x, etiqueta: x }))}
        />
      </TableCell>
      <TableCell className="text-center">
        <input
          type="checkbox"
          aria-label={`Structured outputs para ${nombre}`}
          disabled={!editable}
          checked={req.structured_outputs ?? false}
          onChange={(e) => alCambiar({ ...req, structured_outputs: e.target.checked })}
        />
      </TableCell>
      <TableCell>
        <Input
          aria-label={`Contexto mínimo para ${nombre}`}
          type="number"
          min={1}
          className="h-8 w-28 text-xs"
          disabled={!editable}
          value={req.contexto_min_tokens ?? ""}
          onChange={(e) => alCambiar({ ...req, contexto_min_tokens: numeroOpcional(e.target.value) })}
        />
      </TableCell>
      <TableCell>
        {editable ? (
          <Button variante="fantasma" tamano="pequeno" aria-label={`Quitar rol ${nombre}`} onClick={alQuitar}>
            ✕
          </Button>
        ) : null}
      </TableCell>
    </TableRow>
  );
}

/** Editor estructurado de un perfil: roles → modelo por proveedor, effort, gate por riesgo y exploradores. */
export function EditorPerfil({ org, ws, nombre, inicial, editable, alGuardar }: PropsEditorPerfil) {
  const [datos, setDatos] = useState<EscrituraPerfil>(inicial);
  const [nuevoRol, setNuevoRol] = useState("");
  const cat = useQuery({ queryKey: claves.catalogo(org), queryFn: () => catalogo.listar(org) });
  const sus = useQuery({ queryKey: claves.suscripciones(org), queryFn: () => suscripciones.listar(org) });
  const lasSuscripciones = sus.data?.suscripciones ?? [];
  const elegida = lasSuscripciones.find((x) => x.id === datos.suscripcion) ?? null;
  const modelos = useMemo(() => {
    const r: Record<Proveedor, string[]> = { foundry: [], anthropic: [], compatible: [] };
    // Con suscripción, solo sus modelos elegidos; sin ella, el catálogo del servidor (respaldo de entorno).
    if (elegida) {
      for (const m of elegida.modelos) if (m.seleccionado && !m.ausente) r[elegida.proveedor].push(m.clave);
    } else if (!datos.suscripcion) {
      for (const m of cat.data ?? []) r[m.proveedor].push(m.despliegue ?? m.modelo);
    }
    return r;
  }, [cat.data, elegida, datos.suscripcion]);

  const guardar = useGuardar(
    () => perfiles.guardar(org, nombre, datos, ws),
    [["perfiles", org]],
    (r) => {
      setDatos((d) => ({ ...d, version: r.perfil.version }));
      alGuardar?.();
    },
    { clave: `perfil:${org}:${ws ?? ""}:${nombre}`, version: (r) => r.perfil.version, avisos: (r) => r.avisos },
  );

  const setRol = (rol: string, req: RequisitoRol) => setDatos((d) => ({ ...d, roles: { ...d.roles, [rol]: req } }));
  const quitarRol = (rol: string) =>
    setDatos((d) => {
      const roles = { ...d.roles };
      delete roles[rol];
      return { ...d, roles };
    });
  const setGate = (r: Riesgo, parcial: Partial<TopeGate>) =>
    setDatos((d) => ({ ...d, gate: { ...d.gate, [r]: { ...TOPE_BASE, ...d.gate[r], ...parcial } } }));

  // El contrato exige al menos un modelo por rol: sin esto el servidor responde 422 al guardar la plantilla tal cual.
  const rolesSinModelo = Object.entries(datos.roles)
    .filter(([, req]) => Object.keys(req.modelo).length === 0)
    .map(([rol]) => rol);

  const enviar = (e: FormEvent) => {
    e.preventDefault();
    if (rolesSinModelo.length === 0) guardar.mutate(undefined);
  };

  return (
    <form onSubmit={enviar} className="flex flex-col gap-4">
      {PROVEEDORES.map((p) => (
        <datalist key={p} id={`modelos-${p}`}>
          {modelos[p].map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
      ))}
      {lasSuscripciones.length > 0 || datos.suscripcion ? (
        <section>
          <Campo
            etiqueta="Suscripción"
            htmlFor="perfil-suscripcion"
            ayuda={
              elegida
                ? `Los modelos de cada rol salen de los elegidos en «${elegida.nombre}» (${elegida.proveedor}).`
                : "La organización tiene suscripciones: elige una para que el perfil use sus modelos."
            }
          >
            <Select
              id="perfil-suscripcion"
              className="max-w-sm"
              disabled={!editable}
              vacio="Sin suscripción (proveedor del servidor)"
              value={datos.suscripcion ?? ""}
              onChange={(e) => {
                const id = e.target.value || null;
                const compat = lasSuscripciones.find((x) => x.id === id)?.proveedor === "compatible";
                // Un proveedor compatible no admite effort: se quita al elegirlo para que el perfil se pueda guardar.
                setDatos((d) => ({
                  ...d,
                  suscripcion: id,
                  ...(compat ? { roles: Object.fromEntries(Object.entries(d.roles).map(([rol, r]) => [rol, { ...r, effort: null }])) } : {}),
                }));
              }}
              opciones={lasSuscripciones.map((x) => ({
                valor: x.id,
                etiqueta: `${x.nombre} (${x.proveedor})${x.habilitada ? "" : " · deshabilitada"}`,
              }))}
            />
          </Campo>
          {elegida && !elegida.habilitada ? <p className="mt-1 text-sm text-peligro">Esta suscripción está deshabilitada.</p> : null}
          {elegida?.proveedor === "compatible" ? (
            <p className="mt-1 text-sm text-suave">
              Un proveedor compatible no admite effort. Usarlo es decisión tuya: puede tener otras condiciones de retención y región.
            </p>
          ) : null}
        </section>
      ) : null}
      <section>
        <h4 className="mb-1 text-sm font-semibold">Roles</h4>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Rol</TableHead>
              {PROVEEDORES.map((p) => (
                <TableHead key={p}>Modelo {p}</TableHead>
              ))}
              <TableHead>Effort</TableHead>
              <TableHead>Structured</TableHead>
              <TableHead>Contexto mín.</TableHead>
              <TableHead>
                <span className="sr-only">Quitar</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {Object.entries(datos.roles).map(([rol, req]) => (
              <FilaRol
                key={rol}
                nombre={rol}
                req={req}
                editable={editable}
                {...(elegida ? { proveedorUnico: elegida.proveedor } : {})}
                alCambiar={(r) => setRol(rol, r)}
                alQuitar={() => quitarRol(rol)}
              />
            ))}
          </TableBody>
        </Table>
        {editable && rolesSinModelo.length > 0 ? (
          <p role="status" className="mt-2 text-sm text-peligro">
            Falta el modelo ({elegida ? elegida.proveedor : "foundry, anthropic o compatible"}) de: {rolesSinModelo.join(", ")}.
          </p>
        ) : null}
        {editable ? (
          <div className="mt-2 flex gap-2">
            <Input
              aria-label="Nombre del nuevo rol"
              placeholder="nuevo rol (p. ej. critico)"
              className="h-8 max-w-60 text-xs"
              value={nuevoRol}
              onChange={(e) => setNuevoRol(e.target.value)}
            />
            <Button
              variante="secundario"
              tamano="pequeno"
              disabled={!nuevoRol.trim() || nuevoRol.trim() in datos.roles}
              onClick={() => {
                setRol(nuevoRol.trim(), { modelo: {}, structured_outputs: false });
                setNuevoRol("");
              }}
            >
              Añadir rol
            </Button>
          </div>
        ) : null}
      </section>
      <section className="grid gap-4 md:grid-cols-2">
        <div>
          <h4 className="mb-1 text-sm font-semibold">Gate por riesgo</h4>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Riesgo</TableHead>
                <TableHead>Críticos</TableHead>
                <TableHead>Iteraciones</TableHead>
                <TableHead>Adversarial</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {RIESGOS.map((r) => {
                const g = datos.gate[r] ?? TOPE_BASE;
                return (
                  <TableRow key={r}>
                    <TableCell>{r}</TableCell>
                    <TableCell>
                      <Input
                        aria-label={`Críticos riesgo ${r}`}
                        type="number"
                        min={1}
                        className="h-8 w-20"
                        disabled={!editable}
                        value={g.criticos}
                        onChange={(e) => setGate(r, { criticos: Number(e.target.value) })}
                      />
                    </TableCell>
                    <TableCell>
                      <Input
                        aria-label={`Iteraciones riesgo ${r}`}
                        type="number"
                        min={1}
                        className="h-8 w-20"
                        disabled={!editable}
                        value={g.iteraciones}
                        onChange={(e) => setGate(r, { iteraciones: Number(e.target.value) })}
                      />
                    </TableCell>
                    <TableCell className="text-center">
                      <input
                        type="checkbox"
                        aria-label={`Adversarial riesgo ${r}`}
                        disabled={!editable}
                        checked={g.adversarial}
                        onChange={(e) => setGate(r, { adversarial: e.target.checked })}
                      />
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
        <div>
          <h4 className="mb-1 text-sm font-semibold">Exploradores por riesgo</h4>
          <div className="flex flex-col gap-2">
            {RIESGOS.map((r) => (
              <label key={r} className="flex items-center gap-2 text-sm">
                <span className="w-16">{r}</span>
                <Input
                  type="number"
                  min={0}
                  className="h-8 w-24"
                  disabled={!editable}
                  value={datos.exploradores[r] ?? 0}
                  onChange={(e) => setDatos((d) => ({ ...d, exploradores: { ...d.exploradores, [r]: Number(e.target.value) } }))}
                />
              </label>
            ))}
          </div>
        </div>
      </section>
      <AvisoGuardado guardado={guardar.guardado} />
      <ErrorGuardado error={guardar.error} />
      {editable ? (
        <div>
          <Button type="submit" disabled={guardar.isPending || Object.keys(datos.roles).length === 0 || rolesSinModelo.length > 0}>
            {guardar.isPending ? "Guardando…" : "Guardar perfil"}
          </Button>
        </div>
      ) : null}
    </form>
  );
}
