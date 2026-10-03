import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Actor, RegistroAuditoria } from "../../api/tipos";
import { json, montarEnRuta, servidorFalso } from "../../pruebas/servidor";
import { Auditoria, aFiltros } from "./Auditoria";

// Auditoría: filtros que viajan como query (fechas del día completo en UTC), paginación por cursor
// y el detalle de cada registro.

const RUTA = "/orgs/acme/workspaces/cert/auditoria";
const ana: Actor = { tipo: "humano", canal: "consola", github_id: 1, login: "ana" };
const registro = (id: string, o: Partial<RegistroAuditoria> = {}): RegistroAuditoria => ({
  id,
  alcance: { org: "acme", workspace: "cert" },
  evento: "integracion",
  actor: ana,
  en: "2026-09-30T10:00:00Z",
  detalle: {},
  ...o,
});

const montar = () => montarEnRuta(() => <Auditoria />, "/$org/$ws/auditoria", "/acme/cert/auditoria");

describe("aFiltros", () => {
  const vacio = { evento: "", repositorio: "", unidad: "", desde: "", hasta: "" };

  it("solo manda lo rellenado, recortado, con el día completo en UTC", () => {
    expect(aFiltros(vacio)).toEqual({ limite: 50 });
    expect(aFiltros({ evento: "cambio-nivel", repositorio: " api ", unidad: " 0001-x ", desde: "2026-09-01", hasta: "2026-09-30" })).toEqual({
      limite: 50,
      evento: "cambio-nivel",
      repositorio: "api",
      unidad: "0001-x",
      desde: "2026-09-01T00:00:00.000Z",
      hasta: "2026-09-30T23:59:59.999Z",
    });
    expect(aFiltros({ ...vacio, repositorio: "   " })).toEqual({ limite: 50 });
  });
});

describe("auditoría", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lista los registros con actor, canal, repositorio y unidad, y muestra el detalle", async () => {
    servidorFalso({
      [`GET ${RUTA}`]: () => ({
        registros: [
          registro("a1", {
            evento: "llamada-modelo",
            repositorio: "api",
            unidad: "0001-emitir-pdf",
            proveedor: "foundry",
            modelo: "gpt-x",
            region: "westeurope",
            nivel_codigo: "restringido",
            sha256_enviado: "f".repeat(64),
            detalle: { tokens: 120 },
          }),
          registro("a2", { actor: { tipo: "agente", canal: "arnes", agente: "claude-code" } }),
        ],
        cursor_siguiente: null,
      }),
    });
    montar();

    const filas = await screen.findAllByRole("row");
    const fila1 = filas.find((f) => within(f).queryByText("llamada-modelo"))!;
    expect(within(fila1).getByText("ana")).toBeInTheDocument();
    expect(within(fila1).getByText("(consola)")).toBeInTheDocument();
    expect(within(fila1).getByText("api")).toBeInTheDocument();
    expect(within(fila1).getByText("0001-emitir-pdf")).toBeInTheDocument();
    expect(within(fila1).getByText("nivel restringido · foundry · gpt-x · westeurope")).toBeInTheDocument();
    expect(within(fila1).getByText(/sha256 f{16}…/)).toBeInTheDocument();
    expect(within(fila1).getByText("detalle (1)")).toBeInTheDocument();
    const fila2 = filas.find((f) => within(f).queryByText("claude-code"))!;
    expect(within(fila2).getByText("(arnes)")).toBeInTheDocument();
    expect(within(fila2).getAllByText("—")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Cargar más" })).toBeNull();
  });

  it("filtra por evento, repositorio y fechas, y «Limpiar» vuelve a pedir sin filtros", async () => {
    const user = userEvent.setup();
    const s = servidorFalso({ [`GET ${RUTA}`]: () => ({ registros: [], cursor_siguiente: null }) });
    montar();

    expect(await screen.findByText("No hay registros con estos filtros")).toBeInTheDocument();
    expect(Object.fromEntries(s.de("GET", RUTA)[0]!.consulta)).toEqual({ limite: "50" });

    await user.selectOptions(screen.getByLabelText("Evento"), "cambio-nivel");
    await user.type(screen.getByLabelText("Repositorio"), "api");
    await user.type(screen.getByLabelText("Desde"), "2026-09-01");
    await user.type(screen.getByLabelText("Hasta"), "2026-09-30");
    await user.click(screen.getByRole("button", { name: "Filtrar" }));
    await waitFor(() => expect(s.de("GET", RUTA)).toHaveLength(2));
    expect(Object.fromEntries(s.de("GET", RUTA)[1]!.consulta)).toEqual({
      limite: "50",
      evento: "cambio-nivel",
      repositorio: "api",
      desde: "2026-09-01T00:00:00.000Z",
      hasta: "2026-09-30T23:59:59.999Z",
    });

    await user.click(screen.getByRole("button", { name: "Limpiar" }));
    await waitFor(() => expect(s.de("GET", RUTA)).toHaveLength(3));
    expect(Object.fromEntries(s.de("GET", RUTA)[2]!.consulta)).toEqual({ limite: "50" });
    expect(screen.getByLabelText("Repositorio")).toHaveValue("");
  });

  it("«Cargar más» pide la página siguiente con el cursor y desaparece en la última", async () => {
    const user = userEvent.setup();
    const s = servidorFalso({
      [`GET ${RUTA}`]: (l) =>
        l.consulta.get("cursor") === "c2"
          ? { registros: [registro("a2", { unidad: "0002-segunda" })], cursor_siguiente: null }
          : { registros: [registro("a1", { unidad: "0001-primera" })], cursor_siguiente: "c2" },
    });
    montar();

    expect(await screen.findByText("0001-primera")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cargar más" }));
    expect(await screen.findByText("0002-segunda")).toBeInTheDocument();
    expect(screen.getByText("0001-primera")).toBeInTheDocument();
    expect(s.de("GET", RUTA)[1]!.consulta.get("cursor")).toBe("c2");
    expect(screen.queryByRole("button", { name: "Cargar más" })).toBeNull();
  });

  it("un error del servidor se muestra con opción de reintentar", async () => {
    const user = userEvent.setup();
    let falla = true;
    servidorFalso({ [`GET ${RUTA}`]: () => (falla ? json(500, { detalle: "El almacén de auditoría no responde." }) : { registros: [registro("a1", { unidad: "0009-reintento" })], cursor_siguiente: null }) });
    montar();

    expect(await screen.findByText(/El almacén de auditoría no responde/)).toBeInTheDocument();
    falla = false;
    await user.click(screen.getByRole("button", { name: /Reintentar/ }));
    expect(await screen.findByText("0009-reintento")).toBeInTheDocument();
  });
});
