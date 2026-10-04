import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EscrituraPerfil, ListaSuscripciones, ModeloSuscripcion, PerfilConfig, Suscripcion } from "../../api/tipos";
import { auditoria, json, montarConQuery, perfilConfig, servidorFalso } from "../../pruebas/servidor";
import { EditorPerfil } from "./EditorPerfil";
import { idDesdeNombre, Suscripciones } from "./Suscripciones";

// Suscripciones de modelos (contrato 1.6): alta con clave de solo escritura, descubrimiento, elección de
// modelos y asociación de un perfil. La API se simula con un `fetch` falso.

const CLAVE = "clave-secreta-del-recurso";

const modelo = (clave: string, o: Partial<ModeloSuscripcion> = {}): ModeloSuscripcion => ({
  modelo: "claude-opus-5-5",
  despliegue: clave,
  sku: "DataZoneStandard",
  region: "zona-eu",
  capacidades: { efforts: ["low", "high"], structured_outputs: true, contexto_max_tokens: 1000000 },
  origen: "descubierto",
  seleccionado: false,
  ausente: false,
  clave,
  hosting: "azure",
  restringible: true,
  ...o,
});

const suscripcion = (o: Partial<Suscripcion> = {}): Suscripcion => ({
  org: "acme",
  id: "foundry-eu",
  nombre: "Foundry UE",
  proveedor: "foundry",
  endpoint: "https://acme.services.ai.azure.com",
  proyecto: "railspec",
  region: "swedencentral",
  zona_datos: "eu",
  autenticacion: "api-key",
  clave_configurada: true,
  habilitada: true,
  modelos: [],
  ultima_lectura: null,
  perfiles: [],
  version: 1,
  auditoria,
  ...o,
});

const lista = (ss: Suscripcion[], disponible = true): ListaSuscripciones => ({
  cifrado: { disponible, variable: "RAILSPEC_CLAVE_MAESTRA" },
  suscripciones: ss,
});

