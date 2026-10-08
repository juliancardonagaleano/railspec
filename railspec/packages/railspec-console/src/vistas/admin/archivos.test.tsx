import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LecturaArchivoRepo, PropuestaArchivo, ValidacionArchivo } from "../../api/tipos";
import { json, montarConQuery, servidorFalso, vinculo, type Responder } from "../../pruebas/servidor";
import { Repositorios } from "./Repositorios";

// Editar `contexto.yaml` y `.railspecignore` desde «Repositorios vinculados»: la consola valida y
// propone un PR; nunca escribe en el repositorio. Se simula la API (nunca la red ni GitHub).

const BASE = "/orgs/acme/workspaces/cert/repositorios/api/archivos";
const CONTEXTO = "version: 1\nproveedores: []\n";

const lectura = (o: Partial<LecturaArchivoRepo> = {}): LecturaArchivoRepo => ({
  archivo: "contexto",
  ruta: "contexto.yaml",
  repositorio: "acme/api",
  rama: "main",
  existe: true,
  contenido: CONTEXTO,
  sha: "sha-1",
  plantilla: "# plantilla\nversion: 1\nproveedores: []\n",
  modo: "pr",
  motivo_manual: null,
  ...o,
});

function servidor(extra: Record<string, Responder> = {}) {
  return servidorFalso({
    "GET /orgs/acme/workspaces/cert/repositorios": () => [vinculo("api")],
    [`GET ${BASE}/contexto`]: () => lectura(),
    [`GET ${BASE}/ignore`]: () => lectura({ archivo: "ignore", ruta: ".railspecignore", existe: false, contenido: "", sha: null, plantilla: "# patrones\n" }),
    ...extra,
  });
}

async function abrir(user: ReturnType<typeof userEvent.setup>) {
  montarConQuery(<Repositorios org="acme" ws="cert" puedeEditar />);
  await user.click(await screen.findByRole("button", { name: "Archivos" }));
  return screen.findByRole("dialog", { name: "Archivos de api" });
}

const texto = () => screen.getByRole<HTMLTextAreaElement>("textbox", { name: /contexto\.yaml|\.railspecignore/ });

