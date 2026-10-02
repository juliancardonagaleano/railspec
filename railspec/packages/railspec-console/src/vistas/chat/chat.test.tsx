import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useId } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Conversacion, Insumo } from "../../chat/tipos";
import { guardarUltimaConversacion, leerUltimaConversacion } from "../../lib/conversacionChat";
import type { PropsChat } from "./cargador";
import { MARGEN_RENOVACION_MS, MINIMO_RENOVACION_MS, msHastaRenovar } from "./token";

// El chat real habla con /v1/chat; aquí se sustituye por uno que expone las props que recibe del shell.
const recibidas: PropsChat[] = [];
/** Un id por instancia montada del chat falso: cambia cuando el shell lo remonta. */
const instancias: string[] = [];
const INSUMO: Insumo = {
  formato: "railspec.insumo/v1",
  id: "5f0f3c52-7d9b-4a4b-8f6e-0c1d2e3f4a5b",
  alcance: { org: "acme", workspace: "cert" },
  repositorios: [
    { repositorio: "web", rol: "transversal", base_commit: "b".repeat(40) },
    { repositorio: "api", rol: "primario", base_commit: "a".repeat(40) },
  ],
  autor: {},
  creado_en: "2026-10-02T10:00:00Z",
  nivel_efectivo: "restringido",
  objetivo: "Emitir certificados en PDF",
  hallazgos: [],
  preguntas_abiertas: [],
  restricciones: ["sin tocar el módulo de firma"],
  veredicto_gate: { version_gate: "1", permitido: true, reglas: [], evaluado_en: "2026-10-02T10:00:00Z" },
  sha256: "0".repeat(64),
};

vi.mock("./cargador", () => ({
  ChatPerezoso: (props: PropsChat) => {
    recibidas.push(props);
    const instancia = useId();
    if (instancias.at(-1) !== instancia) instancias.push(instancia);
    return (
      <div>
        <p>chat falso</p>
        <button onClick={() => props.alCrearConversacion?.("11111111-2222-4333-8444-555555555555")}>crear</button>
        <button onClick={() => props.alConversacionNoDisponible?.()}>no disponible</button>
        <button onClick={() => props.alNuevaConversacion?.()}>nueva</button>
        {props.alCrearUnidad ? <button onClick={() => props.alCrearUnidad?.(INSUMO)}>unidad desde insumo</button> : null}
      </div>
    );
  },
}));

const CONV_A = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const CONV_B = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff";

const conversacion = (id: string, repositorios: string[], creada_en: string): Conversacion => ({
  id,
  alcance: { org: "acme", workspace: "cert" },
  repositorios,
  autor: {},
  nivel_efectivo: "restringido",
  creada_en,
  expira_en: "2026-10-05T10:00:00Z",
  consumo_fuga: { caracteres: 0, tope: 1000 },
  consumo_fuga_usuario: { caracteres: 0, tope: 5000 },
  bloqueos: 0,
  limitada: false,
});
// Recientes primero, como las entrega el servidor.
const LISTA = [conversacion(CONV_B, ["web"], "2026-10-02T12:00:00Z"), conversacion(CONV_A, ["api", "web"], "2026-10-02T10:00:00Z")];

const yo = (rol: string) => ({
  login: "ana",
  github_id: 1,
  plataforma_admin: false,
  organizaciones: [{ id: "acme", nombre: "ACME", rol: null, workspaces: [{ workspace: "cert", nombre: "Certificados", rol }] }],
});

const vinculo = (repositorio: string, nivel: string) => ({
  alcance: { org: "acme", workspace: "cert", repositorio },
  url: `https://github.com/acme/${repositorio}`,
  rol: "primario",
  rama_por_defecto: "main",
  nivel_codigo: nivel,
});

function simular(rol: string, extra: (url: string, init?: RequestInit) => Response | undefined = () => undefined) {
  const llamadas: { url: string; init?: RequestInit }[] = [];
  const json = (x: unknown, status = 200) => new Response(JSON.stringify(x), { status });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      llamadas.push({ url, ...(init ? { init } : {}) });
      const propia = extra(url, init);
      if (propia) return propia;
      if (url === "/consola/api/yo") return json(yo(rol));
      if (url.startsWith("/v1/chat/conversaciones?")) return json({ conversaciones: [] });
      if (url === "/consola/api/auth/token") return json({ token: "rsc1.abc", expira_en: new Date(Date.now() + 60 * 60 * 1000).toISOString() });
      if (url === "/consola/api/orgs/acme/workspaces/cert/repositorios") return json([vinculo("api", "restringido"), vinculo("web", "interno")]);
      if (url === "/consola/api/orgs/acme/workspaces/cert/grafo/repositorios")
        return json([
          { repositorio: "api", nivel_codigo: "restringido", rol: "primario", commit: "c".repeat(40) },
          { repositorio: "web", nivel_codigo: "interno", rol: "transversal", commit: null },
        ]);
      return json({ detalle: "no" }, 404);
    }),
  );
  return llamadas;
}

