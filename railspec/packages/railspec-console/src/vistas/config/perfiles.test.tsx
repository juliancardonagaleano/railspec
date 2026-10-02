import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { EscrituraPerfil, ModeloCatalogo, PerfilConfig } from "../../api/tipos";
import { json, montarConQuery, perfilConfig, servidorFalso, type Llamada, type Responder } from "../../pruebas/servidor";
import { EditorPerfil } from "./EditorPerfil";
import { Perfiles } from "./Perfiles";

// Perfiles de esfuerzo: listado por pestaña, edición con bloqueo optimista (`version`),
// avisos del servidor y conflictos. La API se simula con un `fetch` falso.

const modeloFoundry: ModeloCatalogo = {
  org: "acme",
  proveedor: "foundry",
  modelo: "claude-opus-5-5",
  despliegue: "opus-dz",
  hosting: "azure",
  region: "zona-us",
  capacidades: { efforts: ["low", "high"], thinking: true, structured_outputs: true, contexto_max_tokens: 1000000 },
  leido_en: "2026-10-01T10:00:00Z",
};

const estandar = perfilConfig("estandar");
const ligero = perfilConfig("ligero", { version: 1, exploradores: { bajo: 0, medio: 0, alto: 1 } });

/** Servidor con los perfiles en memoria; `extra` recibe ese estado para simular las escrituras. */
function servidor(perfiles: PerfilConfig[], extra: (estado: { perfiles: PerfilConfig[] }) => Record<string, Responder> = () => ({})) {
  const estado = { perfiles };
  const s = servidorFalso({
    "GET /orgs/acme/perfiles": () => estado.perfiles,
    "GET /orgs/acme/catalogo": () => [modeloFoundry],
    ...extra(estado),
  });
  return { ...s, estado };
}

/** Acepta el PUT: guarda lo recibido con la versión siguiente y devuelve los avisos dados. */
const aceptar =
  (est: { perfiles: PerfilConfig[] }, avisos: string[] = []) =>
  (l: Llamada) => {
    const nombre = l.ruta.split("/").at(-1)!;
    const ws = l.consulta.get("workspace");
    const previo = est.perfiles.find((p) => p.nombre === nombre && p.workspace === ws);
    const perfil = perfilConfig(nombre as PerfilConfig["nombre"], {
      ...(l.cuerpo as EscrituraPerfil),
      workspace: ws,
      version: (previo?.version ?? 0) + 1,
    });
    est.perfiles = [...est.perfiles.filter((p) => p !== previo), perfil];
    return { perfil, avisos };
  };

const pestana = (nombre: string) => screen.getByRole("tab", { name: new RegExp(`^${nombre}`) });

