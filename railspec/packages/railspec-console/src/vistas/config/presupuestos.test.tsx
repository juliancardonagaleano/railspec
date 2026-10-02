import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PresupuestoConfig } from "../../api/tipos";
import { json, montarConQuery, presupuestoConfig, servidorFalso, type Responder } from "../../pruebas/servidor";
import { aPresupuesto, Presupuestos } from "./Presupuestos";

// Presupuestos: valores por unidad, por fase y mensual; guardado con `version` (bloqueo
// optimista), campos vacíos = sin tope, validación del formulario y errores del servidor.

const RUTA = "PUT /orgs/acme/presupuestos";

function servidor(lista: () => PresupuestoConfig[], extra: Record<string, Responder> = {}) {
  return servidorFalso({ "GET /orgs/acme/presupuestos": () => lista(), ...extra });
}

const campo = (etiqueta: string) => screen.getByLabelText(etiqueta) as HTMLInputElement;
const guardar = () => screen.getByRole("button", { name: "Guardar presupuesto" });

/** Cambia el valor de un campo numérico (vacía y escribe). */
async function poner(user: ReturnType<typeof userEvent.setup>, etiqueta: string, valor: string) {
  await user.clear(campo(etiqueta));
  if (valor) await user.type(campo(etiqueta), valor);
}

