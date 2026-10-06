import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { claves, suscripciones } from "../../api/endpoints";
import {
  AUTENTICACIONES,
  PROTOCOLOS_COMPATIBLE,
  PROVEEDORES,
  type Autenticacion,
  type ModeloSuscripcion,
  type PrecioModelo,
  type Proveedor,
  type ProtocoloCompatible,
  type ServicioCompatible,
  type Suscripcion,
} from "../../api/tipos";
import { Aviso, Cargando, ErrorVista, Vacio } from "../../componentes/Estados";
import { Badge } from "../../componentes/ui/badge";
import { Button } from "../../componentes/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../componentes/ui/card";
import { Dialog } from "../../componentes/ui/dialog";
import { Campo, Input } from "../../componentes/ui/input";
import { opcionesDe, Select } from "../../componentes/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../componentes/ui/table";
import { AvisoGuardado, ErrorGuardado, numeroOpcional, useGuardado, useGuardar } from "../../lib/mutaciones";
import { fecha, numero } from "../../lib/utiles";

/** Dónde queda el «Guardado»: el diálogo se cierra al guardar y la lista lo anuncia. */
const claveAviso = (org: string) => `suscripciones:${org}`;
const CLAVE_NUEVA = "nueva";
const SLUG = /[^a-z0-9]+/g;

/** Id de la suscripción a partir del nombre: minúsculas, números y guiones. */
export const idDesdeNombre = (nombre: string) =>
  nombre
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(SLUG, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);

const ETIQUETA_PROVEEDOR: Record<Proveedor, string> = {
  foundry: "Azure AI Foundry",
  anthropic: "Anthropic",
  compatible: "Compatible (OpenCode Zen, MiniMax…)",
};
const PERSONALIZADO = "personalizado";
const ETIQUETA_PROTOCOLO: Record<ProtocoloCompatible, string> = {
  "anthropic-messages": "Messages de Anthropic",
  "openai-chat": "Chat completions de OpenAI",
};