describe("perfiles de esfuerzo", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lista una pestaña por perfil con su versión, roles, gate y exploradores", async () => {
    const user = userEvent.setup();
    const s = servidor([estandar, ligero]);
    montarConQuery(<Perfiles org="acme" editable />);

    // Abre en «estandar».
    expect(await screen.findByText(/Versión 3/)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "estandar", selected: true })).toBeInTheDocument();
    expect(screen.getByLabelText("Modelo anthropic para redactor")).toHaveValue("claude-sonnet-5-5");
    expect(screen.getByLabelText("Modelo foundry para redactor")).toHaveValue("");
    expect(screen.getByLabelText("Effort para redactor")).toHaveValue("medium");
    expect(screen.getByLabelText("Críticos riesgo alto")).toHaveValue(3);
    expect(screen.getByLabelText("Adversarial riesgo alto")).toBeChecked();
    expect(screen.getByLabelText("Adversarial riesgo bajo")).not.toBeChecked();
    expect(screen.getByLabelText("alto")).toHaveValue(2); // exploradores
    // Las sugerencias de modelo salen del catálogo (despliegue si lo hay).
    await waitFor(() => expect(document.querySelectorAll("#modelos-foundry option")).toHaveLength(1));
    expect(document.querySelector("#modelos-foundry option")).toHaveAttribute("value", "opus-dz");

    await user.click(pestana("ligero"));
    expect(await screen.findByText(/Versión 1/)).toBeInTheDocument();
    expect(screen.getByLabelText("alto")).toHaveValue(1);

    // Sin perfil guardado: lo dice y ofrece crearlo (el usuario puede editar).
    await user.click(pestana("profundo"));
    expect(screen.getByText("Sin perfil «profundo» en la organización")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Crear perfil" })).toBeInTheDocument();
    // Solo se pidió la lista de la org (sin ?workspace).
    expect(s.de("GET", "/orgs/acme/perfiles")[0]!.consulta.has("workspace")).toBe(false);
  });

  it("sin permiso de edición todo queda en solo lectura", async () => {
    const user = userEvent.setup();
    servidor([estandar]);
    montarConQuery(<Perfiles org="acme" editable={false} />);

    expect(await screen.findByText(/Versión 3/)).toBeInTheDocument();
    for (const control of [
      screen.getByLabelText("Modelo anthropic para redactor"),
      screen.getByLabelText("Effort para redactor"),
      screen.getByLabelText("Críticos riesgo medio"),
      screen.getByLabelText("Adversarial riesgo medio"),
      screen.getByLabelText("medio"),
    ]) {
      expect(control).toBeDisabled();
    }
    expect(screen.queryByRole("button", { name: "Guardar perfil" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Añadir rol" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Quitar rol redactor" })).toBeNull();
    await user.click(pestana("profundo"));
    expect(screen.getByText("Sin perfil «profundo» en la organización")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Crear perfil" })).toBeNull();
  });

  it("guarda lo editado con la versión vista y recarga la versión nueva", async () => {
    const user = userEvent.setup();
    const s = servidor([estandar, ligero], (est) => ({
      "PUT /orgs/acme/perfiles/estandar": aceptar(est),
    }));
    montarConQuery(<Perfiles org="acme" editable />);

    await screen.findByText(/Versión 3/);
    await user.type(screen.getByLabelText("Modelo foundry para redactor"), "opus-dz");
    await user.selectOptions(screen.getByLabelText("Effort para redactor"), "high");
    await user.click(screen.getByLabelText("Structured outputs para redactor"));
    await user.type(screen.getByLabelText("Nombre del nuevo rol"), "critico");
    await user.click(screen.getByRole("button", { name: "Añadir rol" }));
    await user.clear(screen.getByLabelText("Críticos riesgo medio"));
    await user.type(screen.getByLabelText("Críticos riesgo medio"), "4");
    await user.click(screen.getByLabelText("Adversarial riesgo medio"));
    await user.clear(screen.getByLabelText("alto"));
    await user.type(screen.getByLabelText("alto"), "5");
    await user.click(screen.getByRole("button", { name: "Guardar perfil" }));

    // La vista recarga y pasa a la versión guardada.
    expect(await screen.findByText(/Versión 4/)).toBeInTheDocument();
    // El editor se remontó con la versión nueva y el «Guardado» sigue ahí, con esa versión.
    expect(screen.getByText(/^Guardado · versión 4\b/)).toBeInTheDocument();
    const [put] = s.de("PUT", "/orgs/acme/perfiles/estandar");
    expect(put!.consulta.has("workspace")).toBe(false);
    expect(put!.cabeceras["X-Railspec-Consola"]).toBe("1");
    expect(put!.cuerpo).toEqual({
      roles: {
        redactor: { modelo: { anthropic: "claude-sonnet-5-5", foundry: "opus-dz" }, effort: "high", structured_outputs: true },
        critico: { modelo: {}, structured_outputs: false },
      },
      gate: {
        bajo: { criticos: 1, iteraciones: 1, adversarial: false },
        medio: { criticos: 4, iteraciones: 2, adversarial: true },
        alto: { criticos: 3, iteraciones: 3, adversarial: true },
      },
      exploradores: { bajo: 0, medio: 1, alto: 5 },
      version: 3,
    });
  });
});

describe("avisos del guardado de perfiles", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("los avisos del servidor sobreviven al remontaje del editor por la versión nueva", async () => {
    const user = userEvent.setup();
    servidor([estandar, ligero], (est) => ({
      "PUT /orgs/acme/perfiles/estandar": aceptar(est, ["El effort high no lo admite claude-sonnet-5-5."]),
    }));
    montarConQuery(<Perfiles org="acme" editable />);

    await screen.findByText(/Versión 3/);
    await user.click(screen.getByRole("button", { name: "Guardar perfil" }));

    // La lista recarga (versión 4) y `key={version}` monta otro editor: los avisos no se pierden con él.
    expect(await screen.findByText(/Versión 4/)).toBeInTheDocument();
    expect(screen.getByText("Guardado con avisos:")).toBeInTheDocument();
    expect(screen.getByText("El effort high no lo admite claude-sonnet-5-5.")).toBeInTheDocument();
    expect(screen.getByText(/versión 4 ·/)).toBeInTheDocument();
  });

  it("cada perfil tiene su propio aviso", async () => {
    const user = userEvent.setup();
    servidor([estandar, ligero], (est) => ({ "PUT /orgs/acme/perfiles/estandar": aceptar(est) }));
    montarConQuery(<Perfiles org="acme" editable />);

    await screen.findByText(/Versión 3/);
    await user.click(screen.getByRole("button", { name: "Guardar perfil" }));
    expect(await screen.findByText(/^Guardado · versión 4\b/)).toBeInTheDocument();

    await user.click(pestana("ligero"));
    expect(screen.queryByText(/^Guardado ·/)).toBeNull();
  });
});