describe("presupuestos", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("muestra los topes guardados (mensual, por unidad y por fase)", async () => {
    const s = servidor(() => [presupuestoConfig()]);
    montarConQuery(<Presupuestos org="acme" editable />);

    expect(await screen.findByLabelText("Presupuesto mensual (USD)")).toHaveValue(500);
    expect(campo("Por unidad: Tokens máx.")).toHaveValue(100000);
    expect(campo("Por unidad: Segundos máx.")).toHaveValue(600);
    expect(campo("Por unidad: Costo USD máx.")).toHaveValue(5);
    expect(campo("Tokens máx. en Plan")).toHaveValue(20000);
    // Las fases sin tope quedan vacías.
    expect(campo("Tokens máx. en Spec")).toHaveValue(null);
    expect(campo("Costo USD máx. en Implementación")).toHaveValue(null);
    expect(s.de("GET", "/orgs/acme/presupuestos")[0]!.consulta.has("workspace")).toBe(false);
  });

  it("guarda con la versión vista, solo los topes con valor, y recarga la versión nueva", async () => {
    const user = userEvent.setup();
    let actual = presupuestoConfig();
    const s = servidor(() => [actual], {
      [RUTA]: (l) => {
        actual = presupuestoConfig({ ...l.cuerpo, version: actual.version + 1 });
        return actual;
      },
    });
    montarConQuery(<Presupuestos org="acme" editable />);

    await screen.findByLabelText("Presupuesto mensual (USD)");
    await poner(user, "Por unidad: Tokens máx.", "200000");
    await poner(user, "Por unidad: Segundos máx.", ""); // vaciar = sin tope
    await poner(user, "Tokens máx. en Spec", "15000");
    await poner(user, "Costo USD máx. en Implementación", "2.5");
    await poner(user, "Presupuesto mensual (USD)", "750.5");
    await user.click(guardar());

    await waitFor(() => expect(s.de("PUT", "/orgs/acme/presupuestos")).toHaveLength(1));
    const [put] = s.de("PUT", "/orgs/acme/presupuestos");
    expect(put!.consulta.has("workspace")).toBe(false);
    expect(put!.cabeceras["X-Railspec-Consola"]).toBe("1");
    expect(put!.cuerpo).toEqual({
      por_unidad: { tokens_max: 200000, costo_usd_max: 5 },
      por_fase: { spec: { tokens_max: 15000 }, plan: { tokens_max: 20000 }, implement: { costo_usd_max: 2.5 } },
      mensual_usd: 750.5,
      version: 2,
    });
    // La vista recarga: el formulario refleja lo guardado (versión 3).
    await waitFor(() => expect(campo("Por unidad: Tokens máx.")).toHaveValue(200000));
    expect(campo("Por unidad: Segundos máx.")).toHaveValue(null);
  });

  it("los campos vacíos o con 0 no se envían y el mensual vacío es null", async () => {
    const user = userEvent.setup();
    const s = servidor(() => [presupuestoConfig()], { [RUTA]: () => presupuestoConfig({ version: 3 }) });
    montarConQuery(<Presupuestos org="acme" editable />);

    await screen.findByLabelText("Presupuesto mensual (USD)");
    await poner(user, "Presupuesto mensual (USD)", "");
    await poner(user, "Por unidad: Tokens máx.", "0"); // el esquema no admite 0
    await poner(user, "Por unidad: Segundos máx.", "");
    await poner(user, "Por unidad: Costo USD máx.", "");
    await poner(user, "Tokens máx. en Plan", "");
    await user.click(guardar());

    await waitFor(() => expect(s.de("PUT", "/orgs/acme/presupuestos")).toHaveLength(1));
    expect(s.de("PUT", "/orgs/acme/presupuestos")[0]!.cuerpo).toEqual({ por_unidad: {}, por_fase: {}, mensual_usd: null, version: 2 });
  });

  it.each([
    ["Por unidad: Tokens máx.", "-5", "negativo"],
    ["Por unidad: Tokens máx.", "1.5", "fraccionario"],
    ["Tokens máx. en Plan", "2.5", "fraccionario por fase"],
    ["Por unidad: Costo USD máx.", "0.005", "con menos de un centavo"],
    ["Presupuesto mensual (USD)", "-1", "mensual negativo"],
  ])("no envía %s = %s (%s) por validación del formulario", async (etiqueta, valor) => {
    const user = userEvent.setup();
    const s = servidor(() => [presupuestoConfig()], { [RUTA]: () => presupuestoConfig({ version: 3 }) });
    montarConQuery(<Presupuestos org="acme" editable />);

    await screen.findByLabelText("Presupuesto mensual (USD)");
    await poner(user, etiqueta, valor);
    expect(campo(etiqueta)).toBeInvalid();
    await user.click(guardar());
    expect(s.de("PUT", "/orgs/acme/presupuestos")).toHaveLength(0);
  });

  it("muestra el detalle y los campos de un 422 del servidor", async () => {
    const user = userEvent.setup();
    servidor(() => [presupuestoConfig()], {
      [RUTA]: () =>
        json(422, {
          detalle: "Presupuesto no válido.",
          errores: [{ ruta: "por_fase.plan.tokens_max", mensaje: "supera el tope por unidad" }],
        }),
    });
    montarConQuery(<Presupuestos org="acme" editable />);

    await screen.findByLabelText("Presupuesto mensual (USD)");
    await user.click(guardar());
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent("Presupuesto no válido. (por_fase.plan.tokens_max: supera el tope por unidad)");
    expect(screen.queryByText("Presupuesto guardado.")).toBeNull();
  });

  it("tras un 409 recarga lo vigente y el siguiente guardado envía la versión nueva", async () => {
    const user = userEvent.setup();
    let vigente = presupuestoConfig({ version: 2 });
    const s = servidor(() => [vigente], {
      [RUTA]: (l) => {
        if (l.cuerpo.version !== vigente.version) return json(409, { detalle: "versión desactualizada", version_actual: vigente.version });
        vigente = presupuestoConfig({ ...l.cuerpo, version: vigente.version + 1 });
        return vigente;
      },
    });
    montarConQuery(<Presupuestos org="acme" editable />);

    await screen.findByLabelText("Presupuesto mensual (USD)");
    // Otra persona sube el mensual a 900 mientras se edita.
    vigente = presupuestoConfig({ version: 3, mensual_usd: 900 });
    await user.click(guardar());

    await waitFor(() => expect(campo("Presupuesto mensual (USD)")).toHaveValue(900));
    await user.click(guardar());
    await waitFor(() => expect(s.de("PUT", "/orgs/acme/presupuestos")).toHaveLength(2));
    expect(s.de("PUT", "/orgs/acme/presupuestos").map((l) => l.cuerpo.version)).toEqual([2, 3]);
    expect(vigente.version).toBe(4);
  });

  it("explica un 409 como conflicto de versión cuando la recarga aún no cambió el formulario", async () => {
    const user = userEvent.setup();
    // El servidor rechaza pero sigue sirviendo la misma versión: el aviso se conserva.
    servidor(() => [presupuestoConfig()], { [RUTA]: () => json(409, { detalle: "versión desactualizada", version_actual: 7 }) });
    montarConQuery(<Presupuestos org="acme" editable />);

    await screen.findByLabelText("Presupuesto mensual (USD)");
    await user.click(guardar());
    const aviso = await screen.findByText(/Otra persona modificó este registro/);
    expect(aviso).toHaveTextContent("(versión actual 7)");
    expect(aviso).toHaveTextContent("versión desactualizada");
  });

  it("en un workspace sin presupuesto propio parte vacío, guarda en su ámbito y sin versión", async () => {
    const user = userEvent.setup();
    // La lista trae el de la organización (workspace null) y no el del workspace.
    const s = servidor(() => [presupuestoConfig()], { [RUTA]: () => presupuestoConfig({ workspace: "cert", version: 1 }) });
    montarConQuery(<Presupuestos org="acme" ws="cert" editable />);

    expect(await screen.findByText(/Sin presupuesto propio, el workspace usa el de la organización/)).toBeInTheDocument();
    expect(await screen.findByLabelText("Presupuesto mensual (USD)")).toHaveValue(null);
    expect(campo("Por unidad: Tokens máx.")).toHaveValue(null);
    expect(s.de("GET", "/orgs/acme/presupuestos")[0]!.consulta.get("workspace")).toBe("cert");

    await poner(user, "Por unidad: Tokens máx.", "50000");
    await user.click(guardar());
    await waitFor(() => expect(s.de("PUT", "/orgs/acme/presupuestos")).toHaveLength(1));
    const [put] = s.de("PUT", "/orgs/acme/presupuestos");
    expect(put!.consulta.get("workspace")).toBe("cert");
    expect(put!.cuerpo).toEqual({ por_unidad: { tokens_max: 50000 }, por_fase: {}, mensual_usd: null });
  });

  it("en un workspace con presupuesto propio muestra el suyo y envía su versión", async () => {
    const user = userEvent.setup();
    const propio = presupuestoConfig({ workspace: "cert", version: 5, mensual_usd: 80, por_unidad: { tokens_max: 1000 }, por_fase: {} });
    const s = servidor(() => [presupuestoConfig(), propio], { [RUTA]: () => propio });
    montarConQuery(<Presupuestos org="acme" ws="cert" editable />);

    expect(await screen.findByLabelText("Presupuesto mensual (USD)")).toHaveValue(80);
    expect(campo("Por unidad: Tokens máx.")).toHaveValue(1000);
    await user.click(guardar());
    await waitFor(() => expect(s.de("PUT", "/orgs/acme/presupuestos")).toHaveLength(1));
    expect(s.de("PUT", "/orgs/acme/presupuestos")[0]!.cuerpo.version).toBe(5);
  });

  it("sin permiso de edición los campos quedan deshabilitados y no hay botón de guardar", async () => {
    servidor(() => [presupuestoConfig()]);
    montarConQuery(<Presupuestos org="acme" editable={false} />);

    expect(await screen.findByLabelText("Presupuesto mensual (USD)")).toBeDisabled();
    expect(campo("Por unidad: Tokens máx.")).toBeDisabled();
    expect(campo("Tokens máx. en Plan")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Guardar presupuesto" })).toBeNull();
  });

  it("si no se puede leer la lista muestra el error del servidor y no el formulario", async () => {
    servidorFalso({ "GET /orgs/acme/presupuestos": () => json(403, { detalle: "Necesitas rol de lector en la organización." }) });
    montarConQuery(<Presupuestos org="acme" editable />);

    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent("Sin permiso");
    expect(alerta).toHaveTextContent("Necesitas rol de lector en la organización.");
    expect(screen.queryByRole("button", { name: "Guardar presupuesto" })).toBeNull();
  });
});

describe("aPresupuesto", () => {
  it("solo conserva los topes numéricos mayores que 0", () => {
    expect(aPresupuesto({ tokens_max: "1000", segundos_max: "0", costo_usd_max: "abc" })).toEqual({ tokens_max: 1000 });
    expect(aPresupuesto({ tokens_max: "", segundos_max: " 30 ", costo_usd_max: "-1" })).toEqual({ segundos_max: 30 });
    expect(aPresupuesto({ tokens_max: "", segundos_max: "", costo_usd_max: "" })).toEqual({});
  });
});