describe("archivos del repositorio", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("solo quien puede editar ve el botón", async () => {
    servidor();
    montarConQuery(<Repositorios org="acme" ws="cert" puedeEditar={false} />);
    expect(await screen.findByText("api")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Archivos" })).toBeNull();
  });

  it("abre contexto.yaml, propone el cambio con el hash que leyó y enlaza al PR", async () => {
    const user = userEvent.setup();
    const pr: PropuestaArchivo = { modo: "pr", pr_url: "https://github.com/acme/api/pull/7", numero: 7, rama: "railspec/contexto-yaml-abc", diff: "+x" };
    const s = servidor({ [`POST ${BASE}/contexto/proponer`]: () => pr });
    await abrir(user);

    expect(await screen.findByText(/se proponen como un PR contra/)).toBeInTheDocument();
    expect(texto().value).toBe(CONTEXTO);
    const proponer = screen.getByRole("button", { name: /Proponer cambio/ });
    expect(proponer).toBeDisabled(); // sin cambios no hay nada que proponer

    await user.type(texto(), "# nota");
    await user.type(screen.getByLabelText("Motivo (opcional)"), "declarar docs");
    await user.click(proponer);

    const enlace = await screen.findByRole("link", { name: "#7" });
    expect(enlace).toHaveAttribute("href", "https://github.com/acme/api/pull/7");
    expect(s.de("POST", `${BASE}/contexto/proponer`)[0]!.cuerpo).toEqual({
      contenido: CONTEXTO + "# nota",
      sha_base: "sha-1",
      motivo: "declarar docs",
    });
    expect(s.noSimuladas).toEqual([]);
  });

  it("valida y muestra errores con su línea y avisos", async () => {
    const user = userEvent.setup();
    const resultado: ValidacionArchivo = {
      ok: false,
      errores: [{ linea: 3, mensaje: "proveedores[0].url: debe ser https" }],
      avisos: [{ linea: null, mensaje: "el host no está permitido" }],
    };
    const s = servidor({ [`POST ${BASE}/contexto/validar`]: () => resultado });
    await abrir(user);
    await screen.findByText(/se proponen como un PR/);
    await user.type(texto(), "x");
    await user.click(screen.getByRole("button", { name: "Validar" }));

    expect(await screen.findByText(/línea 3: proveedores\[0\]\.url: debe ser https/)).toBeInTheDocument();
    expect(screen.getByText("el host no está permitido")).toBeInTheDocument();
    expect(s.de("POST", `${BASE}/contexto/validar`)[0]!.cuerpo).toEqual({ contenido: CONTEXTO + "x" });
    // Editar descarta un resultado que ya no corresponde.
    await user.type(texto(), "y");
    expect(screen.queryByText(/línea 3/)).toBeNull();
  });

  it("un archivo inválido que rechaza el servidor (422) muestra sus líneas y no abre PR", async () => {
    const user = userEvent.setup();
    servidor({
      [`POST ${BASE}/contexto/proponer`]: () =>
        json(422, { detalle: "contexto.yaml no es válido", errores: [{ ruta: "línea 2", mensaje: "clave repetida: version" }] }),
    });
    await abrir(user);
    await screen.findByText(/se proponen como un PR/);
    await user.type(texto(), "version: 2");
    await user.click(screen.getByRole("button", { name: /Proponer cambio/ }));
    expect(await screen.findByText(/línea 2: clave repetida: version/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /#/ })).toBeNull();
  });

  it("si la App no puede abrir PR, lo dice, pide los permisos y deja copiar el archivo con su diff", async () => {
    const user = userEvent.setup();
    const escribir = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: escribir }, configurable: true });
    const motivo = "la instalación de la App no tiene contenido:escritura y pull requests:escritura";
    servidor({
      [`GET ${BASE}/contexto`]: () => lectura({ modo: "manual", motivo_manual: motivo }),
      [`POST ${BASE}/contexto/proponer`]: () => ({
        modo: "manual",
        motivo_manual: motivo,
        diff: "--- a/contexto.yaml\n+++ b/contexto.yaml\n+# nota\n",
        contenido: CONTEXTO + "# nota\n",
        ruta: "contexto.yaml",
      }),
    });
    await abrir(user);
    expect(await screen.findByText("Edición manual.")).toBeInTheDocument();
    expect(screen.getAllByText(/contenido: escritura/).length).toBeGreaterThan(0);

    await user.type(texto(), "# nota");
    await user.click(screen.getByRole("button", { name: "Ver diff" }));
    expect(await screen.findByText("No se abrió ningún PR: aplica el cambio a mano.")).toBeInTheDocument();
    expect(screen.getByLabelText("Diff")).toHaveTextContent("+# nota");
    await user.click(screen.getByRole("button", { name: "Copiar contexto.yaml" }));
    expect(escribir).toHaveBeenCalledWith(CONTEXTO + "# nota\n");
    expect(await screen.findByRole("button", { name: "Copiado" })).toBeInTheDocument();
  });

  it("un archivo que aún no existe parte de la plantilla y lo avisa", async () => {
    const user = userEvent.setup();
    servidor();
    await abrir(user);
    await screen.findByText(/se proponen como un PR/);
    await user.selectOptions(screen.getByLabelText("Archivo"), "ignore");
    expect(await screen.findByText(/aún no existe en la rama/)).toBeInTheDocument();
    expect(texto().value).toBe("# patrones\n");
  });

  it("un 409 ofrece cargar la versión de la rama y el editor se rehace con ella", async () => {
    const user = userEvent.setup();
    let version = 1;
    servidor({
      [`GET ${BASE}/contexto`]: () => (version === 1 ? lectura() : lectura({ contenido: "version: 1\n# otro\n", sha: "sha-2" })),
      [`POST ${BASE}/contexto/proponer`]: () => {
        version = 2;
        return json(409, { detalle: "contexto.yaml cambió en main desde que lo abriste" });
      },
    });
    await abrir(user);
    await screen.findByText(/se proponen como un PR/);
    await user.type(texto(), "mío");
    await user.click(screen.getByRole("button", { name: /Proponer cambio/ }));

    await user.click(await screen.findByRole("button", { name: /Cargar la versión de la rama/ }));
    await waitFor(() => expect(texto().value).toBe("version: 1\n# otro\n"));
    expect(screen.queryByRole("button", { name: /Cargar la versión de la rama/ })).toBeNull();
  });

  it("un fallo al leer se muestra y se puede reintentar", async () => {
    const user = userEvent.setup();
    let llamadas = 0;
    servidor({
      [`GET ${BASE}/contexto`]: () => (++llamadas === 1 ? json(502, { detalle: "GitHub respondió 500 al leer contexto.yaml" }) : lectura()),
    });
    await abrir(user);
    expect(await screen.findByText(/GitHub respondió 500/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText(/se proponen como un PR/)).toBeInTheDocument();
  });
});