async function abrirChat() {
  window.history.pushState({}, "", "/consola/acme/cert/chat");
  const { router, clienteQuery } = await import("../../router");
  render(
    <QueryClientProvider client={clienteQuery}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("chat falso")).toBeInTheDocument();
}

const ultimas = () => recibidas[recibidas.length - 1]!;

describe("ruta del chat", () => {
  beforeEach(() => {
    vi.resetModules();
    recibidas.length = 0;
    instancias.length = 0;
    window.localStorage.clear();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("ofrece al chat los repositorios vinculados y no retoma nada si no hay conversación guardada", async () => {
    simular("lector");
    await abrirChat();
    expect(ultimas().repositoriosDisponibles).toEqual([
      { id: "api", nivel: "restringido" },
      { id: "web", nivel: "interno" },
    ]);
    expect(ultimas().conversacionId).toBeUndefined();
    expect(ultimas().token).toBe("rsc1.abc");
    // Sin tarjeta de «no disponible»: el chat es parte de la consola.
    expect(screen.queryByText(/aún no está disponible/)).toBeNull();
  });

  it("guarda la conversación creada y la retoma al volver", async () => {
    simular("lector");
    await abrirChat();
    await userEvent.click(screen.getByRole("button", { name: "crear" }));
    expect(leerUltimaConversacion("acme", "cert")).toBe("11111111-2222-4333-8444-555555555555");
  });

  it("retoma la última conversación guardada", async () => {
    guardarUltimaConversacion("acme", "cert", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee");
    simular("lector");
    await abrirChat();
    expect(ultimas().conversacionId).toBe("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee");
  });

  it("si la conversación ya no existe la olvida, avisa y vuelve a pedir repositorios", async () => {
    guardarUltimaConversacion("acme", "cert", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee");
    simular("lector");
    await abrirChat();
    await userEvent.click(screen.getByRole("button", { name: "no disponible" }));
    expect(leerUltimaConversacion("acme", "cert")).toBeUndefined();
    expect(await screen.findByText(/La conversación anterior ya no está disponible/)).toBeInTheDocument();
    await waitFor(() => expect(ultimas().conversacionId).toBeUndefined());
  });

  it("«nueva conversación» olvida la guardada", async () => {
    guardarUltimaConversacion("acme", "cert", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee");
    simular("lector");
    await abrirChat();
    await userEvent.click(screen.getByRole("button", { name: "nueva" }));
    expect(leerUltimaConversacion("acme", "cert")).toBeUndefined();
    await waitFor(() => expect(ultimas().conversacionId).toBeUndefined());
  });

  it("sin conversaciones no ofrece el selector", async () => {
    simular("lector");
    await abrirChat();
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([u]) => String(u).startsWith("/v1/chat/conversaciones?"))).toBe(true));
    expect(screen.queryByLabelText("Conversación")).toBeNull();
  });

  it("lista las conversaciones de la persona con el token del chat y marca la que se retoma", async () => {
    guardarUltimaConversacion("acme", "cert", CONV_A);
    const llamadas = simular("lector", (url) => (url.startsWith("/v1/chat/conversaciones?") ? new Response(JSON.stringify({ conversaciones: LISTA })) : undefined));
    await abrirChat();

    const selector = await screen.findByLabelText("Conversación");
    const opciones = within(selector).getAllByRole("option");
    expect(opciones.map((o) => o.textContent)).toEqual([
      "Nueva conversación",
      expect.stringMatching(/ · web$/),
      expect.stringMatching(/ · api, web$/),
    ]);
    expect(selector).toHaveValue(CONV_A);
    const lista = llamadas.find((l) => l.url.startsWith("/v1/chat/conversaciones?"))!;
    expect(lista.url).toBe("/v1/chat/conversaciones?org=acme&workspace=cert");
    expect(new Headers(lista.init?.headers).get("Authorization")).toBe("Bearer rsc1.abc");
  });

  it("elegir otra conversación la abre en el chat, la guarda como última y no mezcla con la anterior", async () => {
    guardarUltimaConversacion("acme", "cert", CONV_A);
    simular("lector", (url) => (url.startsWith("/v1/chat/conversaciones?") ? new Response(JSON.stringify({ conversaciones: LISTA })) : undefined));
    await abrirChat();
    expect(ultimas().conversacionId).toBe(CONV_A);
    const antes = instancias.length;

    await userEvent.selectOptions(await screen.findByLabelText("Conversación"), CONV_B);

    await waitFor(() => expect(ultimas().conversacionId).toBe(CONV_B));
    expect(leerUltimaConversacion("acme", "cert")).toBe(CONV_B);
    expect(screen.getByLabelText("Conversación")).toHaveValue(CONV_B);
    // Se remontó el chat: dentro de él «nueva conversación» no se deshace y arrastraría el estado de la anterior.
    expect(instancias.length).toBe(antes + 1);
  });

  it("elegir «Nueva conversación» olvida la guardada y vuelve a pedir repositorios", async () => {
    guardarUltimaConversacion("acme", "cert", CONV_A);
    simular("lector", (url) => (url.startsWith("/v1/chat/conversaciones?") ? new Response(JSON.stringify({ conversaciones: LISTA })) : undefined));
    await abrirChat();
    const antes = instancias.length;

    await userEvent.selectOptions(await screen.findByLabelText("Conversación"), "Nueva conversación");

    await waitFor(() => expect(ultimas().conversacionId).toBeUndefined());
    expect(leerUltimaConversacion("acme", "cert")).toBeUndefined();
    expect(screen.getByLabelText("Conversación")).toHaveValue("");
    expect(instancias.length).toBe(antes + 1);
  });

  it("una conversación recién creada entra a la lista y queda elegida", async () => {
    let creadas: Conversacion[] = [];
    simular("lector", (url) => (url.startsWith("/v1/chat/conversaciones?") ? new Response(JSON.stringify({ conversaciones: creadas })) : undefined));
    await abrirChat();
    expect(screen.queryByLabelText("Conversación")).toBeNull();

    creadas = [conversacion("11111111-2222-4333-8444-555555555555", ["api"], "2026-10-02T13:00:00Z")];
    await userEvent.click(screen.getByRole("button", { name: "crear" }));

    expect(await screen.findByLabelText("Conversación")).toHaveValue("11111111-2222-4333-8444-555555555555");
  });

  it("si la conversación elegida ya no existe avisa y la saca de la lista", async () => {
    let lista = LISTA;
    simular("lector", (url) => (url.startsWith("/v1/chat/conversaciones?") ? new Response(JSON.stringify({ conversaciones: lista })) : undefined));
    await abrirChat();
    await userEvent.selectOptions(await screen.findByLabelText("Conversación"), CONV_A);
    await waitFor(() => expect(ultimas().conversacionId).toBe(CONV_A));

    lista = [LISTA[0]!];
    await userEvent.click(screen.getByRole("button", { name: "no disponible" }));

    expect(await screen.findByText(/La conversación anterior ya no está disponible/)).toBeInTheDocument();
    await waitFor(() => expect(within(screen.getByLabelText("Conversación")).getAllByRole("option")).toHaveLength(2));
    expect(screen.getByLabelText("Conversación")).toHaveValue("");
  });

  it("si no puede listar lo dice, deja usar el chat y permite reintentar", async () => {
    let falla = true;
    simular("lector", (url) => {
      if (!url.startsWith("/v1/chat/conversaciones?")) return undefined;
      return falla ? new Response(JSON.stringify({ detalle: "sin servicio" }), { status: 503 }) : new Response(JSON.stringify({ conversaciones: LISTA }));
    });
    await abrirChat();

    expect(await screen.findByText(/No se pudo listar tus conversaciones/)).toBeInTheDocument();
    expect(screen.getByText("chat falso")).toBeInTheDocument();
    expect(screen.queryByLabelText("Conversación")).toBeNull();

    falla = false;
    await userEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByLabelText("Conversación")).toBeInTheDocument();
    expect(screen.queryByText(/No se pudo listar tus conversaciones/)).toBeNull();
  });

  it("un lector no puede crear unidades desde el insumo", async () => {
    simular("lector");
    await abrirChat();
    expect(screen.queryByRole("button", { name: "unidad desde insumo" })).toBeNull();
    expect(ultimas().alCrearUnidad).toBeUndefined();
  });

  it("un desarrollador arranca la unidad con el insumo precargado y va a su página", async () => {
    let cuerpo: unknown;
    const llamadas = simular("desarrollador", (url, init) => {
      if (url === "/consola/api/tools/unit.start") {
        cuerpo = JSON.parse(String(init?.body));
        return new Response(
          JSON.stringify({ estado: { unidad: { org: "acme", workspace: "cert", unidad: "0007-emitir-certificados" } } }),
          { status: 200 },
        );
      }
      return undefined;
    });
    await abrirChat();
    await userEvent.click(screen.getByRole("button", { name: "unidad desde insumo" }));

    const dialogo = await screen.findByRole("dialog", { name: "Nueva unidad" });
    expect(dialogo).toBeInTheDocument();
    expect(screen.getByLabelText("Título")).toHaveValue("Emitir certificados en PDF");
    expect(screen.getByLabelText("Pedido")).toHaveValue("Emitir certificados en PDF\n\nRestricciones:\n- sin tocar el módulo de firma");
    expect(screen.getByLabelText("Insumos del chat (ids)")).toHaveValue(INSUMO.id);
    // El repositorio primario va primero aunque el insumo lo traiga después; la rama sale del vínculo.
    expect(screen.getByLabelText("Repositorio 1")).toHaveValue("api");
    expect(screen.getByLabelText("Commit base 1")).toHaveValue("a".repeat(40));
    await waitFor(() => expect(screen.getByLabelText("Rama 1")).toHaveValue("main"));
    expect(screen.getByLabelText("Repositorio 2")).toHaveValue("web");

    await userEvent.click(screen.getByRole("button", { name: "Arrancar unidad" }));
    await waitFor(() => expect(cuerpo).toBeDefined());
    expect(cuerpo).toEqual({
      alcance: { org: "acme", workspace: "cert" },
      repositorios: [
        { repositorio: "api", rama: "main", base_commit: "a".repeat(40) },
        { repositorio: "web", rama: "main", base_commit: "b".repeat(40) },
      ],
      titulo: "Emitir certificados en PDF",
      pedido: "Emitir certificados en PDF\n\nRestricciones:\n- sin tocar el módulo de firma",
      insumos: [INSUMO.id],
      version_contrato_cliente: "1.4",
    });
    expect(llamadas.find((l) => l.url === "/consola/api/tools/unit.start")?.init?.method).toBe("POST");
    await waitFor(() => expect(window.location.pathname).toBe("/consola/acme/cert/unidades/0007-emitir-certificados"));
  });

  it("no envía la unidad si falta algo y lo explica", async () => {
    simular("desarrollador");
    await abrirChat();
    await userEvent.click(screen.getByRole("button", { name: "unidad desde insumo" }));
    await screen.findByRole("dialog", { name: "Nueva unidad" });
    await userEvent.clear(screen.getByLabelText("Commit base 2"));
    await userEvent.type(screen.getByLabelText("Commit base 2"), "abc123");
    await userEvent.click(screen.getByRole("button", { name: "Arrancar unidad" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("El commit base de web debe ser un sha completo");
    expect(vi.mocked(fetch).mock.calls.some(([u]) => u === "/consola/api/tools/unit.start")).toBe(false);
  });
});

describe("renovación del token del chat", () => {
  const ahora = Date.parse("2026-10-01T10:00:00Z");

  it("renueva 5 minutos antes de expirar", () => {
    expect(msHastaRenovar("2026-10-01T11:00:00Z", ahora)).toBe(60 * 60 * 1000 - MARGEN_RENOVACION_MS);
  });

  it("si ya expiró o la fecha no es válida, renueva ya", () => {
    expect(msHastaRenovar("2026-10-01T09:59:00Z", ahora)).toBe(0);
    expect(msHastaRenovar("no-es-fecha", ahora)).toBe(0);
  });

  it("con vida menor que el margen renueva a mitad de camino sin bajar del mínimo", () => {
    expect(msHastaRenovar("2026-10-01T10:04:00Z", ahora)).toBe(2 * 60 * 1000);
    expect(msHastaRenovar("2026-10-01T10:00:40Z", ahora)).toBe(MINIMO_RENOVACION_MS);
    expect(msHastaRenovar("2026-10-01T10:00:10Z", ahora)).toBe(10 * 1000);
  });
});
