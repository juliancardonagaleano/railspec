import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { construirUrl, ErrorApi, esSinContrato14, fijarRedireccionLogin, invocarTool, pedir, rutaActualSpa, urlLogin } from "./cliente";

function respuesta(status: number, cuerpo?: unknown): Response {
  return new Response(cuerpo === undefined ? null : JSON.stringify(cuerpo), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("cliente de la API", () => {
  const fetchFalso = vi.fn<typeof fetch>();
  const redirigir = vi.fn();

  beforeEach(() => {
    fetchFalso.mockReset();
    redirigir.mockReset();
    vi.stubGlobal("fetch", fetchFalso);
    fijarRedireccionLogin(redirigir);
  });
  afterEach(() => vi.unstubAllGlobals());

  const cabeceras = (llamada = 0) => (fetchFalso.mock.calls[llamada]?.[1]?.headers ?? {}) as Record<string, string>;

  it("GET va con la cookie de sesión y sin la cabecera anti-CSRF", async () => {
    fetchFalso.mockResolvedValue(respuesta(200, { ok: true }));
    await pedir("/yo");
    const [url, init] = fetchFalso.mock.calls[0]!;
    expect(url).toBe("/consola/api/yo");
    expect(init?.credentials).toBe("same-origin");
    expect(cabeceras()["X-Railspec-Consola"]).toBeUndefined();
  });

  it("todo lo que no es GET lleva X-Railspec-Consola: 1 y JSON", async () => {
    fetchFalso.mockResolvedValue(respuesta(204));
    await pedir("/orgs/acme/roles/x", { metodo: "DELETE" });
    expect(cabeceras(0)["X-Railspec-Consola"]).toBe("1");

    fetchFalso.mockResolvedValue(respuesta(200, { unidades: [], cursor_siguiente: null }));
    await invocarTool("unit.list", { alcance: { org: "acme", workspace: "ws" } });
    const [url, init] = fetchFalso.mock.calls[1]!;
    expect(url).toBe("/consola/api/tools/unit.list");
    expect(init?.method).toBe("POST");
    expect(cabeceras(1)["X-Railspec-Consola"]).toBe("1");
    expect(cabeceras(1)["Content-Type"]).toBe("application/json");
    expect(JSON.parse(String(init?.body))).toEqual({ alcance: { org: "acme", workspace: "ws" } });
  });

  it("un 204 devuelve null", async () => {
    fetchFalso.mockResolvedValue(respuesta(204));
    await expect(pedir("/auth/salir", { metodo: "POST" })).resolves.toBeNull();
  });

  it("un 401 redirige al login y lanza ErrorApi", async () => {
    fetchFalso.mockResolvedValue(respuesta(401, { detalle: "Sin sesión" }));
    const error = await pedir("/yo").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ErrorApi);
    expect((error as ErrorApi).status).toBe(401);
    expect((error as ErrorApi).detalle).toBe("Sin sesión");
    expect(redirigir).toHaveBeenCalledTimes(1);
  });

  it("con sinRedireccion un 401 no redirige", async () => {
    fetchFalso.mockResolvedValue(respuesta(401, { detalle: "Sin sesión" }));
    await expect(pedir("/auth/config", { sinRedireccion: true })).rejects.toBeInstanceOf(ErrorApi);
    expect(redirigir).not.toHaveBeenCalled();
  });

  it("un ErrorTool conserva su código y la versión del estado", async () => {
    fetchFalso.mockResolvedValue(
      respuesta(409, { codigo: "checkpoint-ya-resuelto", detalle: "Lo resolvió el arnés", version_estado: 7 }),
    );
    const error = (await invocarTool("unit.approve", {}).catch((e: unknown) => e)) as ErrorApi;
    expect(error).toBeInstanceOf(ErrorApi);
    expect(error.status).toBe(409);
    expect(error.codigo).toBe("checkpoint-ya-resuelto");
    expect(error.detalle).toBe("Lo resolvió el arnés");
    expect(error.versionActual).toBe(7);
    expect(redirigir).not.toHaveBeenCalled();
  });

  it("un 422 de la consola trae el detalle y los errores de validación", async () => {
    fetchFalso.mockResolvedValue(respuesta(422, { detalle: "Inválido", errores: [{ ruta: "url", mensaje: "debe ser https" }] }));
    const error = (await pedir("/orgs", { metodo: "POST", cuerpo: {} }).catch((e: unknown) => e)) as ErrorApi;
    expect(error.codigo).toBeUndefined();
    expect(error.errores).toEqual([{ ruta: "url", mensaje: "debe ser https" }]);
  });

  it("un cuerpo de error no JSON no rompe", async () => {
    fetchFalso.mockResolvedValue(new Response("Bad gateway", { status: 502 }));
    const error = (await pedir("/yo").catch((e: unknown) => e)) as ErrorApi;
    expect(error.status).toBe(502);
    expect(error.detalle).toBe("Bad gateway");
  });

  it("construye URLs con consulta omitiendo vacíos", () => {
    expect(construirUrl("/orgs/a/roles", { workspace: undefined })).toBe("/consola/api/orgs/a/roles");
    expect(construirUrl("/x", { workspace: "w s", vacio: "", n: 3 })).toBe("/consola/api/x?workspace=w+s&n=3");
  });

  it("solo un servidor anterior a 1.4 cuenta como sin contrato 1.4", () => {
    // Pydantic rechaza el verbo `impact`/`trace` desconocido: 422 con un único error en `consulta`.
    const verboDesconocido = new ErrorApi(422, {
      detalle: "entrada fuera de contrato",
      errores: [{ ruta: "consulta", mensaje: "Input tag 'impact' found using 'verbo' does not match any of the expected tags" }],
    });
    expect(esSinContrato14(verboDesconocido)).toBe(true);
    // Servidor 1.4: un 422 por un campo concreto no es falta de contrato.
    const campoInvalido = new ErrorApi(422, {
      detalle: "entrada fuera de contrato",
      errores: [{ ruta: "consulta.trace.criterio", mensaje: "String should match pattern '^CA-[0-9]{2,3}$'" }],
    });
    expect(esSinContrato14(campoInvalido)).toBe(false);
    // Servidor 1.4 sin grafo: la tool `graph.query` no está registrada (404 no-encontrado).
    const sinGrafo = new ErrorApi(404, { codigo: "no-encontrado", detalle: "tool graph.query no disponible" });
    expect(esSinContrato14(sinGrafo)).toBe(false);
    expect(esSinContrato14(new ErrorApi(403, { codigo: "fuera-de-alcance", detalle: "sin rol" }))).toBe(false);
    expect(esSinContrato14(new Error("red"))).toBe(false);
  });

  it("la ruta de vuelta es relativa a la SPA", () => {
    window.history.pushState({}, "", "/consola/acme/ws/grafo?simbolo=abc");
    expect(rutaActualSpa()).toBe("/acme/ws/grafo?simbolo=abc");
    expect(urlLogin("/acme/ws")).toBe("/consola/login?volver=%2Facme%2Fws");
  });
});
