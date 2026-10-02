import { useEffect, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { claves, repositorios } from "../../api/endpoints";
import {
  NIVELES_CODIGO,
  type EscrituraVinculo,
  type NivelCodigo,
  type PoliticaChat,
  type RolRepositorio,
  type VinculoRepositorio,
} from "../../api/tipos";
import { Cargando, ErrorVista, Vacio } from "../../componentes/Estados";
import { Badge } from "../../componentes/ui/badge";
import { Button } from "../../componentes/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../componentes/ui/card";
import { Dialog } from "../../componentes/ui/dialog";
import { Campo, Input, Textarea } from "../../componentes/ui/input";
import { opcionesDe, Select } from "../../componentes/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";
import { AvisoGuardado, ErrorGuardado, useGuardado, useGuardar } from "../../lib/mutaciones";
import { fecha } from "../../lib/utiles";

const POLITICA_BASE: PoliticaChat = {
  hosting: "azure-zona-datos",
  huella_tokens_n: 8,
  presupuesto_fuga_conversacion: 2000,
  presupuesto_fuga_usuario_dia: 20000,
  fragmentos_en_respuesta: false,
  modelos_permitidos: [],
  permitido: true,
};

const listaDeModelos = (texto: string): string[] =>
  texto
    .split(",")
    .map((m) => m.trim())
    .filter(Boolean);

/**
 * Los modelos se escriben separados por comas: el texto se conserva tal cual mientras se teclea (si se
 * normalizara en cada pulsación, la coma desaparecería antes de poder escribir el siguiente modelo).
 */
function CampoModelos({ modelos, alCambiar }: { modelos: string[]; alCambiar: (m: string[]) => void }) {
  const [texto, setTexto] = useState(modelos.join(", "));
  // Si la lista cambia desde fuera (otro valor cargado), el texto la sigue.
  useEffect(() => {
    setTexto((previo) => (listaDeModelos(previo).join(",") === modelos.join(",") ? previo : modelos.join(", ")));
  }, [modelos]);
  return (
    <Campo etiqueta="Modelos permitidos" htmlFor="pol-modelos" ayuda="Separados por comas; vacío = todos los del catálogo." className="sm:col-span-2">
      <Input
        id="pol-modelos"
        value={texto}
        onChange={(e) => {
          setTexto(e.target.value);
          alCambiar(listaDeModelos(e.target.value));
        }}
      />
    </Campo>
  );
}

function EditorPolitica({ valor, alCambiar }: { valor: PoliticaChat; alCambiar: (p: PoliticaChat) => void }) {
  const num = (k: keyof PoliticaChat) => (e: { target: { value: string } }) => alCambiar({ ...valor, [k]: Number(e.target.value) });
  return (
    <fieldset className="grid gap-3 rounded-md border border-borde p-3 sm:grid-cols-2">
      <legend className="px-1 text-sm font-medium">Política de chat sobre el código</legend>
      <label className="flex items-center gap-2 text-sm sm:col-span-2">
        <input type="checkbox" checked={valor.permitido ?? true} onChange={(e) => alCambiar({ ...valor, permitido: e.target.checked })} />
        Chat permitido sobre este repositorio
      </label>
      <Campo etiqueta="Hosting" htmlFor="pol-hosting">
        <Select
          id="pol-hosting"
          value={valor.hosting}
          onChange={(e) => alCambiar({ ...valor, hosting: e.target.value as PoliticaChat["hosting"] })}
          opciones={opcionesDe(["azure-zona-datos", "cualquiera"])}
        />
      </Campo>
      <Campo etiqueta="Huella de tokens (n)" htmlFor="pol-huella" ayuda="Entre 4 y 64.">
        <Input id="pol-huella" type="number" min={4} max={64} required value={valor.huella_tokens_n} onChange={num("huella_tokens_n")} />
      </Campo>
      <Campo etiqueta="Fuga máx. por conversación (tokens)" htmlFor="pol-fuga-conv">
        <Input
          id="pol-fuga-conv"
          type="number"
          min={1}
          required
          value={valor.presupuesto_fuga_conversacion}
          onChange={num("presupuesto_fuga_conversacion")}
        />
      </Campo>
      <Campo etiqueta="Fuga máx. por usuario y día (tokens)" htmlFor="pol-fuga-dia">
        <Input
          id="pol-fuga-dia"
          type="number"
          min={1}
          required
          value={valor.presupuesto_fuga_usuario_dia}
          onChange={num("presupuesto_fuga_usuario_dia")}
        />
      </Campo>
      <CampoModelos modelos={valor.modelos_permitidos ?? []} alCambiar={(m) => alCambiar({ ...valor, modelos_permitidos: m })} />
      <label className="flex items-center gap-2 text-sm sm:col-span-2">
        <input
          type="checkbox"
          checked={valor.fragmentos_en_respuesta ?? false}
          onChange={(e) => alCambiar({ ...valor, fragmentos_en_respuesta: e.target.checked })}
        />
        Permitir fragmentos de código en las respuestas
      </label>
    </fieldset>
  );
}

/** Dónde queda el «Guardado»: el diálogo se cierra al guardar y la tarjeta lo anuncia. */
const claveAviso = (org: string, ws: string) => `repositorios:${org}:${ws}`;

function FormularioVinculo({
  org,
  ws,
  vinculo,
  alCerrar,
}: {
  org: string;
  ws: string;
  vinculo?: VinculoRepositorio;
  alCerrar: () => void;
}) {
  const [slug, setSlug] = useState(vinculo?.alcance.repositorio ?? "");
  const [url, setUrl] = useState(vinculo?.url ?? "https://github.com/");
  const [rol, setRol] = useState<RolRepositorio>(vinculo?.rol ?? "primario");
  const [rama, setRama] = useState(vinculo?.rama_por_defecto ?? "main");
  const [nivel, setNivel] = useState<NivelCodigo>(vinculo?.nivel_codigo ?? "restringido");
  const [retencion, setRetencion] = useState(String(vinculo?.retencion_snapshots_dias ?? 30));
  const [exclusiones, setExclusiones] = useState((vinculo?.exclusiones ?? []).join("\n"));
  const [avanzada, setAvanzada] = useState(false);
  const [politica, setPolitica] = useState<PoliticaChat>(vinculo?.chat_contexto_codigo ?? POLITICA_BASE);
  const [motivo, setMotivo] = useState("");
  const cambiaNivel = vinculo !== undefined && vinculo.nivel_codigo !== nivel;

  const guardar = useGuardar(
    () => {
      const datos: EscrituraVinculo = {
        url: url.trim(),
        rol,
        rama_por_defecto: rama.trim() || "main",
        nivel_codigo: nivel,
        retencion_snapshots_dias: Number(retencion) || 30,
        exclusiones: exclusiones
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
      };
      if (avanzada) datos.chat_contexto_codigo = politica;
      if (vinculo) datos.version = vinculo.version;
      if (cambiaNivel) datos.motivo = motivo.trim();
      return repositorios.guardar(org, ws, slug.trim(), datos);
    },
    [claves.repositorios(org, ws), claves.grafo(org, ws)],
    alCerrar,
    { clave: claveAviso(org, ws) },
  );
  const enviar = (e: FormEvent) => {
    e.preventDefault();
    if (cambiaNivel && !motivo.trim()) return;
    guardar.mutate(undefined);
  };
  return (
    <Dialog abierto alCerrar={alCerrar} titulo={vinculo ? `Editar ${vinculo.alcance.repositorio}` : "Vincular repositorio"} className="max-w-2xl">
      <form onSubmit={enviar} className="flex flex-col gap-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Campo etiqueta="Identificador" htmlFor="repo-slug" ayuda={vinculo ? "No se puede cambiar." : "Minúsculas, dígitos y guiones."}>
            <Input
              id="repo-slug"
              required
              disabled={Boolean(vinculo)}
              pattern="^[a-z0-9][a-z0-9\-]{0,62}$"
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
            />
          </Campo>
          <Campo etiqueta="URL" htmlFor="repo-url">
            <Input id="repo-url" type="url" required pattern="^https://.*" maxLength={512} value={url} onChange={(e) => setUrl(e.target.value)} />
          </Campo>
          <Campo etiqueta="Rol" htmlFor="repo-rol">
            <Select id="repo-rol" value={rol} onChange={(e) => setRol(e.target.value as RolRepositorio)} opciones={opcionesDe(["primario", "transversal"])} />
          </Campo>
          <Campo etiqueta="Rama por defecto" htmlFor="repo-rama">
            <Input id="repo-rama" maxLength={255} value={rama} onChange={(e) => setRama(e.target.value)} />
          </Campo>
          <Campo etiqueta="Nivel de política de código" htmlFor="repo-nivel">
            <Select id="repo-nivel" value={nivel} onChange={(e) => setNivel(e.target.value as NivelCodigo)} opciones={opcionesDe(NIVELES_CODIGO)} />
          </Campo>
          <Campo etiqueta="Retención de snapshots (días)" htmlFor="repo-retencion">
            <Input id="repo-retencion" type="number" min={1} value={retencion} onChange={(e) => setRetencion(e.target.value)} />
          </Campo>
        </div>
        {cambiaNivel ? (
          <Campo etiqueta="Motivo del cambio de nivel" htmlFor="repo-motivo" ayuda="Obligatorio: queda en la auditoría (cambio-nivel).">
            <Textarea id="repo-motivo" required value={motivo} onChange={(e) => setMotivo(e.target.value)} />
          </Campo>
        ) : null}
        <Campo etiqueta="Exclusiones (glob, una por línea)" htmlFor="repo-exclusiones">
          <Textarea id="repo-exclusiones" value={exclusiones} onChange={(e) => setExclusiones(e.target.value)} placeholder="secrets/**" />
        </Campo>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={avanzada} onChange={(e) => setAvanzada(e.target.checked)} />
          Personalizar la política de chat (si no, se usa la del nivel)
        </label>
        {avanzada ? <EditorPolitica valor={politica} alCambiar={setPolitica} /> : null}
        <ErrorGuardado error={guardar.error} />
        <div className="flex justify-end gap-2">
          <Button variante="secundario" onClick={alCerrar}>
            Cancelar
          </Button>
          <Button type="submit" disabled={guardar.isPending || (cambiaNivel && !motivo.trim())}>
            {guardar.isPending ? "Guardando…" : "Guardar"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function DialogoDesvincular({ org, ws, vinculo, alCerrar }: { org: string; ws: string; vinculo: VinculoRepositorio; alCerrar: () => void }) {
  const nombre = vinculo.alcance.repositorio;
  const [confirmacion, setConfirmacion] = useState("");
  const [motivo, setMotivo] = useState("");
  const desvincular = useGuardar(
    () => repositorios.desvincular(org, ws, nombre, motivo.trim()),
    [claves.repositorios(org, ws), claves.grafo(org, ws)],
    alCerrar,
  );
  const listo = confirmacion === nombre && motivo.trim().length > 0;
  return (
    <Dialog
      abierto
      alCerrar={alCerrar}
      titulo={`Desvincular ${nombre}`}
      descripcion="Borra el vínculo y el grafo de este repositorio. Queda auditado y no se puede deshacer."
    >
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (listo) desvincular.mutate(undefined);
        }}
      >
        <Campo etiqueta={`Escribe «${nombre}» para confirmar`} htmlFor="desv-nombre">
          <Input id="desv-nombre" autoComplete="off" value={confirmacion} onChange={(e) => setConfirmacion(e.target.value)} />
        </Campo>
        <Campo etiqueta="Motivo" htmlFor="desv-motivo">
          <Textarea id="desv-motivo" required value={motivo} onChange={(e) => setMotivo(e.target.value)} />
        </Campo>
        <ErrorGuardado error={desvincular.error} />
        <div className="flex justify-end gap-2">
          <Button variante="secundario" onClick={alCerrar}>
            Cancelar
          </Button>
          <Button type="submit" variante="peligro" disabled={!listo || desvincular.isPending}>
            Desvincular
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

export function Repositorios({ org, ws, puedeEditar }: { org: string; ws: string; puedeEditar: boolean }) {
  const lista = useQuery({ queryKey: claves.repositorios(org, ws), queryFn: () => repositorios.listar(org, ws) });
  const [editando, setEditando] = useState<VinculoRepositorio | "nuevo" | null>(null);
  const [desvinculando, setDesvinculando] = useState<VinculoRepositorio | null>(null);
  const guardado = useGuardado(claveAviso(org, ws));
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between">
        <div>
          <CardTitle>Repositorios vinculados</CardTitle>
          <CardDescription>Nivel de política de código, retención y exclusiones de cada repositorio.</CardDescription>
        </div>
        {puedeEditar ? (
          <Button tamano="pequeno" onClick={() => setEditando("nuevo")}>
            Vincular repositorio
          </Button>
        ) : null}
      </CardHeader>
      <CardContent>
        <AvisoGuardado guardado={guardado} className="mb-3" />
        {lista.isPending ? <Cargando /> : null}
        {lista.isError ? <ErrorVista error={lista.error} reintentar={() => void lista.refetch()} /> : null}
        {lista.isSuccess && lista.data.length === 0 ? <Vacio titulo="Sin repositorios vinculados" /> : null}
        {lista.isSuccess && lista.data.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Repositorio</TableHead>
                <TableHead>Rol</TableHead>
                <TableHead>Nivel</TableHead>
                <TableHead>Rama</TableHead>
                <TableHead>Retención</TableHead>
                <TableHead>Chat</TableHead>
                <TableHead>Actualizado</TableHead>
                <TableHead>
                  <span className="sr-only">Acciones</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {lista.data.map((v) => (
                <TableRow key={v.alcance.repositorio}>
                  <TableCell>
                    <p className="font-medium">{v.alcance.repositorio}</p>
                    <a href={v.url} className="text-xs text-primario" target="_blank" rel="noreferrer noopener">
                      {v.url}
                    </a>
                    {v.exclusiones.length ? <p className="text-xs text-suave">{v.exclusiones.length} exclusiones</p> : null}
                  </TableCell>
                  <TableCell>{v.rol}</TableCell>
                  <TableCell>
                    <Badge tono={v.nivel_codigo === "restringido" ? "peligro" : v.nivel_codigo === "interno" ? "aviso" : "exito"}>
                      {v.nivel_codigo}
                    </Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs">{v.rama_por_defecto}</TableCell>
                  <TableCell>{v.retencion_snapshots_dias} días</TableCell>
                  <TableCell className="text-xs">
                    {v.chat_contexto_codigo.permitido === false ? "no permitido" : v.chat_contexto_codigo.hosting}
                  </TableCell>
                  <TableCell className="text-xs">{fecha(v.auditoria.actualizado_en)}</TableCell>
                  <TableCell>
                    {puedeEditar ? (
                      <div className="flex gap-1">
                        <Button variante="secundario" tamano="pequeno" onClick={() => setEditando(v)}>
                          Editar
                        </Button>
                        <Button variante="fantasma" tamano="pequeno" onClick={() => setDesvinculando(v)}>
                          Desvincular
                        </Button>
                      </div>
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </CardContent>
      {editando ? (
        <FormularioVinculo
          org={org}
          ws={ws}
          {...(editando === "nuevo" ? {} : { vinculo: editando })}
          alCerrar={() => setEditando(null)}
        />
      ) : null}
      {desvinculando ? <DialogoDesvincular org={org} ws={ws} vinculo={desvinculando} alCerrar={() => setDesvinculando(null)} /> : null}
    </Card>
  );
}
