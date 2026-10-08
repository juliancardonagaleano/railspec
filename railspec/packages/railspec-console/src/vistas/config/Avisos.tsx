import { useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { avisos, claves } from "../../api/endpoints";
import type { CanalAviso, EstadoAvisos, InformeSemanal, ResultadoPruebaAvisos, TipoAviso } from "../../api/tipos";
import { Aviso, Cargando, ErrorVista, Vacio } from "../../componentes/Estados";
import { Badge } from "../../componentes/ui/badge";
import { Button } from "../../componentes/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../componentes/ui/card";
import { Campo, Input, Textarea } from "../../componentes/ui/input";
import { Select } from "../../componentes/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";
import { AvisoGuardado, ErrorGuardado, useGuardado, useGuardar } from "../../lib/mutaciones";
import { fecha, numero, usd } from "../../lib/utiles";

const claveAviso = (org: string) => `avisos:${org}`;
const MAX_DESTINATARIOS = 20;
const DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"];
const HORAS = Array.from({ length: 24 }, (_, h) => ({ valor: String(h), etiqueta: `${String(h).padStart(2, "0")}:00 UTC` }));

const ETIQUETA_TIPO: Record<TipoAviso, string> = {
  "gate-escalado": "Gate escalado",
  "presupuesto-agotado": "Presupuesto agotado",
  "informe-semanal": "Informe semanal",
  prueba: "Prueba",
};
const ETIQUETA_CANAL: Record<CanalAviso, string> = { teams: "Teams", correo: "Correo" };

const destinatariosDe = (texto: string) =>
  texto
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

function FormularioAvisos({ org, estado, editable }: { org: string; estado: EstadoAvisos; editable: boolean }) {
  const { config, cifrado, canales } = estado;
  const [activo, setActivo] = useState(config.activo);
  const [teamsUrl, setTeamsUrl] = useState("");
  const [quitar, setQuitar] = useState(false);
  const [texto, setTexto] = useState(config.correo.destinatarios.join("\n"));
  const [gate, setGate] = useState(config.eventos.gate_escalado);
  const [presupuesto, setPresupuesto] = useState(config.eventos.presupuesto_agotado);
  const [informeActivo, setInformeActivo] = useState(config.informe.activo);
  const [dia, setDia] = useState(config.informe.dia_semana);
  const [hora, setHora] = useState(config.informe.hora_utc);

  const teamsBloqueado = !editable || !cifrado.disponible || !canales.teams.disponible;
  const correoBloqueado = !editable || !canales.correo.disponible;
  const destinatarios = destinatariosDe(texto);
  const demasiados = destinatarios.length > MAX_DESTINATARIOS;
  const url = teamsUrl.trim();

  const guardar = useGuardar(
    () =>
      avisos.guardar(org, {
        activo,
        ...(url && !teamsBloqueado ? { teams_url: url } : {}),
        quitar_teams: quitar,
        destinatarios,
        eventos: { gate_escalado: gate, presupuesto_agotado: presupuesto },
        informe: { activo: informeActivo, dia_semana: dia, hora_utc: hora },
        ...(config.version === null ? {} : { version: config.version }),
      }),
    [claves.avisos(org)],
    undefined,
    { clave: claveAviso(org), version: (r) => r.version ?? undefined },
  );
  const enviar = (e: FormEvent) => {
    e.preventDefault();
    if (!demasiados && editable) guardar.mutate(undefined);
  };
  const motivoTeams = !cifrado.disponible ? (
    <Aviso tono="aviso">
      El servidor no puede cifrar el URL del webhook: define <code>{cifrado.variable}</code> para configurar Teams.
    </Aviso>
  ) : !canales.teams.disponible ? (
    <Aviso tono="aviso">{canales.teams.motivo ?? "Teams no está disponible en este servidor."}</Aviso>
  ) : null;

  return (
    <form onSubmit={enviar} className="flex flex-col gap-4">
      <label className="flex items-center gap-2 text-sm font-medium">
        <input type="checkbox" checked={activo} disabled={!editable} onChange={(e) => setActivo(e.target.checked)} />
        Avisos activos
      </label>

      <section aria-label="Teams" className="flex flex-col gap-2 rounded-lg border border-borde p-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold">Microsoft Teams</h3>
          {config.teams.configurado && !quitar ? (
            <Badge tono="exito">{config.teams.host ? `Configurado: ${config.teams.host}` : "Configurado"}</Badge>
          ) : (
            <Badge tono="neutro">{quitar ? "Se quitará al guardar" : "Sin configurar"}</Badge>
          )}
        </div>
        {motivoTeams}
        <Campo
          etiqueta="URL del webhook de Teams"
          htmlFor="avisos-teams-url"
          ayuda={`Solo para reemplazarlo: se cifra al guardar y no se vuelve a mostrar. Hosts permitidos: ${estado.hosts_teams.join(", ") || "—"}.`}
        >
          <Input
            id="avisos-teams-url"
            type="password"
            autoComplete="new-password"
            spellCheck={false}
            maxLength={2000}
            disabled={teamsBloqueado}
            value={teamsUrl}
            onChange={(e) => {
              setTeamsUrl(e.target.value);
              if (e.target.value) setQuitar(false);
            }}
          />
        </Campo>
        {config.teams.configurado ? (
          <div>
            <Button
              tamano="pequeno"
              variante="secundario"
              disabled={teamsBloqueado}
              onClick={() => {
                setQuitar(!quitar);
                setTeamsUrl("");
              }}
            >
              {quitar ? "Deshacer" : "Quitar"}
            </Button>
          </div>
        ) : null}
      </section>

      <section aria-label="Correo" className="flex flex-col gap-2 rounded-lg border border-borde p-3">
        <h3 className="text-sm font-semibold">Correo</h3>
        {!canales.correo.disponible ? <Aviso tono="aviso">{canales.correo.motivo ?? "El correo no está disponible en este servidor."}</Aviso> : null}
        <Campo
          etiqueta="Destinatarios"
          htmlFor="avisos-destinatarios"
          ayuda={`Una dirección por línea (máximo ${MAX_DESTINATARIOS}).`}
        >
          <Textarea
            id="avisos-destinatarios"
            rows={4}
            spellCheck={false}
            disabled={correoBloqueado}
            value={texto}
            onChange={(e) => setTexto(e.target.value)}
          />
        </Campo>
        {demasiados ? <p className="text-sm text-peligro">Hay más de {MAX_DESTINATARIOS} destinatarios.</p> : null}
      </section>

      <fieldset className="flex flex-col gap-2 rounded-lg border border-borde p-3">
        <legend className="px-1 text-sm font-semibold">Eventos</legend>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={gate} disabled={!editable} onChange={(e) => setGate(e.target.checked)} />
          Gate escalado
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={presupuesto} disabled={!editable} onChange={(e) => setPresupuesto(e.target.checked)} />
          Presupuesto agotado
        </label>
      </fieldset>

      <fieldset className="flex flex-col gap-2 rounded-lg border border-borde p-3">
        <legend className="px-1 text-sm font-semibold">Informe semanal</legend>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={informeActivo} disabled={!editable} onChange={(e) => setInformeActivo(e.target.checked)} />
          Informe semanal activo
        </label>
        <div className="grid gap-3 sm:grid-cols-2">
          <Campo etiqueta="Día de la semana" htmlFor="avisos-dia">
            <Select
              id="avisos-dia"
              disabled={!editable}
              value={String(dia)}
              onChange={(e) => setDia(Number(e.target.value))}
              opciones={DIAS.map((d, i) => ({ valor: String(i), etiqueta: d }))}
            />
          </Campo>
          <Campo etiqueta="Hora (UTC)" htmlFor="avisos-hora">
            <Select id="avisos-hora" disabled={!editable} value={String(hora)} onChange={(e) => setHora(Number(e.target.value))} opciones={HORAS} />
          </Campo>
        </div>
      </fieldset>

      <ErrorGuardado error={guardar.error} />
      {editable ? (
        <div>
          <Button type="submit" disabled={guardar.isPending || demasiados}>
            {guardar.isPending ? "Guardando…" : "Guardar"}
          </Button>
        </div>
      ) : null}
    </form>
  );
}

function Pruebas({ org }: { org: string }) {
  const probar = useMutation({ mutationFn: (que: "aviso" | "informe") => avisos.prueba(org, que) });
  const resultados: ResultadoPruebaAvisos["resultados"] = probar.data?.resultados ?? [];
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        <Button variante="secundario" disabled={probar.isPending} onClick={() => probar.mutate("aviso")}>
          Enviar aviso de prueba
        </Button>
        <Button variante="secundario" disabled={probar.isPending} onClick={() => probar.mutate("informe")}>
          Enviar informe ahora
        </Button>
      </div>
      <p className="text-xs text-suave">Se envía con la configuración guardada, a los canales disponibles.</p>
      {probar.isError ? <ErrorVista error={probar.error} /> : null}
      {probar.isSuccess && resultados.length === 0 ? <Aviso>No hay ningún canal configurado a donde enviar.</Aviso> : null}
      {resultados.length > 0 ? (
        <ul aria-label="Resultado de la prueba" className="flex flex-col gap-1 text-sm">
          {resultados.map((r) => (
            <li key={r.canal}>
              {ETIQUETA_CANAL[r.canal]}: {r.resultado === "enviado" ? "enviado" : `error${r.codigo ? ` (${r.codigo})` : ""}`}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Ultimos({ estado }: { estado: EstadoAvisos }) {
  if (estado.ultimos.length === 0) return <Vacio titulo="Sin avisos todavía" />;
  return (
    <Table aria-label="Últimos avisos">
      <TableHeader>
        <TableRow>
          <TableHead>Cuándo</TableHead>
          <TableHead>Tipo</TableHead>
          <TableHead>Canal</TableHead>
          <TableHead>Resultado</TableHead>
          <TableHead>Workspace</TableHead>
          <TableHead>Unidad</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {estado.ultimos.map((u, i) => (
          <TableRow key={`${u.en}-${i}`}>
            <TableCell>{fecha(u.en)}</TableCell>
            <TableCell>{ETIQUETA_TIPO[u.tipo]}</TableCell>
            <TableCell>{ETIQUETA_CANAL[u.canal]}</TableCell>
            <TableCell>
              {u.resultado === "enviado" ? <Badge tono="exito">enviado</Badge> : <Badge tono="peligro">{u.codigo ? `error: ${u.codigo}` : "error"}</Badge>}
            </TableCell>
            <TableCell>{u.workspace ?? "—"}</TableCell>
            <TableCell>{u.unidad ?? "—"}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

const ETIQUETA_TIER = { bajo: "Bajo", medio: "Medio", alto: "Alto" } as const;

function VistaPrevia({ informe }: { informe: InformeSemanal }) {
  const t = informe.totales;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-suave">
        Del {fecha(informe.desde)} al {fecha(informe.hasta)}
      </p>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div>
          <dt className="text-xs text-suave">Unidades cerradas</dt>
          <dd className="text-lg font-semibold">{numero(t.unidades_cerradas)}</dd>
        </div>
        <div>
          <dt className="text-xs text-suave">Gates escalados</dt>
          <dd className="text-lg font-semibold">{numero(t.gates_escalados)}</dd>
        </div>
        <div>
          <dt className="text-xs text-suave">Costo</dt>
          <dd className="text-lg font-semibold">{usd(t.costo_usd)}</dd>
        </div>
        <div>
          <dt className="text-xs text-suave">Llamadas</dt>
          <dd className="text-lg font-semibold">{numero(t.llamadas)}</dd>
        </div>
      </dl>
      <Table aria-label="Por tier">
        <TableHeader>
          <TableRow>
            <TableHead>Tier</TableHead>
            <TableHead>Costo</TableHead>
            <TableHead>Llamadas</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {informe.por_tier.map((x) => (
            <TableRow key={x.tier ?? "sin"}>
              <TableCell>{x.tier ? ETIQUETA_TIER[x.tier] : "Sin tier"}</TableCell>
              <TableCell>{usd(x.costo_usd)}</TableCell>
              <TableCell>{numero(x.llamadas)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Table aria-label="Por workspace">
        <TableHeader>
          <TableRow>
            <TableHead>Workspace</TableHead>
            <TableHead>Unidades cerradas</TableHead>
            <TableHead>Gates escalados</TableHead>
            <TableHead>Costo</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {informe.por_workspace.map((x) => (
            <TableRow key={x.workspace}>
              <TableCell>{x.workspace}</TableCell>
              <TableCell>{numero(x.unidades_cerradas)}</TableCell>
              <TableCell>{numero(x.gates_escalados)}</TableCell>
              <TableCell>{usd(x.costo_usd)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export function Avisos({ org }: { org: string }) {
  const estado = useQuery({ queryKey: claves.avisos(org), queryFn: () => avisos.leer(org) });
  const informe = useQuery({ queryKey: claves.informeSemanal(org), queryFn: () => avisos.informeSemanal(org) });
  const guardado = useGuardado(claveAviso(org));
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Avisos e informes</CardTitle>
          <CardDescription>
            Avisa a Teams o por correo cuando un gate escala o se agota un presupuesto, y envía un resumen semanal de la organización.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <AvisoGuardado guardado={guardado} />
          {estado.isPending ? <Cargando /> : null}
          {estado.isError ? <ErrorVista error={estado.error} reintentar={() => void estado.refetch()} /> : null}
          {estado.isSuccess ? (
            <>
              <FormularioAvisos key={estado.data.config.version ?? "nueva"} org={org} estado={estado.data} editable />
              <Pruebas org={org} />
            </>
          ) : null}
        </CardContent>
      </Card>
      {estado.isSuccess ? (
        <Card>
          <CardHeader>
            <CardTitle>Últimos avisos</CardTitle>
          </CardHeader>
          <CardContent>
            <Ultimos estado={estado.data} />
          </CardContent>
        </Card>
      ) : null}
      <Card>
        <CardHeader>
          <CardTitle>Vista previa del informe semanal</CardTitle>
          <CardDescription>Lo que llevaría el informe si se enviara ahora.</CardDescription>
        </CardHeader>
        <CardContent>
          {informe.isPending ? <Cargando /> : null}
          {informe.isError ? <ErrorVista error={informe.error} reintentar={() => void informe.refetch()} /> : null}
          {informe.isSuccess ? <VistaPrevia informe={informe.data} /> : null}
        </CardContent>
      </Card>
    </div>
  );
}
