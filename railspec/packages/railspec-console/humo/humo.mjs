#!/usr/bin/env node
// Humo de la consola en un navegador real: arranca `railspec-server` en memoria (modo desarrollo, con la
// SPA compilada en `dist/`) y recorre con Chromium el camino de una persona nueva: login con token,
// Inicio, crear organización y workspace, Configuración y Suscripciones (alta, modelo declarado, perfil).
//
//   npm run build && npm run humo
//
// Requisitos: `railspec-server` instalado (pip install -e railspec/packages/railspec-server[motor] más
// `mongomock`, que es el estado en memoria) y Playwright con Chromium (`npm i --no-save playwright` y
// `npx playwright install chromium`, o el que ya traiga el entorno). No hace llamadas a Foundry ni a Anthropic.
//
// Variables: RAILSPEC_HUMO_SERVIDOR (comando del servidor, por defecto `railspec-server`),
// RAILSPEC_HUMO_PLAYWRIGHT (carpeta de `playwright` si no está en node_modules), RAILSPEC_HUMO_SALIDA
// (capturas, por defecto `humo/salida`), RAILSPEC_HUMO_VISIBLE=1 (navegador con ventana).
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { createServer } from "node:net";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { randomBytes } from "node:crypto";

const aqui = dirname(fileURLToPath(import.meta.url));
const paquete = resolve(aqui, "..");
const salida = resolve(process.env.RAILSPEC_HUMO_SALIDA ?? join(aqui, "salida"));
mkdirSync(salida, { recursive: true });

const require = createRequire(join(paquete, "package.json"));
const { chromium } = require(process.env.RAILSPEC_HUMO_PLAYWRIGHT ?? "playwright");

const puertoLibre = () =>
  new Promise((ok, mal) => {
    const s = createServer();
    s.listen(0, "127.0.0.1", () => {
      const { port } = s.address();
      s.close(() => ok(port));
    });
    s.on("error", mal);
  });

