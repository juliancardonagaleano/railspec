import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ClaveMetricas, Operacion as DatosOperacion } from "../../api/tipos";
import { json, montarEnRuta, servidorFalso, yo } from "../../pruebas/servidor";
import { Operacion, porGrupo, tiempoArriba } from "./Operacion";

// Operación del servidor: estado en vivo y claves de /metrics por origen (solo administración de plataforma).

const datos = (o: Partial<DatosOperacion> = {}): DatosOperacion => ({
  version: "0.1.0",
  esquema: { codigo: 4, almacenado: 4 },
  sondas: [{ nombre: "postgres", estado: "ok" }],
  inicio: "2026-10-09T10:00:00Z",
  ahora: "2026-10-09T13:25:00Z",
  peticiones: [
    { grupo: "mcp", metodo: "POST", estado: "2xx", total: 40 },
    { grupo: "mcp", metodo: "POST", estado: "5xx", total: 10 },
    { grupo: "sondas", metodo: "GET", estado: "2xx", total: 7 },
  ],
  token_entorno: false,
  ...o,
});

const clave = (o: Partial<ClaveMetricas> = {}): ClaveMetricas => ({
  id: "k1",
  nombre: "Grafana",
  prefijo: "rsm1.AbCdEf",
  creada_por: "julian",
  creada_en: "2026-10-09T10:00:00Z",
  ultimo_uso: null,
  revocada_en: null,
  revocada_por: null,
  activa: true,
  ...o,
});

const admin = () => ({ ...yo("org-admin"), plataforma_admin: true });

describe("operación del servidor", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("suma las peticiones por superficie y da el tiempo arriba legible", () => {
    expect(porGrupo(datos().peticiones)).toEqual([
      { grupo: "mcp", ok: 40, cliente: 0, servidor: 10, total: 50 },
      { grupo: "sondas", ok: 7, cliente: 0, servidor: 0, total: 7 },
    ]);
    expect(tiempoArriba("2026-10-09T10:00:00Z", "2026-10-09T13:25:00Z")).toBe("3 h 25 min");
    expect(tiempoArriba("2026-10-07T10:00:00Z", "2026-10-09T14:00:00Z")).toBe("2 d 4 h");
    expect(tiempoArriba("2026-10-09T10:00:00Z", "2026-10-09T10:08:59Z")).toBe("8 min");
  });

  it("muestra sondas, esquema, peticiones y avisa de lo que falla", async () => {
    servidorFalso({
      "GET /yo": admin,
      "GET /operacion": () =>
        datos({ sondas: [{ nombre: "postgres", estado: "sin respuesta" }], esquema: { codigo: 5, almacenado: 4 }, token_entorno: true }),
      "GET /metricas/claves": () => [],
    });
    montarEnRuta(() => <Operacion />, "/operacion", "/operacion");

    expect(await screen.findByText("postgres: sin respuesta")).toBeInTheDocument();
    expect(screen.getByText(/No responde: postgres/)).toBeInTheDocument();
    expect(screen.getByText("código 5, base 4")).toBeInTheDocument();
    expect(screen.getByText(/3 h 25 min/)).toBeInTheDocument();
    const fila = screen.getByText("MCP (arnés)").closest("tr")!;
    expect(within(fila).getByText("20 %")).toBeInTheDocument();
    expect(screen.getByText(/RAILSPEC_METRICAS_TOKEN también está definido/)).toBeInTheDocument();
    expect(await screen.findByText(/No hay claves/)).toBeInTheDocument();
  });

  it("crear una clave la muestra una sola vez y la lista la recoge", async () => {
    const user = userEvent.setup();
    const lista: ClaveMetricas[] = [];
    const s = servidorFalso({
      "GET /yo": admin,
      "GET /operacion": () => datos(),
      "GET /metricas/claves": () => lista,
      "POST /metricas/claves": (l) => {
        lista.unshift(clave({ nombre: l.cuerpo.nombre }));
        return json(201, { clave: lista[0], secreto: "rsm1.AbCdEf-secreto-completo" });
      },
    });
    montarEnRuta(() => <Operacion />, "/operacion", "/operacion");

    await user.type(await screen.findByLabelText("Origen"), "  Grafana ");
    await user.click(screen.getByRole("button", { name: "Crear clave" }));

    expect(await screen.findByLabelText("Clave nueva")).toHaveTextContent("rsm1.AbCdEf-secreto-completo");
    expect(s.de("POST", "/metricas/claves")[0]!.cuerpo).toEqual({ nombre: "Grafana" });
    const fila = (await screen.findByText("rsm1.AbCdEf…")).closest("tr")!;
    expect(within(fila).getByText("Grafana")).toBeInTheDocument();
    expect(within(fila).getByText("Nunca")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Ya la guardé" }));
    expect(screen.queryByLabelText("Clave nueva")).toBeNull();
    expect(screen.queryByText(/secreto-completo/)).toBeNull();
  });

  it("un nombre repetido explica el 409 sin crear nada", async () => {
    const user = userEvent.setup();
    servidorFalso({
      "GET /yo": admin,
      "GET /operacion": () => datos(),
      "GET /metricas/claves": () => [clave()],
      "POST /metricas/claves": () => json(409, { detalle: "ya hay una clave activa llamada «Grafana»: revócala o usa otro nombre" }),
    });
    montarEnRuta(() => <Operacion />, "/operacion", "/operacion");

    await user.type(await screen.findByLabelText("Origen"), "Grafana");
    await user.click(screen.getByRole("button", { name: "Crear clave" }));
    expect(await screen.findByText(/ya hay una clave activa llamada «Grafana»/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Clave nueva")).toBeNull();
  });

  it("revocar pide confirmación y deja la clave como revocada", async () => {
    const user = userEvent.setup();
    let vigente = clave({ ultimo_uso: "2026-10-09T12:00:00Z" });
    const s = servidorFalso({
      "GET /yo": admin,
      "GET /operacion": () => datos(),
      "GET /metricas/claves": () => [vigente],
      "DELETE /metricas/claves/k1": () => {
        vigente = { ...vigente, activa: false, revocada_en: "2026-10-09T13:00:00Z", revocada_por: "julian" };
        return vigente;
      },
    });
    montarEnRuta(() => <Operacion />, "/operacion", "/operacion");

    await user.click(await screen.findByRole("button", { name: "Revocar Grafana" }));
    expect(s.de("DELETE", "/metricas/claves/k1")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Confirmar revocación" }));

    expect(await screen.findByText("Revocada")).toBeInTheDocument();
    expect(s.de("DELETE", "/metricas/claves/k1")).toHaveLength(1);
    await waitFor(() => expect(screen.queryByRole("button", { name: /Revocar/ })).toBeNull());
  });

  it("sin administración de plataforma no pide nada al servidor", async () => {
    const s = servidorFalso({ "GET /yo": () => yo("org-admin") });
    montarEnRuta(() => <Operacion />, "/operacion", "/operacion");

    expect(await screen.findByText(/Solo quien administra la plataforma/)).toBeInTheDocument();
    expect(s.de("GET", "/operacion")).toHaveLength(0);
    expect(s.de("GET", "/metricas/claves")).toHaveLength(0);
  });
});