function FormularioSuscripcion({
  org,
  actual,
  servicios,
  alCerrar,
}: {
  org: string;
  actual?: Suscripcion;
  servicios: ServicioCompatible[];
  alCerrar: () => void;
}) {
  const [proveedor, setProveedor] = useState<Proveedor>(actual?.proveedor ?? "foundry");
  const [nombre, setNombre] = useState(actual?.nombre ?? "");
  const [endpoint, setEndpoint] = useState(actual?.endpoint ?? "https://");
  const [proyecto, setProyecto] = useState(actual?.proyecto ?? "");
  const [region, setRegion] = useState(actual?.region ?? "");
  const [zona, setZona] = useState(actual?.zona_datos ?? "");
  const [autenticacion, setAutenticacion] = useState<Autenticacion>(actual?.autenticacion ?? "api-key");
  const [clave, setClave] = useState("");
  const [habilitada, setHabilitada] = useState(actual?.habilitada ?? true);
  const [servicio, setServicio] = useState(actual?.servicio ?? (actual?.proveedor === "compatible" ? PERSONALIZADO : (servicios[0]?.id ?? PERSONALIZADO)));
  const [epChat, setEpChat] = useState(actual?.servicio ? "" : (actual?.endpoint ?? ""));
  const [epMensajes, setEpMensajes] = useState(actual?.servicio ? "" : (actual?.endpoint_mensajes ?? ""));
  const foundry = proveedor === "foundry";
  const compatible = proveedor === "compatible";
  const conocido = compatible ? servicios.find((x) => x.id === servicio) : undefined;
  const propio = compatible && !conocido;
  const id = actual?.id ?? idDesdeNombre(nombre);
  // Cambiar a dónde apunta la suscripción obliga a escribir la clave otra vez: no viaja a un host nuevo sin que la veas.
  const destino = (sv: string | null | undefined, ep: string | null | undefined, em: string | null | undefined) =>
    sv ? `${sv}||` : `|${ep ?? ""}|${em ?? ""}`;
  const cambiaEndpoint =
    Boolean(actual) &&
    (foundry
      ? endpoint.trim() !== (actual?.endpoint ?? "")
      : compatible &&
        destino(conocido?.id, epChat.trim(), epMensajes.trim()) !==
          destino(actual?.servicio, actual?.endpoint, actual?.endpoint_mensajes));
  const necesitaClave = autenticacion === "api-key" && (!actual || cambiaEndpoint || !actual.clave_configurada);
  const claveFalta = necesitaClave && !clave.trim();
  const sinEndpoint = propio && !epChat.trim() && !epMensajes.trim();

  const guardar = useGuardar(
    () =>
      suscripciones.guardar(org, id, {
        proveedor,
        nombre: nombre.trim(),
        autenticacion: foundry ? autenticacion : "api-key",
        ...(foundry
          ? { endpoint: endpoint.trim(), proyecto: proyecto.trim() || null, region: region.trim() || null, zona_datos: zona.trim() || null }
          : {}),
        ...(conocido ? { servicio: conocido.id } : {}),
        ...(propio ? { endpoint: epChat.trim() || null, endpoint_mensajes: epMensajes.trim() || null } : {}),
        habilitada,
        ...(autenticacion === "api-key" && clave ? { clave } : {}),
        ...(actual ? { version: actual.version } : {}),
      }),
    [claves.suscripciones(org), ["perfiles", org]],
    alCerrar,
    { clave: claveAviso(org) },
  );
  const enviar = (e: FormEvent) => {
    e.preventDefault();
    if (!claveFalta && !sinEndpoint && id) guardar.mutate(undefined);
  };
  return (
    <Dialog abierto alCerrar={alCerrar} titulo={actual ? `Editar ${actual.nombre}` : "Nueva suscripción"} className="max-w-xl">
      <form onSubmit={enviar} className="flex flex-col gap-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Campo etiqueta="Proveedor" htmlFor="sus-proveedor">
            <Select
              id="sus-proveedor"
              disabled={Boolean(actual)}
              value={proveedor}
              onChange={(e) => {
                const p = e.target.value as Proveedor;
                setProveedor(p);
                if (p !== "foundry") setAutenticacion("api-key");
              }}
              opciones={PROVEEDORES.map((p) => ({ valor: p, etiqueta: ETIQUETA_PROVEEDOR[p] }))}
            />
          </Campo>
          <Campo etiqueta="Nombre" htmlFor="sus-nombre" ayuda={actual ? undefined : id ? `Id: ${id}` : "De él sale el id de la suscripción."}>
            <Input id="sus-nombre" required maxLength={120} value={nombre} onChange={(e) => setNombre(e.target.value)} />
          </Campo>
        </div>
        {foundry ? (
          <>
            <Campo
              etiqueta="Endpoint"
              htmlFor="sus-endpoint"
              ayuda="Del recurso de Foundry, https://<recurso>.services.ai.azure.com. Cambiarlo obliga a escribir la clave otra vez."
            >
              <Input id="sus-endpoint" type="url" required pattern="^https://.*" maxLength={512} value={endpoint} onChange={(e) => setEndpoint(e.target.value)} />
            </Campo>
            <div className="grid gap-3 sm:grid-cols-3">
              <Campo etiqueta="Proyecto" htmlFor="sus-proyecto" ayuda="Para leer los despliegues.">
                <Input id="sus-proyecto" maxLength={100} value={proyecto} onChange={(e) => setProyecto(e.target.value)} />
              </Campo>
              <Campo etiqueta="Región" htmlFor="sus-region" ayuda="Del recurso, p. ej. swedencentral.">
                <Input id="sus-region" maxLength={40} value={region} onChange={(e) => setRegion(e.target.value)} />
              </Campo>
              <Campo etiqueta="Zona de datos" htmlFor="sus-zona" ayuda="De los SKU DataZone.">
                <Select id="sus-zona" vacio="—" value={zona} onChange={(e) => setZona(e.target.value)} opciones={opcionesDe(["eu", "us"])} />
              </Campo>
            </div>
            <Campo etiqueta="Autenticación" htmlFor="sus-auth">
              <Select
                id="sus-auth"
                value={autenticacion}
                onChange={(e) => setAutenticacion(e.target.value as Autenticacion)}
                opciones={AUTENTICACIONES.map((a) => ({ valor: a, etiqueta: a === "api-key" ? "Clave del recurso" : "Identidad del servidor (Entra ID)" }))}
              />
            </Campo>
          </>
        ) : null}
        {compatible ? (
          <>
            <Aviso tono="aviso">
              Un proveedor compatible solo sirve a repositorios <b>abiertos</b>: no garantiza región ni retención. Los repositorios
              restringidos e internos no lo pueden usar.
            </Aviso>
            <Campo etiqueta="Servicio" htmlFor="sus-servicio" ayuda={conocido ? conocido.nota : "Endpoints propios de la organización."}>
              <Select
                id="sus-servicio"
                value={servicio}
                onChange={(e) => setServicio(e.target.value)}
                opciones={[
                  ...servicios.map((x) => ({ valor: x.id, etiqueta: x.nombre })),
                  { valor: PERSONALIZADO, etiqueta: "Personalizado (otro endpoint)" },
                ]}
              />
            </Campo>
            {propio ? (
              <div className="grid gap-3 sm:grid-cols-2">
                <Campo etiqueta="Endpoint de chat completions" htmlFor="sus-ep-chat" ayuda="URL base de la API de OpenAI, https://…/v1.">
                  <Input id="sus-ep-chat" type="url" pattern="^https://.*" maxLength={512} value={epChat} onChange={(e) => setEpChat(e.target.value)} />
                </Campo>
                <Campo etiqueta="Endpoint de mensajes" htmlFor="sus-ep-msg" ayuda="URL base de la API de Anthropic. Basta con uno de los dos.">
                  <Input id="sus-ep-msg" type="url" pattern="^https://.*" maxLength={512} value={epMensajes} onChange={(e) => setEpMensajes(e.target.value)} />
                </Campo>
              </div>
            ) : null}
            {sinEndpoint ? <p className="text-sm text-peligro">Escribe al menos un endpoint.</p> : null}
          </>
        ) : null}
        {autenticacion === "api-key" || !foundry ? (
          <Campo
            etiqueta="Clave"
            htmlFor="sus-clave"
            ayuda={
              actual?.clave_configurada && !cambiaEndpoint
                ? "Hay una clave guardada. Déjala vacía para conservarla; escribe otra para reemplazarla."
                : "Se cifra al guardar y no se vuelve a mostrar."
            }
          >
            <Input
              id="sus-clave"
              type="password"
              autoComplete="new-password"
              spellCheck={false}
              maxLength={2000}
              value={clave}
              onChange={(e) => setClave(e.target.value)}
            />
          </Campo>
        ) : null}
        {claveFalta ? <p className="text-sm text-peligro">Escribe la clave: {cambiaEndpoint ? "cambiaste el endpoint." : "esta suscripción no tiene ninguna."}</p> : null}
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={habilitada} onChange={(e) => setHabilitada(e.target.checked)} />
          Habilitada (si no, ningún perfil puede usarla)
        </label>
        <ErrorGuardado error={guardar.error} />
        <div className="flex justify-end gap-2">
          <Button variante="secundario" onClick={alCerrar}>
            Cancelar
          </Button>
          <Button type="submit" disabled={guardar.isPending || claveFalta || sinEndpoint || !id}>
            {guardar.isPending ? "Guardando…" : "Guardar"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function FormularioDespliegue({ org, s, alCerrar }: { org: string; s: Suscripcion; alCerrar: () => void }) {
  const compatible = s.proveedor === "compatible";
  const [despliegue, setDespliegue] = useState("");
  const [modelo, setModelo] = useState("");
  const [sku, setSku] = useState("DataZoneStandard");
  const [contexto, setContexto] = useState("");
  const [structured, setStructured] = useState(true);
  const [protocolo, setProtocolo] = useState<ProtocoloCompatible>(s.endpoint ? "openai-chat" : "anthropic-messages");
  const [pEntrada, setPEntrada] = useState("");
  const [pSalida, setPSalida] = useState("");
  const [pCache, setPCache] = useState("");
  // La tarifa va completa o no va: sin ella el costo del modelo cuenta 0 USD en los topes.
  const hayPrecio = pEntrada.trim() !== "" || pSalida.trim() !== "" || pCache.trim() !== "";
  const precioIncompleto = compatible && hayPrecio && (pEntrada.trim() === "" || pSalida.trim() === "");
  const precio: PrecioModelo | null =
    compatible && hayPrecio && !precioIncompleto
      ? { entrada: Number(pEntrada), salida: Number(pSalida), ...(pCache.trim() !== "" ? { cache_lectura: Number(pCache) } : {}) }
      : null;
  const declarar = useGuardar(
    () =>
      suscripciones.declarar(
        org,
        s.id,
        compatible
          ? {
              modelo: modelo.trim(),
              protocolo,
              ...(precio ? { precio_usd_mtok: precio } : {}),
              contexto: numeroOpcional(contexto),
              version: s.version,
            }
          : {
              modelo: modelo.trim(),
              despliegue: despliegue.trim(),
              sku: sku.trim(),
              structured_outputs: structured,
              contexto: numeroOpcional(contexto),
              version: s.version,
            },
      ),
    [claves.suscripciones(org)],
    alCerrar,
  );
  const hayEndpoint = (p: ProtocoloCompatible) => (p === "openai-chat" ? Boolean(s.endpoint) : Boolean(s.endpoint_mensajes));
  const protocolosPosibles = PROTOCOLOS_COMPATIBLE.filter(hayEndpoint);
  return (
    <Dialog abierto alCerrar={alCerrar} titulo={compatible ? "Declarar modelo" : "Declarar despliegue"}>
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (!precioIncompleto) declarar.mutate(undefined);
        }}
      >
        <p className="text-sm text-suave">
          {compatible
            ? `Para un modelo que ${s.nombre} no lista. Queda elegido y disponible para sus perfiles; la salida estructurada la emula Railspec.`
            : `Para un despliegue que la API de Foundry no lista. Queda elegido y disponible para los perfiles de ${s.nombre}.`}
        </p>
        {compatible ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <Campo etiqueta="Modelo" htmlFor="dep-modelo" ayuda="El id que espera la API, p. ej. MiniMax-M3.">
              <Input id="dep-modelo" required maxLength={120} value={modelo} onChange={(e) => setModelo(e.target.value)} />
            </Campo>
            <Campo etiqueta="Protocolo" htmlFor="dep-protocolo" ayuda="La API con la que se llama a este modelo.">
              <Select
                id="dep-protocolo"
                value={protocolo}
                onChange={(e) => setProtocolo(e.target.value as ProtocoloCompatible)}
                opciones={protocolosPosibles.map((p) => ({ valor: p, etiqueta: ETIQUETA_PROTOCOLO[p] }))}
              />
            </Campo>
            <Campo etiqueta="Contexto (tokens, opcional)" htmlFor="dep-ctx">
              <Input id="dep-ctx" type="number" min={1} value={contexto} onChange={(e) => setContexto(e.target.value)} />
            </Campo>
            <div className="grid grid-cols-3 gap-2 sm:col-span-2">
              <Campo etiqueta="USD/Mtok entrada" htmlFor="dep-p-entrada">
                <Input id="dep-p-entrada" type="number" min={0} step="any" value={pEntrada} onChange={(e) => setPEntrada(e.target.value)} />
              </Campo>
              <Campo etiqueta="USD/Mtok salida" htmlFor="dep-p-salida">
                <Input id="dep-p-salida" type="number" min={0} step="any" value={pSalida} onChange={(e) => setPSalida(e.target.value)} />
              </Campo>
              <Campo etiqueta="USD/Mtok caché (opc.)" htmlFor="dep-p-cache">
                <Input id="dep-p-cache" type="number" min={0} step="any" value={pCache} onChange={(e) => setPCache(e.target.value)} />
              </Campo>
            </div>
            {precioIncompleto ? <p className="text-sm text-peligro sm:col-span-2">La tarifa necesita al menos entrada y salida.</p> : null}
            {!hayPrecio ? (
              <p className="text-xs text-suave sm:col-span-2">Sin tarifa, el costo de este modelo cuenta 0 USD en los topes.</p>
            ) : null}
          </div>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-2">
              <Campo etiqueta="Despliegue" htmlFor="dep-nombre" ayuda="El nombre en Foundry.">
                <Input id="dep-nombre" required maxLength={120} value={despliegue} onChange={(e) => setDespliegue(e.target.value)} />
              </Campo>
              <Campo etiqueta="Modelo" htmlFor="dep-modelo" ayuda="Id de catálogo, p. ej. claude-opus-5-5.">
                <Input id="dep-modelo" required maxLength={120} value={modelo} onChange={(e) => setModelo(e.target.value)} />
              </Campo>
              <Campo etiqueta="SKU" htmlFor="dep-sku" ayuda="Define la región: DataZoneStandard, Standard, GlobalStandard…">
                <Input id="dep-sku" required maxLength={60} pattern="[A-Za-z0-9]+" value={sku} onChange={(e) => setSku(e.target.value)} />
              </Campo>
              <Campo etiqueta="Contexto (tokens, opcional)" htmlFor="dep-ctx">
                <Input id="dep-ctx" type="number" min={1} value={contexto} onChange={(e) => setContexto(e.target.value)} />
              </Campo>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={structured} onChange={(e) => setStructured(e.target.checked)} />
              Admite salidas estructuradas
            </label>
          </>
        )}
        <ErrorGuardado error={declarar.error} />
        <div className="flex justify-end gap-2">
          <Button variante="secundario" onClick={alCerrar}>
            Cancelar
          </Button>
          <Button type="submit" disabled={declarar.isPending || precioIncompleto}>
            {declarar.isPending ? "Guardando…" : "Declarar"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function ModelosDeSuscripcion({ org, s, editable }: { org: string; s: Suscripcion; editable: boolean }) {
  const elegidosGuardados = s.modelos.filter((m) => m.seleccionado).map((m) => m.clave);
  // El borrador es de esta versión: cualquier guardado remonta el componente (key en la tarjeta).
  const [elegidos, setElegidos] = useState<string[]>(elegidosGuardados);
  const [declarando, setDeclarando] = useState(false);
  const sinCambios = elegidos.length === elegidosGuardados.length && elegidos.every((c) => elegidosGuardados.includes(c));
  const guardar = useGuardar(
    () => suscripciones.elegir(org, s.id, elegidos, s.version),
    [claves.suscripciones(org)],
    undefined,
    { clave: `suscripcion-modelos:${org}:${s.id}` },
  );
  const retirar = useGuardar((m: ModeloSuscripcion) => suscripciones.retirar(org, s.id, m.clave, s.version), [claves.suscripciones(org)]);
  const alternar = (clave: string, marcado: boolean) => setElegidos((xs) => (marcado ? [...xs, clave] : xs.filter((x) => x !== clave)));
  const foundry = s.proveedor === "foundry";
  const compatible = s.proveedor === "compatible";
  return (
    <div className="flex flex-col gap-2">
      {s.modelos.length === 0 ? (
        <p className="text-sm text-suave">
          Sin modelos todavía. {editable ? "Pulsa «Descubrir modelos»" : "Un admin. de organización puede descubrirlos"}
          {foundry ? " o declara un despliegue a mano." : compatible ? " o declara un modelo a mano." : "."}
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Disponible</TableHead>
              <TableHead>{foundry ? "Despliegue" : "Modelo"}</TableHead>
              {foundry ? <TableHead>Modelo</TableHead> : null}
              <TableHead>{compatible ? "Protocolo / tarifa" : "SKU / región"}</TableHead>
              <TableHead>Capacidades</TableHead>
              <TableHead className="text-right">Contexto</TableHead>
              <TableHead>
                <span className="sr-only">Acciones</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {s.modelos.map((m) => (
              <TableRow key={m.clave} data-modelo={m.clave}>
                <TableCell className="text-center">
                  <input
                    type="checkbox"
                    aria-label={`Disponible ${m.clave}`}
                    disabled={!editable || (m.ausente && !elegidos.includes(m.clave))}
                    checked={elegidos.includes(m.clave)}
                    onChange={(e) => alternar(m.clave, e.target.checked)}
                  />
                </TableCell>
                <TableCell className="font-mono text-xs">
                  {m.clave}
                  {m.ausente ? (
                    <Badge tono="peligro" className="ml-1">
                      ausente
                    </Badge>
                  ) : null}
                  {m.origen === "declarado" ? (
                    <Badge tono="violeta" className="ml-1">
                      declarado
                    </Badge>
                  ) : null}
                </TableCell>
                {foundry ? <TableCell className="font-mono text-xs">{m.modelo}</TableCell> : null}
                <TableCell className="text-xs">
                  {compatible ? (
                    <>
                      {m.protocolo ? ETIQUETA_PROTOCOLO[m.protocolo] : "—"} ·{" "}
                      {m.precio_usd_mtok
                        ? `${m.precio_usd_mtok.entrada}/${m.precio_usd_mtok.salida} USD/Mtok`
                        : "sin tarifa"}
                    </>
                  ) : (
                    <>
                      {m.sku ?? "—"} · {m.region ?? "sin región"}
                    </>
                  )}{" "}
                  {m.restringible ? <Badge tono="exito">restringido/interno</Badge> : <Badge tono="neutro">solo abierto</Badge>}
                </TableCell>
                <TableCell className="text-xs">
                  {m.capacidades.efforts?.join(", ") || "—"}
                  {m.capacidades.structured_outputs ? <Badge tono="info" className="ml-1">structured</Badge> : null}
                </TableCell>
                <TableCell className="text-right text-xs">{numero(m.capacidades.contexto_max_tokens)}</TableCell>
                <TableCell>
                  {editable && m.origen === "declarado" ? (
                    <Button variante="fantasma" tamano="pequeno" aria-label={`Retirar ${m.clave}`} disabled={retirar.isPending} onClick={() => retirar.mutate(m)}>
                      Retirar
                    </Button>
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <ErrorGuardado error={retirar.error} />
      <ErrorGuardado error={guardar.error} />
      <AvisoGuardado guardado={guardar.guardado} texto="Modelos guardados" />
      {editable ? (
        <div className="flex flex-wrap gap-2">
          {s.modelos.length > 0 ? (
            <Button tamano="pequeno" disabled={guardar.isPending || sinCambios} onClick={() => guardar.mutate(undefined)}>
              {guardar.isPending ? "Guardando…" : "Guardar modelos disponibles"}
            </Button>
          ) : null}
          {foundry || compatible ? (
            <Button tamano="pequeno" variante="secundario" onClick={() => setDeclarando(true)}>
              {compatible ? "Declarar modelo" : "Declarar despliegue"}
            </Button>
          ) : null}
        </div>
      ) : null}
      {declarando ? <FormularioDespliegue org={org} s={s} alCerrar={() => setDeclarando(false)} /> : null}
    </div>
  );
}

function TarjetaSuscripcion({
  org,
  s,
  editable,
  alEditar,
  alBorrar,
}: {
  org: string;
  s: Suscripcion;
  editable: boolean;
  alEditar: () => void;
  alBorrar: () => void;
}) {
  const cliente = useQueryClient();
  // Un 502 también deja registrada la lectura (`ultima_lectura`): se recarga haya salido bien o mal.
  const descubrir = useMutation({
    mutationFn: () => suscripciones.descubrir(org, s.id),
    onSettled: () => void cliente.invalidateQueries({ queryKey: claves.suscripciones(org) }),
  });
  const lectura = s.ultima_lectura;
  const usada = s.perfiles.length;
  return (
    <li className="flex flex-col gap-2 rounded-lg border border-borde p-3" data-suscripcion={s.id}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{s.nombre}</span>
          <span className="font-mono text-xs text-suave">{s.id}</span>
          <Badge tono="info">{ETIQUETA_PROVEEDOR[s.proveedor]}</Badge>
          {!s.habilitada ? <Badge tono="aviso">deshabilitada</Badge> : null}
          {s.autenticacion === "identidad-servidor" ? (
            <Badge tono="neutro">identidad del servidor</Badge>
          ) : s.clave_configurada ? (
            <Badge tono="exito">clave guardada</Badge>
          ) : (
            <Badge tono="peligro">sin clave</Badge>
          )}
        </div>
        {editable ? (
          <div className="flex gap-1">
            <Button tamano="pequeno" variante="secundario" disabled={descubrir.isPending} onClick={() => descubrir.mutate()}>
              {descubrir.isPending ? "Descubriendo…" : "Descubrir modelos"}
            </Button>
            <Button tamano="pequeno" variante="secundario" onClick={alEditar}>
              Editar
            </Button>
            <Button tamano="pequeno" variante="fantasma" onClick={alBorrar}>
              Borrar
            </Button>
          </div>
        ) : null}
      </div>
      <p className="text-xs text-suave">
        {s.servicio ? `servicio ${s.servicio} · ` : ""}
        {s.endpoint ? `${s.endpoint} · ` : ""}
        {s.endpoint_mensajes ? `${s.endpoint_mensajes} · ` : ""}
        {s.proyecto ? `proyecto ${s.proyecto} · ` : ""}
        {s.region ? `${s.region} · ` : ""}
        {s.zona_datos ? `zona ${s.zona_datos} · ` : ""}
        {usada > 0 ? `la usan ${s.perfiles.map((p) => (p.workspace ? `${p.nombre}@${p.workspace}` : p.nombre)).join(", ")}` : "ningún perfil la usa"}
      </p>
      {lectura ? (
        <p className="text-xs text-suave">
          Última lectura {fecha(lectura.en)}
          {lectura.por ? ` por ${lectura.por}` : ""}: {lectura.resultado === "ok" ? `${lectura.modelos} modelos` : "falló"}
        </p>
      ) : null}
      {descubrir.isError ? (
        <div role="alert" className="rounded-md bg-rose-50 p-2 text-sm text-rose-900 dark:bg-rose-950/40 dark:text-rose-100">
          {descubrir.error instanceof Error ? descubrir.error.message : "No se pudo leer el proveedor."}
          <span className="block text-xs">Se conserva la elección anterior.</span>
        </div>
      ) : lectura?.resultado === "error" && lectura.error_detalle ? (
        <div role="alert" className="rounded-md bg-rose-50 p-2 text-sm text-rose-900 dark:bg-rose-950/40 dark:text-rose-100">
          {lectura.error_detalle} <span className="text-xs opacity-70">({lectura.error_codigo})</span>
        </div>
      ) : null}
      <ModelosDeSuscripcion key={s.version} org={org} s={s} editable={editable} />
    </li>
  );
}

/** Suscripciones de modelos de la organización (Foundry, Anthropic y compatibles): alta, descubrimiento y elección. */
export function Suscripciones({ org, editable }: { org: string; editable: boolean }) {
  const lista = useQuery({ queryKey: claves.suscripciones(org), queryFn: () => suscripciones.listar(org) });
  // Se guarda el id y no la suscripción: tras un 409 la lista se recarga y el formulario se abre con la versión vigente.
  const [editandoId, setEditandoId] = useState<string | null>(null);
  const editando = editandoId === CLAVE_NUEVA ? CLAVE_NUEVA : (lista.data?.suscripciones.find((x) => x.id === editandoId) ?? null);
  const [borrando, setBorrando] = useState<Suscripcion | null>(null);
  const guardado = useGuardado(claveAviso(org));
  const borrar = useGuardar((s: Suscripcion) => suscripciones.borrar(org, s.id), [claves.suscripciones(org)], () => setBorrando(null));
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between">
        <div>
          <CardTitle>Suscripciones de modelos</CardTitle>
          <CardDescription>
            Conexiones de la organización a Foundry, a Anthropic y a servicios compatibles (OpenCode Zen, MiniMax…). Descubre los modelos de cada una, elige los que quedan disponibles y
            asocia los perfiles a una suscripción.
          </CardDescription>
        </div>
        {editable ? (
          <Button tamano="pequeno" onClick={() => setEditandoId(CLAVE_NUEVA)}>
            Nueva suscripción
          </Button>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <AvisoGuardado guardado={guardado} />
        {lista.isPending ? <Cargando /> : null}
        {lista.isError ? <ErrorVista error={lista.error} reintentar={() => void lista.refetch()} /> : null}
        {lista.isSuccess && !lista.data.cifrado.disponible ? (
          <Aviso tono="aviso">
            El servidor no tiene clave maestra: define <code>{lista.data.cifrado.variable}</code> para poder guardar claves de suscripciones.
            Mientras tanto solo se pueden usar suscripciones con la identidad del servidor.
          </Aviso>
        ) : null}
        {lista.isSuccess && lista.data.suscripciones.length === 0 ? (
          <Vacio titulo="Sin suscripciones">
            <p className="text-sm text-suave">Los perfiles usan los proveedores que fija el servidor por variables de entorno.</p>
          </Vacio>
        ) : null}
        {lista.isSuccess && lista.data.suscripciones.length > 0 ? (
          <ul className="flex flex-col gap-3" aria-label="Suscripciones">
            {lista.data.suscripciones.map((s) => (
              <TarjetaSuscripcion
                key={s.id}
                org={org}
                s={s}
                editable={editable}
                alEditar={() => setEditandoId(s.id)}
                alBorrar={() => setBorrando(s)}
              />
            ))}
          </ul>
        ) : null}
      </CardContent>
      {editando ? (
        <FormularioSuscripcion
          key={editando === CLAVE_NUEVA ? CLAVE_NUEVA : editando.version}
          org={org}
          servicios={lista.data?.servicios_compatibles ?? []}
          {...(editando === CLAVE_NUEVA ? {} : { actual: editando })}
          alCerrar={() => setEditandoId(null)}
        />
      ) : null}
      <Dialog
        abierto={borrando !== null}
        alCerrar={() => setBorrando(null)}
        titulo="Borrar suscripción"
        descripcion={borrando ? `¿Borrar ${borrando.nombre}? Se borra también su clave guardada.` : undefined}
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
