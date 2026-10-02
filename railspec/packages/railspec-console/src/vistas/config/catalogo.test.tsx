import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EstadoCatalogo } from "../../api/tipos";
import { Catalogo } from "./Catalogo";

const modelo = {
  org: "acme",
  proveedor: "foundry",
  modelo: "claude-opus-5-5",
  despliegue: "opus-dz",
  hosting: "azure",
  region: "zona-us",
  capacidades: { efforts: ["low"], thinking: true, structured_outputs: true, contexto_max_tokens: 1000000 },
  leido_en: "2026-10-01T10:00:00Z",
};

const estadoBase: EstadoCatalogo = {
  sincronizable: true,
  proveedores: [
    {
      proveedor: "anthropic",
      configurado: true,
      fuentes: ["api"],
      modelos: 0,
      leido_en: null,
      ultimo_intento: null,
      aviso: null,
    },
    {
      proveedor: "foundry",
      configurado: true,
      fuentes: ["declarados", "proyecto"],
      modelos: 1,
      leido_en: "2026-10-01T10:00:00Z",
      ultimo_intento: {
        intento_en: "2026-10-02T09:00:00Z",
        origen: "consola",
        por: "ana",
        resultado: "error",
        reutilizada: false,
        modelos: 1,
        leido_en: "2026-10-01T10:00:00Z",
        error: { codigo: "autenticacion", detalle: "foundry rechazó la credencial del servidor (HTTP 401): revísala o renuévala." },
      },
      aviso: null,
    },
  ],
};

function montar(fetchFalso: ReturnType<typeof vi.fn>, puedeSincronizar = true) {
  vi.stubGlobal("fetch", fetchFalso);
  const cliente = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={cliente}>
      <Catalogo org="acme" puedeSincronizar={puedeSincronizar} />
    </QueryClientProvider>,
  );
}

const json = (x: unknown, status = 200) => new Response(JSON.stringify(x), { status });

function servidor(estado: EstadoCatalogo, sincronizar?: (url: string, init?: RequestInit) => Response) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/consola/api/orgs/acme/catalogo") return json([modelo]);
    if (url === "/consola/api/orgs/acme/catalogo/estado") return json(estado);
    if (url.startsWith("/consola/api/orgs/acme/catalogo/sincronizar") && sincronizar) return sincronizar(url, init);
    return json({ detalle: "no" }, 404);
  });
}

describe("catálogo de modelos", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("muestra el estado por proveedor con el error claro y el catálogo conservado", async () => {
    montar(servidor(estadoBase));
    const lista = await screen.findByRole("list", { name: "Proveedores" });
    const foundry = within(lista).getByText("foundry").closest("li")!;
    expect(within(foundry).getByText("falló la última lectura")).toBeInTheDocument();
    expect(within(foundry).getByRole("alert")).toHaveTextContent("rechazó la credencial del servidor (HTTP 401)");
    expect(within(foundry).getByRole("alert")).toHaveTextContent("Se conserva el catálogo anterior.");
    expect(within(foundry).getByText(/por ana/)).toBeInTheDocument();
    expect(within(foundry).getByText(/despliegues declarados y proyecto de Foundry/)).toBeInTheDocument();
    const anthropic = within(lista).getByText("anthropic").closest("li")!;
    expect(within(anthropic).getByText("sin sincronizar")).toBeInTheDocument();
    expect(await screen.findByText("claude-opus-5-5")).toBeInTheDocument();
  });

  it("sincroniza un solo proveedor con la cabecera anti-CSRF y resume el resultado", async () => {
    const sincronizar = vi.fn((_url: string, _init?: RequestInit) =>
      json({
        resultado: "ok",
        modelos: 1,
        proveedores: [
          { proveedor: "foundry", intento_en: "2026-10-02T10:00:00Z", resultado: "ok", reutilizada: false, modelos: 1, leido_en: "2026-10-02T10:00:00Z", error: null },
        ],
      }),
    );
    montar(servidor(estadoBase, sincronizar));
    await userEvent.click(await screen.findByRole("button", { name: "Sincronizar foundry" }));
    expect(await screen.findByText(/Catálogo sincronizado: 1 modelos\./)).toBeInTheDocument();
    const [url, init] = sincronizar.mock.calls[0]!;
    expect(url).toBe("/consola/api/orgs/acme/catalogo/sincronizar?proveedor=foundry");
    expect((init?.headers as Record<string, string>)["X-Railspec-Consola"]).toBe("1");
    expect(init?.method).toBe("POST");
  });

  it("sincroniza todos sin filtro y avisa de un resultado parcial", async () => {
    const sincronizar = vi.fn((_url: string) =>
      json({
        resultado: "parcial",
        modelos: 1,
        proveedores: [
          { proveedor: "anthropic", intento_en: "x", resultado: "error", reutilizada: false, modelos: 0, leido_en: null, error: { codigo: "red", detalle: "no se pudo conectar" } },
          { proveedor: "foundry", intento_en: "x", resultado: "ok", reutilizada: false, modelos: 1, leido_en: "x", error: null },
        ],
      }),
    );
    montar(servidor(estadoBase, sincronizar));
    await userEvent.click(await screen.findByRole("button", { name: "Sincronizar todos" }));
    expect(await screen.findByText(/Sincronización parcial: anthropic no se pudo leer/)).toBeInTheDocument();
    expect(sincronizar.mock.calls[0]![0]).toBe("/consola/api/orgs/acme/catalogo/sincronizar");
  });

  it("muestra el detalle del servidor cuando todos los proveedores fallan (502)", async () => {
    montar(servidor(estadoBase, () => json({ resultado: "error", detalle: "foundry: foundry no respondió a tiempo: reintenta en unos minutos.", modelos: 1, proveedores: [] }, 502)));
    await userEvent.click(await screen.findByRole("button", { name: "Sincronizar todos" }));
    await waitFor(() => expect(screen.getByText("El proveedor falló")).toBeInTheDocument());
    expect(screen.getByText(/no respondió a tiempo/)).toBeInTheDocument();
  });

  it("sin permiso de org-admin no ofrece sincronizar", async () => {
    montar(servidor(estadoBase), false);
    await screen.findByRole("list", { name: "Proveedores" });
    expect(screen.queryByRole("button", { name: /Sincronizar/ })).toBeNull();
  });

  it("sin proveedores configurados en el servidor lo explica y no ofrece sincronizar", async () => {
    montar(servidor({ sincronizable: false, proveedores: [] }));
    expect(await screen.findByText(/no tiene proveedores de modelos configurados/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Sincronizar/ })).toBeNull();
  });

  it("avisa cuando un proveedor no tiene fuente de catálogo", async () => {
    const estado: EstadoCatalogo = {
      sincronizable: true,
      proveedores: [
        { proveedor: "foundry", configurado: true, fuentes: [], modelos: 0, leido_en: null, ultimo_intento: null, aviso: "Foundry no tiene despliegues declarados ni proyecto en este servidor." },
      ],
    };
    montar(servidor(estado));
    expect(await screen.findByText(/Foundry no tiene despliegues declarados ni proyecto/)).toBeInTheDocument();
  });
});