describe("editor de perfil", () => {
  afterEach(() => vi.unstubAllGlobals());

  const inicial: EscrituraPerfil = {
    roles: estandar.roles,
    gate: estandar.gate,
    exploradores: estandar.exploradores,
    version: 3,
  };

  function montarEditor(extra: Record<string, Responder>, props: { editable?: boolean; ws?: string; inicial?: EscrituraPerfil } = {}) {
    const s = servidorFalso({ "GET /orgs/acme/catalogo": () => [modeloFoundry], ...extra });
    montarConQuery(<EditorPerfil org="acme" nombre="estandar" inicial={props.inicial ?? inicial} editable={props.editable ?? true} {...(props.ws ? { ws: props.ws } : {})} />);
    return s;
  }

  it("tras guardar reutiliza la versión nueva: el siguiente guardado ya no choca", async () => {
    const user = userEvent.setup();
    let version = 3;
    const s = montarEditor({
      "PUT /orgs/acme/perfiles/estandar": (l) => {
        if (l.cuerpo.version !== version) return json(409, { detalle: "versión desactualizada", version_actual: version });
        version += 1;
        return { perfil: perfilConfig("estandar", { version }), avisos: [] };
      },
    });

    await user.click(await screen.findByRole("button", { name: "Guardar perfil" }));
    expect(await screen.findByText(/^Guardado · versión 4\b/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Guardar perfil" }));
    await waitFor(() => expect(s.de("PUT", "/orgs/acme/perfiles/estandar")).toHaveLength(2));
    expect(s.de("PUT", "/orgs/acme/perfiles/estandar").map((l) => l.cuerpo.version)).toEqual([3, 4]);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("muestra los avisos del servidor al guardar, uno por línea, y no el «guardado» simple", async () => {
    const user = userEvent.setup();
    montarEditor({
      "PUT /orgs/acme/perfiles/estandar": () => ({
        perfil: perfilConfig("estandar", { version: 4 }),
        avisos: ["El rol «redactor» no tiene modelo para foundry.", "El effort high no lo admite claude-sonnet-5-5."],
      }),
    });

    await user.click(await screen.findByRole("button", { name: "Guardar perfil" }));
    expect(await screen.findByText("Guardado con avisos:")).toBeInTheDocument();
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual(["El rol «redactor» no tiene modelo para foundry.", "El effort high no lo admite claude-sonnet-5-5."]);
    expect(screen.queryByText(/^Guardado ·/)).toBeNull();
  });

  it("un 409 de versión se explica como conflicto e indica la versión vigente", async () => {
    const user = userEvent.setup();
    montarEditor({ "PUT /orgs/acme/perfiles/estandar": () => json(409, { detalle: "perfil en versión 5, recibida 3", version_actual: 5 }) });

    await user.click(await screen.findByRole("button", { name: "Guardar perfil" }));
    const aviso = await screen.findByText(/Otra persona modificó este registro/);
    expect(aviso).toHaveTextContent("(versión actual 5)");
    expect(aviso).toHaveTextContent("perfil en versión 5, recibida 3");
    expect(screen.queryByText(/^Guardado ·/)).toBeNull();
  });

  it("un 422 muestra el detalle del servidor y los campos inválidos", async () => {
    const user = userEvent.setup();
    montarEditor({
      "PUT /orgs/acme/perfiles/estandar": () =>
        json(422, {
          detalle: "El perfil no es satisfacible con el catálogo.",
          codigo: "perfil-insatisfacible",
          errores: [{ ruta: "roles.redactor.effort", mensaje: "el modelo no admite effort high" }],
        }),
    });

    await user.click(await screen.findByRole("button", { name: "Guardar perfil" }));
    const alerta = await screen.findByRole("alert");
    expect(alerta).toHaveTextContent("El perfil no es satisfacible con el catálogo.");
    expect(alerta).toHaveTextContent("roles.redactor.effort: el modelo no admite effort high");
  });

  it("no se puede guardar un perfil sin roles ni añadir un rol vacío o repetido", async () => {
    const user = userEvent.setup();
    const s = montarEditor({});

    const anadir = await screen.findByRole("button", { name: "Añadir rol" });
    expect(anadir).toBeDisabled(); // nombre vacío
    await user.type(screen.getByLabelText("Nombre del nuevo rol"), "redactor");
    expect(anadir).toBeDisabled(); // ya existe
    await user.clear(screen.getByLabelText("Nombre del nuevo rol"));
    await user.type(screen.getByLabelText("Nombre del nuevo rol"), "  critico  ");
    expect(anadir).toBeEnabled();
    await user.click(anadir);
    expect(screen.getByLabelText("Modelo anthropic para critico")).toHaveValue(""); // el nombre se recorta
    expect(screen.getByLabelText("Nombre del nuevo rol")).toHaveValue("");

    await user.click(screen.getByRole("button", { name: "Quitar rol redactor" }));
    await user.click(screen.getByRole("button", { name: "Quitar rol critico" }));
    expect(screen.getByRole("button", { name: "Guardar perfil" })).toBeDisabled();
    expect(s.de("PUT", "/orgs/acme/perfiles/estandar")).toHaveLength(0);
  });

  it("vaciar un modelo lo quita del rol y vaciar el contexto mínimo envía null", async () => {
    const user = userEvent.setup();
    const s = montarEditor(
      { "PUT /orgs/acme/perfiles/estandar": () => ({ perfil: perfilConfig("estandar", { version: 4 }), avisos: [] }) },
      {
        inicial: {
          ...inicial,
          roles: { redactor: { modelo: { anthropic: "claude-sonnet-5-5" }, effort: "low", structured_outputs: false, contexto_min_tokens: 8000 } },
        },
      },
    );

    await user.clear(await screen.findByLabelText("Modelo anthropic para redactor"));
    await user.clear(screen.getByLabelText("Contexto mínimo para redactor"));
    await user.selectOptions(screen.getByLabelText("Effort para redactor"), "—");
    await user.click(screen.getByRole("button", { name: "Guardar perfil" }));
    await screen.findByText(/^Guardado · versión 4\b/);
    expect(s.de("PUT", "/orgs/acme/perfiles/estandar")[0]!.cuerpo.roles).toEqual({
      redactor: { modelo: {}, effort: null, structured_outputs: false, contexto_min_tokens: null },
    });
  });

  it("de un workspace guarda en su ámbito (?workspace=) y sin versión cuando es un ajuste nuevo", async () => {
    const user = userEvent.setup();
    const { version: _omitida, ...sinVersion } = inicial;
    const s = montarEditor(
      { "PUT /orgs/acme/perfiles/estandar": () => ({ perfil: perfilConfig("estandar", { workspace: "cert", version: 1 }), avisos: [] }) },
      { ws: "cert", inicial: sinVersion },
    );

    await user.click(await screen.findByRole("button", { name: "Guardar perfil" }));
    await screen.findByText(/^Guardado · versión 1\b/);
    const [put] = s.de("PUT", "/orgs/acme/perfiles/estandar");
    expect(put!.consulta.get("workspace")).toBe("cert");
    expect(put!.cuerpo).not.toHaveProperty("version");
  });
});

describe("perfiles de un workspace", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("marca los ajustes propios, hereda los demás y crea un ajuste a partir del heredado", async () => {
    const user = userEvent.setup();
    const est = { perfiles: [estandar, perfilConfig("ligero", { workspace: "cert", version: 2 })] };
    const s = servidorFalso({
      "GET /orgs/acme/perfiles": () => est.perfiles,
      "GET /orgs/acme/catalogo": () => [],
      "PUT /orgs/acme/perfiles/estandar": aceptar(est),
    });
    montarConQuery(<Perfiles org="acme" ws="cert" editable />);

    // «ligero» tiene ajuste propio; «estandar» solo el de la organización.
    expect(await screen.findByRole("tab", { name: "ligero propio" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "estandar" })).toBeInTheDocument();
    expect(s.de("GET", "/orgs/acme/perfiles")[0]!.consulta.get("workspace")).toBe("cert");

    expect(screen.getByText("El workspace usa el perfil «estandar» de la organización")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Crear ajuste propio del workspace" }));
    // Parte de lo heredado de la organización.
    expect(screen.getByLabelText("Modelo anthropic para redactor")).toHaveValue("claude-sonnet-5-5");
    await user.selectOptions(screen.getByLabelText("Effort para redactor"), "high");
    await user.click(screen.getByRole("button", { name: "Guardar perfil" }));

    // Tras guardar, la pestaña pasa a tener ajuste propio.
    expect(await screen.findByRole("tab", { name: "estandar propio" })).toBeInTheDocument();
    const [put] = s.de("PUT", "/orgs/acme/perfiles/estandar");
    expect(put!.consulta.get("workspace")).toBe("cert");
    expect(put!.cuerpo).not.toHaveProperty("version"); // alta: no hay versión que proteger
    expect(put!.cuerpo.roles.redactor.effort).toBe("high");
  });

  it("tras un 409 recarga lo vigente y el siguiente guardado envía la versión nueva", async () => {
    const user = userEvent.setup();
    let vigente = perfilConfig("estandar", { version: 3 });
    const s = servidorFalso({
      "GET /orgs/acme/perfiles": () => [vigente],
      "GET /orgs/acme/catalogo": () => [],
      "PUT /orgs/acme/perfiles/estandar": (l) => {
        if (l.cuerpo.version !== vigente.version) return json(409, { detalle: "versión desactualizada", version_actual: vigente.version });
        vigente = perfilConfig("estandar", { ...l.cuerpo, version: vigente.version + 1 });
        return { perfil: vigente, avisos: [] };
      },
    });
    montarConQuery(<Perfiles org="acme" editable />);

    await screen.findByText(/Versión 3/);
    // Otra persona cambia el effort y sube la versión mientras se edita.
    vigente = perfilConfig("estandar", {
      version: 4,
      roles: { redactor: { modelo: { anthropic: "claude-sonnet-5-5" }, effort: "xhigh", structured_outputs: false } },
    });
    await user.click(screen.getByRole("button", { name: "Guardar perfil" }));

    await waitFor(() => expect(screen.getByLabelText("Effort para redactor")).toHaveValue("xhigh"));
    expect(screen.getByText(/Versión 4/)).toBeInTheDocument();
    // El editor se remontó con la versión 4 y el aviso de conflicto no se perdió con el viejo.
    expect(screen.getByText(/Otra persona modificó este registro/)).toHaveTextContent("(versión actual 4)");
    await user.click(screen.getByRole("button", { name: "Guardar perfil" }));
    await waitFor(() => expect(s.de("PUT", "/orgs/acme/perfiles/estandar")).toHaveLength(2));
    expect(s.de("PUT", "/orgs/acme/perfiles/estandar").map((l) => l.cuerpo.version)).toEqual([3, 4]);
    // Guardar de nuevo retira el aviso y anuncia el guardado.
    expect(await screen.findByText(/^Guardado · versión 5\b/)).toBeInTheDocument();
    expect(screen.queryByText(/Otra persona modificó este registro/)).toBeNull();
  });

  it("el aviso de conflicto no pasa a otra pestaña de perfil ni vuelve al regresar", async () => {
    const user = userEvent.setup();
    let vigente = perfilConfig("estandar", { version: 3 });
    servidorFalso({
      "GET /orgs/acme/perfiles": () => [vigente, perfilConfig("ligero", { version: 1 })],
      "GET /orgs/acme/catalogo": () => [],
      "PUT /orgs/acme/perfiles/estandar": () => json(409, { detalle: "versión desactualizada", version_actual: vigente.version }),
    });
    montarConQuery(<Perfiles org="acme" editable />);

    await screen.findByText(/Versión 3/);
    vigente = perfilConfig("estandar", { version: 4 });
    await user.click(screen.getByRole("button", { name: "Guardar perfil" }));
    await screen.findByText(/Versión 4/);
    expect(screen.getByText(/Otra persona modificó este registro/)).toBeInTheDocument();

    // Cada perfil tiene su aviso: «ligero» no hereda el conflicto de «estandar»...
    await user.click(screen.getByRole("tab", { name: /ligero/ }));
    expect(await screen.findByText(/Versión 1/)).toBeInTheDocument();
    expect(screen.queryByText(/Otra persona modificó este registro/)).toBeNull();
    // ...y al volver a «estandar» el editor es nuevo y arranca sin el aviso viejo.
    await user.click(screen.getByRole("tab", { name: /estandar/ }));
    expect(await screen.findByText(/Versión 4/)).toBeInTheDocument();
    expect(screen.queryByText(/Otra persona modificó este registro/)).toBeNull();
  });
});