describe("suscripciones de modelos", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("el id sale del nombre sin acentos ni símbolos", () => {
    expect(idDesdeNombre("Foundry UE (Suecia)")).toBe("foundry-ue-suecia");
    expect(idDesdeNombre("  Ñandú  ")).toBe("nandu");
  });

  it("la lista muestra la suscripción sin clave y avisa si el servidor no puede cifrar", async () => {
    servidorFalso({ "GET /orgs/acme/suscripciones": () => lista([suscripcion({ perfiles: [{ nombre: "estandar", workspace: null }] })], false) });
    montarConQuery(<Suscripciones org="acme" editable />);

    expect(await screen.findByText("Foundry UE")).toBeInTheDocument();
    expect(screen.getByText("clave guardada")).toBeInTheDocument();
    expect(screen.getByText(/la usan estandar/)).toBeInTheDocument();
    expect(screen.getByText(/RAILSPEC_CLAVE_MAESTRA/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(CLAVE);
  });

  it("da de alta una suscripción: la clave viaja una vez, el campo es de contraseña y el diálogo se cierra", async () => {
    const user = userEvent.setup();
    let creada: Suscripcion[] = [];
    const s = servidorFalso({
      "GET /orgs/acme/suscripciones": () => lista(creada),
      "PUT /orgs/acme/suscripciones/foundry-ue": (l) => {
        creada = [suscripcion({ id: "foundry-ue", nombre: l.cuerpo.nombre })];
        return json(200, creada[0]);
      },
    });
    montarConQuery(<Suscripciones org="acme" editable />);

    await user.click(await screen.findByRole("button", { name: "Nueva suscripción" }));
    const dialogo = await screen.findByRole("dialog", { name: "Nueva suscripción" });
    await user.type(within(dialogo).getByLabelText("Nombre"), "Foundry UE");
    expect(within(dialogo).getByText("Id: foundry-ue")).toBeInTheDocument();
    const endpoint = within(dialogo).getByLabelText("Endpoint");
    await user.clear(endpoint);
    await user.type(endpoint, "https://acme.services.ai.azure.com");
    await user.type(within(dialogo).getByLabelText("Proyecto"), "railspec");
    await user.type(within(dialogo).getByLabelText("Región"), "swedencentral");
    await user.selectOptions(within(dialogo).getByLabelText("Zona de datos"), "eu");
    const clave = within(dialogo).getByLabelText("Clave");
    expect(clave).toHaveAttribute("type", "password");
    // Sin clave no se puede guardar.
    expect(within(dialogo).getByRole("button", { name: "Guardar" })).toBeDisabled();
    await user.type(clave, CLAVE);
    await user.click(within(dialogo).getByRole("button", { name: "Guardar" }));

    expect(await screen.findByText(/^Guardado · versión 1\b/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    const [put] = s.de("PUT", "/orgs/acme/suscripciones/foundry-ue");
    expect(put?.cuerpo).toMatchObject({
      proveedor: "foundry",
      nombre: "Foundry UE",
      autenticacion: "api-key",
      endpoint: "https://acme.services.ai.azure.com",
      proyecto: "railspec",
      region: "swedencentral",
      zona_datos: "eu",
      clave: CLAVE,
    });
    expect(put?.cuerpo).not.toHaveProperty("version");
    expect(document.body.textContent).not.toContain(CLAVE);
  });

  it("al editar, la clave vacía se conserva (no se envía) y cambiar el endpoint exige escribirla otra vez", async () => {
    const user = userEvent.setup();
    const s = servidorFalso({
      "GET /orgs/acme/suscripciones": () => lista([suscripcion({ version: 4 })]),
      "PUT /orgs/acme/suscripciones/foundry-eu": () => suscripcion({ version: 5 }),
    });
    montarConQuery(<Suscripciones org="acme" editable />);

    await user.click(await screen.findByRole("button", { name: "Editar" }));
    const dialogo = await screen.findByRole("dialog", { name: "Editar Foundry UE" });
    expect(within(dialogo).getByLabelText("Clave")).toHaveValue("");
    expect(within(dialogo).getByText(/Déjala vacía para conservarla/)).toBeInTheDocument();

    const endpoint = within(dialogo).getByLabelText("Endpoint");
    await user.clear(endpoint);
    await user.type(endpoint, "https://otro.services.ai.azure.com");
    expect(within(dialogo).getByRole("button", { name: "Guardar" })).toBeDisabled();
    expect(within(dialogo).getByText(/cambiaste el endpoint/)).toBeInTheDocument();

    await user.clear(endpoint);
    await user.type(endpoint, "https://acme.services.ai.azure.com");
    await user.click(within(dialogo).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(s.de("PUT", "/orgs/acme/suscripciones/foundry-eu")).toHaveLength(1));
    const cuerpo = s.de("PUT", "/orgs/acme/suscripciones/foundry-eu")[0]!.cuerpo;
    expect(cuerpo.version).toBe(4);
    expect(cuerpo).not.toHaveProperty("clave");
  });

  it("descubre los modelos, deja elegir los disponibles y guarda la elección con la versión vista", async () => {
    const user = userEvent.setup();
    let actual = suscripcion();
    const s = servidorFalso({
      "GET /orgs/acme/suscripciones": () => lista([actual]),
      "POST /orgs/acme/suscripciones/foundry-eu/descubrir": () => {
        actual = suscripcion({
          version: 2,
          modelos: [modelo("opus-eu"), modelo("sonnet-eu", { modelo: "claude-sonnet-5-5" })],
          ultima_lectura: { en: "2026-10-04T10:00:00Z", por: "ana", resultado: "ok", modelos: 2 },
        });
        return { resultado: "ok", modelos: 2, suscripcion: actual };
      },
      "PUT /orgs/acme/suscripciones/foundry-eu/modelos": (l) => {
        actual = suscripcion({
          version: 3,
          modelos: actual.modelos.map((m) => ({ ...m, seleccionado: l.cuerpo.seleccionados.includes(m.clave) })),
        });
        return actual;
      },
    });
    montarConQuery(<Suscripciones org="acme" editable />);

    expect(await screen.findByText(/Sin modelos todavía/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Descubrir modelos" }));
    const casilla = await screen.findByRole("checkbox", { name: "Disponible opus-eu" });
    expect(screen.getByText(/2 modelos/)).toBeInTheDocument();
    expect(casilla).not.toBeChecked();
    expect(screen.getAllByText("restringido/interno")).toHaveLength(2);

    const guardar = screen.getByRole("button", { name: "Guardar modelos disponibles" });
    expect(guardar).toBeDisabled();
    await user.click(casilla);
    await user.click(guardar);

    await waitFor(() => expect(s.de("PUT", "/orgs/acme/suscripciones/foundry-eu/modelos")).toHaveLength(1));
    expect(s.de("PUT", "/orgs/acme/suscripciones/foundry-eu/modelos")[0]!.cuerpo).toEqual({ seleccionados: ["opus-eu"], version: 2 });
    expect(await screen.findByText(/^Modelos guardados · versión 3\b/)).toBeInTheDocument();
    expect(await screen.findByRole("checkbox", { name: "Disponible opus-eu" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Disponible sonnet-eu" })).not.toBeChecked();
  });

  it("si el proveedor falla muestra el motivo, no inventa modelos y conserva la elección", async () => {
    const user = userEvent.setup();
    let actual = suscripcion({ modelos: [modelo("opus-eu", { seleccionado: true })] });
    servidorFalso({
      "GET /orgs/acme/suscripciones": () => lista([actual]),
      "POST /orgs/acme/suscripciones/foundry-eu/descubrir": () => {
        actual = suscripcion({
          version: 2,
          modelos: actual.modelos,
          ultima_lectura: {
            en: "2026-10-04T10:00:00Z",
            por: "ana",
            resultado: "error",
            modelos: 0,
            error_codigo: "autenticacion",
            error_detalle: "Foundry UE: el proveedor rechazó la clave (HTTP 401).",
          },
        });
        return json(502, { resultado: "error", modelos: 0, suscripcion: actual, codigo: "autenticacion", detalle: "Foundry UE: el proveedor rechazó la clave (HTTP 401)." });
      },
    });
    montarConQuery(<Suscripciones org="acme" editable />);

    await user.click(await screen.findByRole("button", { name: "Descubrir modelos" }));
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent("el proveedor rechazó la clave (HTTP 401)");
    expect(alerta).toHaveTextContent("Se conserva la elección anterior");
    expect(await screen.findByText(/Última lectura .* falló/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Disponible opus-eu" })).toBeChecked();
  });

  it("declara un despliegue a mano y retira los declarados", async () => {
    const user = userEvent.setup();
    let actual = suscripcion();
    const s = servidorFalso({
      "GET /orgs/acme/suscripciones": () => lista([actual]),
      "POST /orgs/acme/suscripciones/foundry-eu/modelos": () => {
        actual = suscripcion({ version: 2, modelos: [modelo("opus-eu", { origen: "declarado", seleccionado: true })] });
        return actual;
      },
      "DELETE /orgs/acme/suscripciones/foundry-eu/modelos/opus-eu": () => {
        actual = suscripcion({ version: 3 });
        return actual;
      },
    });
    montarConQuery(<Suscripciones org="acme" editable />);

    await user.click(await screen.findByRole("button", { name: "Declarar despliegue" }));
    const dialogo = await screen.findByRole("dialog", { name: "Declarar despliegue" });
    await user.type(within(dialogo).getByLabelText("Despliegue"), "opus-eu");
    await user.type(within(dialogo).getByLabelText("Modelo"), "claude-opus-5-5");
    await user.click(within(dialogo).getByRole("button", { name: "Declarar" }));

    expect(await screen.findByText("declarado")).toBeInTheDocument();
    expect(s.de("POST", "/orgs/acme/suscripciones/foundry-eu/modelos")[0]!.cuerpo).toMatchObject({
      modelo: "claude-opus-5-5",
      despliegue: "opus-eu",
      sku: "DataZoneStandard",
      version: 1,
    });
    await user.click(await screen.findByRole("button", { name: "Retirar opus-eu" }));
    await waitFor(() => expect(s.de("DELETE", "/orgs/acme/suscripciones/foundry-eu/modelos/opus-eu")).toHaveLength(1));
    expect(s.de("DELETE", "/orgs/acme/suscripciones/foundry-eu/modelos/opus-eu")[0]!.consulta.get("version")).toBe("2");
  });

  it("borrar una suscripción en uso muestra el 409 del servidor", async () => {
    const user = userEvent.setup();
    servidorFalso({
      "GET /orgs/acme/suscripciones": () => lista([suscripcion({ perfiles: [{ nombre: "estandar", workspace: null }] })]),
      "DELETE /orgs/acme/suscripciones/foundry-eu": () => json(409, { detalle: "la usan los perfiles: estandar", codigo: "suscripcion-en-uso" }),
    });
    montarConQuery(<Suscripciones org="acme" editable />);

    await user.click(await screen.findByRole("button", { name: "Borrar" }));
    const dialogo = await screen.findByRole("dialog", { name: "Borrar suscripción" });
    await user.click(within(dialogo).getByRole("button", { name: "Borrar" }));
    expect(await within(dialogo).findByText(/la usan los perfiles: estandar/)).toBeInTheDocument();
  });

  it("sin permiso de escritura la lista es de solo lectura", async () => {
    servidorFalso({ "GET /orgs/acme/suscripciones": () => lista([suscripcion({ modelos: [modelo("opus-eu", { seleccionado: true })] })]) });
    montarConQuery(<Suscripciones org="acme" editable={false} />);

    expect(await screen.findByText("Foundry UE")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Nueva suscripción" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Descubrir modelos" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Editar" })).toBeNull();
    expect(screen.getByRole("checkbox", { name: "Disponible opus-eu" })).toBeDisabled();
  });
});

describe("perfil asociado a una suscripción", () => {
  afterEach(() => vi.unstubAllGlobals());

  const foundry = suscripcion({
    modelos: [modelo("opus-eu", { seleccionado: true }), modelo("sonnet-eu", { seleccionado: false }), modelo("viejo", { seleccionado: true, ausente: true })],
  });
  const inicial = (perfil: PerfilConfig): EscrituraPerfil => ({
    roles: { redactor: { modelo: {}, structured_outputs: false } },
    gate: perfil.gate,
    exploradores: perfil.exploradores,
    version: 3,
  });

  it("elegir la suscripción limita las sugerencias a sus modelos elegidos y desactiva el otro proveedor", async () => {
    const user = userEvent.setup();
    const s = servidorFalso({
      "GET /orgs/acme/suscripciones": () => lista([foundry]),
      "GET /orgs/acme/catalogo": () => [],
      "PUT /orgs/acme/perfiles/estandar": (l) => ({ perfil: perfilConfig("estandar", { ...l.cuerpo, version: 4 }), avisos: [] }),
    });
    montarConQuery(<EditorPerfil org="acme" nombre="estandar" inicial={inicial(perfilConfig("estandar"))} editable />);

    const selector = await screen.findByLabelText("Suscripción");
    await user.selectOptions(selector, "foundry-eu");
    expect(screen.getByLabelText("Modelo anthropic para redactor")).toBeDisabled();
    const entrada = screen.getByLabelText("Modelo foundry para redactor");
    expect(entrada).toBeEnabled();
    const sugerencias = [...document.querySelectorAll("#modelos-foundry option")].map((o) => o.getAttribute("value"));
    expect(sugerencias).toEqual(["opus-eu"]);

    await user.type(entrada, "opus-eu");
    await user.click(screen.getByRole("button", { name: "Guardar perfil" }));
    await waitFor(() => expect(s.de("PUT", "/orgs/acme/perfiles/estandar")).toHaveLength(1));
    expect(s.de("PUT", "/orgs/acme/perfiles/estandar")[0]!.cuerpo).toMatchObject({
      suscripcion: "foundry-eu",
      roles: { redactor: { modelo: { foundry: "opus-eu" } } },
      version: 3,
    });
  });

  it("sin suscripciones en la organización no aparece el selector y rige el catálogo del servidor", async () => {
    servidorFalso({
      "GET /orgs/acme/suscripciones": () => lista([]),
      "GET /orgs/acme/catalogo": () => [],
    });
    montarConQuery(<EditorPerfil org="acme" nombre="estandar" inicial={inicial(perfilConfig("estandar"))} editable />);
    await screen.findByLabelText("Modelo foundry para redactor");
    expect(screen.queryByLabelText("Suscripción")).toBeNull();
  });
});