async function levantarServidor() {
  const puerto = await puertoLibre();
  const [comando, ...args] = (process.env.RAILSPEC_HUMO_SERVIDOR ?? "railspec-server").split(" ");
  const proceso = spawn(comando, args, {
    env: {
      ...process.env,
      RAILSPEC_PERMITIR_DESARROLLO: "1",
      RAILSPEC_TOKENS_DESARROLLO: "tok-admin=admina:1001,tok-dev=dev:1002",
      RAILSPEC_CONSOLA_ADMINS: "1001",
      RAILSPEC_CONSOLA_DIR: join(paquete, "dist"),
      RAILSPEC_CLAVE_MAESTRA: randomBytes(32).toString("base64"),
      RAILSPEC_HOST: "127.0.0.1",
      RAILSPEC_PUERTO: String(puerto),
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let registro = "";
  proceso.stdout.on("data", (d) => (registro += d));
  proceso.stderr.on("data", (d) => (registro += d));
  const url = `http://127.0.0.1:${puerto}`;
  for (let i = 0; i < 120; i++) {
    if (proceso.exitCode !== null) throw new Error(`el servidor terminó (${proceso.exitCode}):\n${registro}`);
    try {
      if ((await fetch(`${url}/consola/api/auth/config`)).ok) return { url, proceso, registro: () => registro };
    } catch {
      /* aún no escucha */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  proceso.kill();
  throw new Error(`el servidor no respondió:\n${registro}`);
}

const servidor = await levantarServidor();
const navegador = await chromium.launch({ headless: process.env.RAILSPEC_HUMO_VISIBLE !== "1" });
const contexto = await navegador.newContext({ viewport: { width: 1280, height: 800 } });
const pagina = await contexto.newPage();

// Todo lo que el navegador se queja cuenta como fallo: errores de consola, excepciones y respuestas 4xx/5xx
// que el recorrido no haya declarado (`esperados`).
const problemas = [];
const esperados = new Set();
// «Failed to load resource» es el eco de una respuesta 4xx/5xx: ya la cuenta el manejador de `response`.
pagina.on("console", (m) => m.type() === "error" && !m.text().startsWith("Failed to load resource") && problemas.push(`consola: ${m.text()}`));
pagina.setDefaultTimeout(8000);
pagina.on("pageerror", (e) => problemas.push(`excepción: ${e.message}`));
pagina.on("response", (r) => {
  const { pathname } = new URL(r.url());
  if (r.status() >= 400 && !esperados.has(`${r.status()} ${pathname}`)) problemas.push(`HTTP ${r.status()} ${r.request().method()} ${pathname}`);
});

const resultados = [];
async function paso(nombre, fn) {
  const t0 = Date.now();
  try {
    await fn();
    resultados.push({ nombre, ok: true });
    console.log(`  ok   ${nombre} (${Date.now() - t0} ms)`);
  } catch (e) {
    resultados.push({ nombre, ok: false, error: e });
    console.log(`  FALLO ${nombre}\n       ${String(e.message).split("\n").join("\n       ")}`);
    await pagina.screenshot({ path: join(salida, `fallo-${resultados.length}.png`), fullPage: true }).catch(() => undefined);
    throw e;
  }
}
const foto = (n) => pagina.screenshot({ path: join(salida, `${n}.png`), fullPage: true });
const ruta = () => new URL(pagina.url()).pathname + new URL(pagina.url()).search;
const aterriza = (re) => pagina.waitForURL((u) => re.test(u.pathname + u.search), { timeout: 8000 });
const dialogo = () => pagina.getByRole("dialog");

let fallo = null;
try {
  console.log(`humo de la consola contra ${servidor.url}`);

  await paso("sin sesión, /consola/ lleva al login y ofrece solo el token de desarrollo", async () => {
    esperados.add("401 /consola/api/yo");
    await pagina.goto(`${servidor.url}/consola/`);
    await aterriza(/^\/consola\/login\?volver=%2F$/);
    await pagina.getByRole("heading", { name: "Railspec · consola" }).waitFor();
    assert.equal(await pagina.getByRole("button", { name: "Entrar con GitHub" }).count(), 0);
    await foto("01-login");
  });

  await paso("un token desconocido se rechaza con el mensaje del servidor", async () => {
    esperados.add("401 /consola/api/auth/desarrollo");
    await pagina.getByLabel("Token de desarrollo").fill("no-existe");
    await pagina.getByRole("button", { name: "Entrar con token" }).click();
    await pagina.getByRole("alert").getByText("token de desarrollo desconocido").waitFor();
    assert.match(ruta(), /^\/consola\/login/);
  });

  await paso("el token del admin de plataforma entra y Inicio ofrece crear la primera organización", async () => {
    await pagina.getByLabel("Token de desarrollo").fill("tok-admin");
    await pagina.getByRole("button", { name: "Entrar con token" }).click();
    await aterriza(/^\/consola\/?$/);
    await pagina.getByText("Aún no perteneces a ninguna organización").waitFor();
    await pagina.getByRole("link", { name: "Crear una organización" }).waitFor();
    await foto("02-inicio-vacio");
  });

  await paso("crear organización «acme»", async () => {
    await pagina.getByRole("link", { name: "Crear una organización" }).click();
    await aterriza(/^\/consola\/organizaciones$/);
    await pagina.getByRole("button", { name: "Nueva organización" }).click();
    await pagina.getByLabel("Identificador").fill("acme");
    await pagina.getByLabel("Nombre", { exact: true }).fill("Acme Corp");
    await pagina.getByLabel("Región de datos").fill("eu");
    await pagina.getByRole("button", { name: "Guardar" }).click();
    await dialogo().waitFor({ state: "detached" });
    await pagina.getByRole("link", { name: "Acme Corp" }).waitFor();
    await foto("03-organizaciones");
  });

  await paso("Inicio entra a la organización sin workspaces y lleva a administración", async () => {
    // /yo se cachea 60 s: se recarga para ver la organización nueva.
    await pagina.goto(`${servidor.url}/consola/`);
    await aterriza(/^\/consola\/acme$/);
    await pagina.getByText("Esta organización aún no tiene workspaces visibles para ti").waitFor();
    await pagina.getByRole("link", { name: "Ir a administración" }).click();
    await aterriza(/^\/consola\/acme\/administracion$/);
  });

  await paso("crear workspace «cert» y que Inicio redirija a él", async () => {
    await pagina.getByRole("button", { name: "Nuevo workspace" }).click();
    await pagina.getByLabel("Identificador").fill("cert");
    await pagina.getByLabel("Nombre", { exact: true }).fill("Certificados");
    await pagina.getByRole("button", { name: "Guardar" }).click();
    await dialogo().waitFor({ state: "detached" });
    await pagina.getByRole("table").getByText("Certificados").waitFor();
    await foto("04-administracion");
    await pagina.goto(`${servidor.url}/consola/`);
    await aterriza(/^\/consola\/acme\/cert$/);
    await foto("05-tablero");
  });

  await paso("Configuración del workspace muestra las cinco pestañas", async () => {
    await pagina.goto(`${servidor.url}/consola/acme/cert/configuracion`);
    await pagina.getByRole("heading", { name: "Configuración del workspace" }).waitFor();
    const tabs = await pagina.getByRole("tablist", { name: "Configuración" }).getByRole("tab").allTextContents();
    assert.deepEqual(tabs, ["Perfiles", "Presupuestos", "Proveedores de contexto", "Suscripciones", "Catálogo de modelos"]);
    await foto("06-config-workspace");
  });

  await paso("Mandatos: la pantalla carga la lista del workspace sin error", async () => {
    await pagina.goto(`${servidor.url}/consola/acme/cert/mandatos`);
    await pagina.getByRole("heading", { name: "Mandatos" }).waitFor();
    await pagina.getByText("Este workspace aún no tiene mandatos").waitFor();
    assert.equal(await pagina.getByRole("alert").count(), 0, "la lista de mandatos muestra un error");
    await foto("06-mandatos");
  });

  await paso("Configuración de la organización: pestañas Presupuestos, Proveedores y Catálogo cargan sin error", async () => {
    await pagina.goto(`${servidor.url}/consola/acme/configuracion`);
    await pagina.getByRole("heading", { name: "Configuración de la organización" }).waitFor();
    for (const nombre of ["Presupuestos", "Proveedores de contexto", "Catálogo de modelos"]) {
      await pagina.getByRole("tab", { name: nombre }).click();
      await pagina.getByRole("tab", { name: nombre, selected: true }).waitFor();
      await pagina.waitForLoadState("networkidle");
      assert.equal(await pagina.getByRole("alert").count(), 0, `la pestaña ${nombre} muestra un error`);
    }
    await foto("07-config-catalogo");
  });

  await paso("alta de una suscripción Foundry: la clave se cifra y no vuelve a verse", async () => {
    await pagina.getByRole("tab", { name: "Suscripciones" }).click();
    await pagina.getByRole("button", { name: "Nueva suscripción" }).click();
    await pagina.getByLabel("Nombre", { exact: true }).fill("Foundry UE");
    await pagina.getByLabel("Endpoint").fill("https://acme.services.ai.azure.com");
    await pagina.getByLabel("Proyecto").fill("railspec");
    await pagina.getByLabel("Región", { exact: true }).fill("swedencentral");
    await pagina.getByLabel("Zona de datos").selectOption("eu");
    await pagina.getByLabel("Clave", { exact: true }).fill("clave-secreta-del-recurso");
    await pagina.getByRole("button", { name: "Guardar" }).click();
    await dialogo().waitFor({ state: "detached" });
    const tarjeta = pagina.locator('[data-suscripcion="foundry-ue"]');
    await tarjeta.getByText("clave guardada").waitFor();
    assert.ok(!(await pagina.content()).includes("clave-secreta-del-recurso"), "la clave aparece en la página");
    await pagina.reload();
    await pagina.getByRole("tab", { name: "Suscripciones" }).click();
    await pagina.locator('[data-suscripcion="foundry-ue"]').getByText("clave guardada").waitFor();
    assert.ok(!(await pagina.content()).includes("clave-secreta-del-recurso"));
    await foto("08-suscripcion");
  });

  await paso("declarar un modelo y dejarlo disponible", async () => {
    const tarjeta = pagina.locator('[data-suscripcion="foundry-ue"]');
    await tarjeta.getByRole("button", { name: "Declarar un despliegue" }).or(tarjeta.getByRole("button", { name: /Declarar/ })).first().click();
    await dialogo().getByLabel("Despliegue", { exact: true }).fill("opus-eu");
    await dialogo().getByLabel("Modelo", { exact: true }).fill("claude-opus-5-5");
    await dialogo().getByLabel("SKU").fill("DataZoneStandard");
    await dialogo().getByRole("button", { name: "Declarar", exact: true }).click();
    await dialogo().waitFor({ state: "detached" });
    await tarjeta.getByText("claude-opus-5-5").first().waitFor();
    await foto("09-modelo-declarado");
  });

  await paso("crear el perfil «estandar» de la organización asociado a la suscripción", async () => {
    await pagina.getByRole("tab", { name: "Perfiles" }).click();
    await pagina.getByRole("button", { name: "Crear perfil" }).click();
    await pagina.getByLabel("Suscripción").selectOption("foundry-ue");
    // La plantilla trae el rol «redactor» sin modelo: la consola avisa y no deja guardar (el contrato exige uno).
    await pagina.getByRole("status").getByText(/Falta el modelo \(foundry\) de: redactor/).waitFor();
    assert.ok(await pagina.getByRole("button", { name: "Guardar perfil" }).isDisabled());
    await pagina.getByLabel("Modelo foundry para redactor").fill("opus-eu");
    assert.ok(await pagina.getByRole("button", { name: "Guardar perfil" }).isEnabled());
    await foto("10-perfil-editor");
    await pagina.getByRole("button", { name: "Guardar perfil" }).click();
    await pagina.getByText(/^Guardado · versión 1/).waitFor();
    await pagina.reload();
    assert.equal(await pagina.getByLabel("Suscripción").inputValue(), "foundry-ue");
    await pagina.getByRole("tab", { name: "Suscripciones" }).click();
    await pagina.locator('[data-suscripcion="foundry-ue"]').getByText(/la usan estandar/).waitFor();
  });

  await paso("asignar el rol «lector» a otra persona y verla entrar con la configuración en solo lectura", async () => {
    await pagina.goto(`${servidor.url}/consola/acme/administracion`);
    await pagina.getByRole("button", { name: "Asignar rol" }).click();
    await dialogo().getByLabel("Login de GitHub").fill("dev");
    await dialogo().getByLabel("Rol", { exact: true }).selectOption("lector");
    await dialogo().getByLabel("Workspace").selectOption("cert");
    await dialogo().getByRole("button", { name: "Asignar", exact: true }).click();
    await pagina.getByText("Rol asignado").waitFor();
    await foto("11-roles");
    const otra = await navegador.newContext({ viewport: { width: 1280, height: 800 } });
    const p2 = await otra.newPage();
    p2.on("pageerror", (e) => problemas.push(`excepción (dev): ${e.message}`));
    await p2.goto(`${servidor.url}/consola/login`);
    await p2.getByLabel("Token de desarrollo").fill("tok-dev");
    await p2.getByRole("button", { name: "Entrar con token" }).click();
    await p2.waitForURL(/\/consola\/acme(\/cert)?$/);
    await p2.goto(`${servidor.url}/consola/acme/configuracion`);
    await p2.getByText("Solo lectura: necesitas ser admin. de organización para cambiarla.").waitFor();
    await p2.getByRole("tab", { name: "Suscripciones" }).click();
    await p2.getByRole("tab", { name: "Suscripciones", selected: true }).waitFor();
    await p2.locator('[data-suscripcion="foundry-ue"]').waitFor();
    assert.equal(await p2.getByRole("button", { name: "Nueva suscripción" }).count(), 0);
    assert.equal(await p2.getByRole("button", { name: "Descubrir modelos" }).count(), 0);
    assert.ok(!(await p2.content()).includes("clave-secreta-del-recurso"));
    await p2.screenshot({ path: join(salida, "12-solo-lectura.png"), fullPage: true });
    await otra.close();
  });

  await paso("Salir cierra la sesión y las rutas vuelven al login", async () => {
    esperados.add("401 /consola/api/yo");
    await pagina.getByRole("button", { name: "Salir" }).click();
    await aterriza(/^\/consola\/login/);
    await pagina.goto(`${servidor.url}/consola/acme/configuracion`);
    await aterriza(/^\/consola\/login\?volver=%2Facme%2Fconfiguracion$/);
  });
} catch (e) {
  fallo = e;
}

await navegador.close();
servidor.proceso.kill();

const fallidos = resultados.filter((r) => !r.ok);
if (problemas.length) {
  console.log(`\nel navegador registró ${problemas.length} problema(s):`);
  for (const p of [...new Set(problemas)]) console.log(`  - ${p}`);
}
console.log(`\n${resultados.length - fallidos.length}/${resultados.length} pasos ok; capturas en ${salida}`);
if (fallo || problemas.length) {
  if (fallo) console.log(`\nregistro del servidor:\n${servidor.registro().split("\n").slice(-15).join("\n")}`);
  process.exit(1);
}
