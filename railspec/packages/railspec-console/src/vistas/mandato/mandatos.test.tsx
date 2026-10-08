import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MandateGetSalida } from "../../api/tipos";
import { decision, estadoUnidad, HUELLA, mandato, resumen, salidaGet, unidadDeMandato } from "../../pruebas/mandatos";
import { json, montarConQuery, montarEnRuta, servidorFalso, vinculo, yo } from "../../pruebas/servidor";
import { DetalleMandato } from "./DetalleMandato";
import { DialogoAprobarMandato } from "./DialogoAprobarMandato";
import { Mandatos } from "./Mandatos";
import { MandatosParados } from "./MandatosParados";

// Pantalla de mandatos (contrato 1.11): lista, detalle, aprobar/renovar sobre la huella del servidor,
// revocar con motivo, revisar decisiones y el formulario de alta/edición.

const RAIZ = "/orgs/acme/workspaces/cert";
const alcance = { org: "acme", workspace: "cert" };
const desarrollador = () => yo(null, "desarrollador");

const montarLista = () => montarEnRuta(() => <Mandatos />, "/$org/$ws/mandatos", "/acme/cert/mandatos");
const montarDetalle = () => montarEnRuta(() => <DetalleMandato />, "/$org/$ws/mandatos/$mandato", "/acme/cert/mandatos/pdf-a");
const cuerpos = (s: ReturnType<typeof servidorFalso>, tool: string) => s.de("POST", `/tools/${tool}`).map((l) => l.cuerpo);

describe("lista de mandatos", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("muestra id, título, modo, estado, vigencia, parada, unidades y decisiones pendientes", async () => {
    servidorFalso({
      "GET /yo": () => desarrollador(),
      "POST /tools/mandate.list": () => ({
        mandatos: [
          resumen(),
          resumen({ id: "vieja", titulo: "Limpieza vieja", estado: "aprobado", vigente: false, vigente_hasta: "2026-09-01T06:00:00Z", decisiones_pendientes: 0 }),
          resumen({
            id: "frenada",
            titulo: "Frenada por gate",
            modo: "supervisado",
            estado: "parado",
            vigente: false,
            causa_parada: "gate-escalado",
            unidades: 2,
            decisiones_pendientes: 0,
          }),
          resumen({ id: "nueva", titulo: "Borrador", estado: "propuesto", vigente: false, vigente_hasta: null, unidades: 0, decisiones_pendientes: 0 }),
        ],
      }),
    });
    montarLista();

    const filaDe = async (texto: string) => (await screen.findByText(texto)).closest("tr") as HTMLElement;
    const viva = await filaDe("Migrar emisión de certificados a PDF/A");
    expect(within(viva).getByText("pdf-a")).toBeInTheDocument();
    expect(within(viva).getByText("desatendido")).toBeInTheDocument();
    expect(within(viva).getByText("aprobado")).toBeInTheDocument();
    expect(within(viva).getByLabelText("1 de 3 unidades")).toHaveTextContent("1/3");
    expect(within(viva).getByText("1")).toBeInTheDocument();

    // Aprobado pero con la vigencia pasada: «caducado».
    const vieja = await filaDe("Limpieza vieja");
    expect(within(vieja).getByText("caducado")).toBeInTheDocument();
    expect(within(vieja).queryByText("aprobado")).toBeNull();

    const frenada = await filaDe("Frenada por gate");
    expect(within(frenada).getByText("parado")).toBeInTheDocument();
    expect(within(frenada).getByText("gate-escalado")).toBeInTheDocument();
    expect(within(frenada).getByLabelText("2 de 3 unidades")).toBeInTheDocument();

    const nueva = await filaDe("Borrador");
    expect(within(nueva).getByText("propuesto")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Borrador" })).toBeInTheDocument();
  });

  it("sin mandatos lo dice, y un lector no ve «Nuevo mandato»", async () => {
    servidorFalso({ "GET /yo": () => yo(null, "lector"), "POST /tools/mandate.list": () => ({ mandatos: [] }) });
    montarLista();
    expect(await screen.findByText("Este workspace aún no tiene mandatos")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Nuevo mandato" })).toBeNull();
  });

  it("un error de la lista no se disfraza de vacío", async () => {
    servidorFalso({ "GET /yo": () => desarrollador(), "POST /tools/mandate.list": () => json(403, { detalle: "sin rol" }) });
    montarLista();
    expect(await screen.findByText("sin rol")).toBeInTheDocument();
    expect(screen.queryByText("Este workspace aún no tiene mandatos")).toBeNull();
  });
});

describe("formulario de nuevo mandato", () => {
  afterEach(() => vi.unstubAllGlobals());

  const servidor = (extra: Parameters<typeof servidorFalso>[0] = {}) =>
    servidorFalso({
      "GET /yo": () => desarrollador(),
      "POST /tools/mandate.list": () => ({ mandatos: [] }),
      [`GET ${RAIZ}/repositorios`]: () => [vinculo("api"), vinculo("lib")],
      ...extra,
    });

  async function abrirFormulario() {
    const user = userEvent.setup();
    montarLista();
    await user.click(await screen.findByRole("button", { name: "Nuevo mandato" }));
    const dialogo = await screen.findByRole("dialog", { name: "Nuevo mandato" });
    await within(dialogo).findByLabelText("Título");
    return { user, dialogo };
  }

  it("ofrece los repositorios vinculados y valida antes de enviar (un desatendido sin tope no se envía)", async () => {
    const s = servidor({ "POST /tools/mandate.propose": () => ({ mandato: mandato(), huella: HUELLA }) });
    const { user, dialogo } = await abrirFormulario();

    expect(within(dialogo).getByRole("checkbox", { name: "api" })).toBeInTheDocument();
    expect(within(dialogo).getByRole("checkbox", { name: "lib" })).toBeInTheDocument();
    await user.click(within(dialogo).getByRole("button", { name: "Crear mandato" }));
    const alerta = await within(dialogo).findByRole("alert");
    expect(alerta).toHaveTextContent(/id debe ser un slug/);
    expect(alerta).toHaveTextContent(/título es obligatorio/);
    expect(alerta).toHaveTextContent(/al menos un repositorio/);

    await user.type(within(dialogo).getByLabelText(/^Id \(es el plan/), "pdf-a");
    await user.type(within(dialogo).getByLabelText("Título"), "Migrar a PDF/A");
    await user.type(within(dialogo).getByLabelText("Objetivo"), "Emitir en PDF/A");
    await user.click(within(dialogo).getByRole("checkbox", { name: "api" }));
    await user.selectOptions(within(dialogo).getByLabelText("Modo"), "desatendido");
    await user.click(within(dialogo).getByRole("button", { name: "Crear mandato" }));

    expect(await within(dialogo).findByText(/desatendido necesita al menos un tope de presupuesto/)).toBeInTheDocument();
    expect(cuerpos(s, "mandate.propose")).toHaveLength(0);
  });

  it("valida la vigencia y los reintentos", async () => {
    const s = servidor();
    const { user, dialogo } = await abrirFormulario();
    await user.clear(within(dialogo).getByLabelText("Vigencia (horas)"));
    await user.type(within(dialogo).getByLabelText("Vigencia (horas)"), "200");
    await user.clear(within(dialogo).getByLabelText("Reintentos de parada"));
    await user.type(within(dialogo).getByLabelText("Reintentos de parada"), "4");
    await user.click(within(dialogo).getByRole("button", { name: "Crear mandato" }));
    const alerta = await within(dialogo).findByRole("alert");
    expect(alerta).toHaveTextContent(/vigencia es un entero de 1 a 168/);
    expect(alerta).toHaveTextContent(/reintentos de parada son un entero de 0 a 3/);
    expect(cuerpos(s, "mandate.propose")).toHaveLength(0);
  });

  it("crea el mandato con límites, rutas, topes y delegaciones, sin version_vista", async () => {
    const s = servidor({ "POST /tools/mandate.propose": () => ({ mandato: mandato(), huella: HUELLA }) });
    const { user, dialogo } = await abrirFormulario();

    await user.type(within(dialogo).getByLabelText(/^Id \(es el plan/), "pdf-a");
    await user.type(within(dialogo).getByLabelText("Título"), "Migrar a PDF/A");
    await user.type(within(dialogo).getByLabelText("Objetivo"), "Emitir en PDF/A");
    await user.click(within(dialogo).getByRole("checkbox", { name: "api" }));
    await user.selectOptions(within(dialogo).getByLabelText("Modo"), "desatendido");
    await user.clear(within(dialogo).getByLabelText("Máx. unidades"));
    await user.type(within(dialogo).getByLabelText("Máx. unidades"), "3");
    await user.type(within(dialogo).getByLabelText("Rutas permitidas"), "src/pdf/**{Enter}tests/pdf/**");
    await user.type(within(dialogo).getByLabelText("Tokens máx."), "2000000");
    await user.type(within(dialogo).getByLabelText("Costo USD máx."), "25");
    await user.click(within(dialogo).getByRole("button", { name: "Añadir delegación" }));
    await user.selectOptions(within(dialogo).getByLabelText("Tipo de la delegación 1"), "reservada");
    await user.type(within(dialogo).getByLabelText("Texto de la delegación 1"), "Cambios de esquema");
    await user.click(within(dialogo).getByRole("button", { name: "Crear mandato" }));

    await waitFor(() => expect(cuerpos(s, "mandate.propose")).toHaveLength(1));
    expect(cuerpos(s, "mandate.propose")[0]).toEqual({
      alcance,
      id: "pdf-a",
      contenido: {
        titulo: "Migrar a PDF/A",
        objetivo: "Emitir en PDF/A",
        modo: "desatendido",
        limites: {
          repositorios: ["api"],
          max_unidades: 3,
          rutas_permitidas: ["src/pdf/**", "tests/pdf/**"],
          presupuesto: { tokens_max: 2000000, costo_usd_max: 25 },
          reintentos_parada: 0,
          vigencia_horas: 24,
        },
        delegaciones: [{ id: "D-1", tipo: "reservada", texto: "Cambios de esquema" }],
      },
    });
  });
});

describe("detalle de un mandato", () => {
  afterEach(() => vi.unstubAllGlobals());

  const servidor = (salida: MandateGetSalida = salidaGet(), extra: Parameters<typeof servidorFalso>[0] = {}, rol = desarrollador) =>
    servidorFalso({ "GET /yo": () => rol(), "POST /tools/mandate.get": () => salida, ...extra });

  it("muestra el contenido completo, el presupuesto frente al consumo, la historia, las unidades y las decisiones", async () => {
    servidor(
      salidaGet({
        unidades: [
          unidadDeMandato({ decisiones_pendientes: 1 }),
          unidadDeMandato({ unidad: "0002-firma", titulo: "Firmar", diferida: true, causa_parada: "unidad-amparada-fallida" }),
        ],
      }),
    );
    montarDetalle();

    expect(await screen.findByRole("heading", { name: "Migrar emisión de certificados a PDF/A" })).toBeInTheDocument();
    expect(screen.getByText(/Dejar la emisión de certificados en PDF\/A con firma/)).toBeInTheDocument();
    expect(screen.getByText("src/pdf/**")).toBeInTheDocument();
    expect(screen.getByText("api")).toBeInTheDocument();
    expect(screen.getByText(/hasta 3/)).toBeInTheDocument();
    expect(screen.getByText(/Tokens: 120.?000 de 2.?000.?000/)).toBeInTheDocument();
    expect(screen.getByText(/Costo: .*3,50.* de .*25,00/)).toBeInTheDocument();
    // Delegaciones con su tipo.
    expect(screen.getByText("pre-decidida")).toBeInTheDocument();
    expect(screen.getByText("con-criterio")).toBeInTheDocument();
    expect(screen.getByText("reservada")).toBeInTheDocument();
    expect(screen.getByText(/Cualquier cambio de esquema/)).toBeInTheDocument();
    // Historia de aprobaciones.
    expect(screen.getByText("Esta noche, solo api.")).toBeInTheDocument();
    expect(screen.getByText(`${HUELLA.slice(0, 12)}…`)).toBeInTheDocument();
    // Unidades, enlazadas, con sus insignias.
    expect(screen.getByRole("link", { name: "Emitir PDF/A" })).toHaveAttribute("href", "/acme/cert/unidades/0001-emitir-pdfa");
    const firma = screen.getByRole("link", { name: "Firmar" }).closest("tr") as HTMLElement;
    expect(within(firma).getByText("diferida")).toBeInTheDocument();
    expect(within(firma).getByText("unidad-amparada-fallida")).toBeInTheDocument();
    const emitir = screen.getByRole("link", { name: "Emitir PDF/A" }).closest("tr") as HTMLElement;
    expect(within(emitir).getByText("1 decisión(es) pendiente(s)")).toBeInTheDocument();
    // Decisión tomada.
    expect(screen.getByText("DD-1")).toBeInTheDocument();
    expect(screen.getByText("pendiente de revisión")).toBeInTheDocument();
    expect(screen.getByText(/Renombrar la función/)).toBeInTheDocument();
  });

  it("un mandato aprobado pero fuera de vigencia se ve «caducado» con su motivo; uno parado muestra la causa y qué hacer", async () => {
    servidor(
      salidaGet({
        vigente: false,
        motivo_no_vigente: "la aprobación del mandato caducó el 2026-10-01 06:00 UTC",
      }),
    );
    montarDetalle();
    expect((await screen.findAllByText("caducado")).length).toBeGreaterThan(0);
    expect(screen.queryByText("aprobado")).toBeNull();
    expect(screen.getByText(/caducó el 2026-10-01 06:00 UTC/)).toBeInTheDocument();
    expect(screen.getByText(/No ampara trabajo ahora/)).toBeInTheDocument();
  });

  it("muestra la parada del mandato con su explicación", async () => {
    const parado = mandato({
      estado: "parado",
      parada: { causa: "presupuesto-mandato", unidad: "0001-emitir-pdfa", detalle: "Se alcanzó el tope de costo.", en: "2026-09-30T22:00:00Z" },
    });
    servidor(salidaGet({ mandato: parado, vigente: false, motivo_no_vigente: "el mandato está parado" }));
    montarDetalle();
    expect(await screen.findByText("Se alcanzó el tope de costo.")).toBeInTheDocument();
    expect(screen.getAllByText("presupuesto-mandato").length).toBeGreaterThan(0);
    expect(screen.getByText(/Edita el tope \(vuelve a propuesto\) y apruébalo/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Renovar" })).toBeInTheDocument();
  });

  it("un lector lo ve todo pero sin acciones ni revisión; un revocado ya no tiene acciones", async () => {
    servidor(salidaGet(), {}, () => yo(null, "lector"));
    montarDetalle();
    await screen.findByRole("heading", { name: "Migrar emisión de certificados a PDF/A" });
    expect(screen.queryByRole("button", { name: "Renovar" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Editar" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Revocar" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Aceptar DD-1" })).toBeNull();
  });

  it("un mandato revocado muestra el motivo y no ofrece acciones", async () => {
    const revocado = mandato({
      estado: "revocado",
      revocacion: { actor: { tipo: "humano", canal: "consola", login: "julian" }, en: "2026-10-01T00:00:00Z", motivo: "Ya no hace falta" },
    });
    servidor(salidaGet({ mandato: revocado, vigente: false }));
    montarDetalle();
    expect(await screen.findByText(/Ya no hace falta/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revocar" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Editar" })).toBeNull();
  });

  describe("aprobar y renovar", () => {
    it("aprobar un propuesto muestra el contenido íntegro y la huella, y la envía con la versión vista y el comentario", async () => {
      const propuesto = mandato({ estado: "propuesto", aprobaciones: [], version: 3 });
      const s = servidor(salidaGet({ mandato: propuesto, vigente: false }), {
        "POST /tools/mandate.approve": () => ({ mandato: mandato(), huella: HUELLA }),
      });
      const user = userEvent.setup();
      montarDetalle();

      await user.click(await screen.findByRole("button", { name: "Aprobar" }));
      const dialogo = await screen.findByRole("dialog", { name: "Aprobar el mandato pdf-a" });
      expect(within(dialogo).getByText(/sin checkpoints humanos/)).toBeInTheDocument();
      expect(within(dialogo).getByText(/12 h desde ahora/)).toBeInTheDocument();
      expect(within(dialogo).getByText(/hasta 3 unidades en api/)).toBeInTheDocument();
      expect(within(dialogo).getByText(/src\/pdf\/\*\*/, { selector: "li" })).toBeInTheDocument();
      expect(within(dialogo).getByTestId("huella-mandato")).toHaveTextContent(HUELLA);
      await user.type(within(dialogo).getByLabelText("Comentario"), "  Esta noche  ");
      await user.click(within(dialogo).getByRole("button", { name: "Aprobar mandato" }));

      await waitFor(() => expect(cuerpos(s, "mandate.approve")).toHaveLength(1));
      expect(cuerpos(s, "mandate.approve")[0]).toEqual({ alcance, id: "pdf-a", version_vista: 3, huella: HUELLA, comentario: "Esta noche" });
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    });

    it("renovar sin comentario no manda el campo", async () => {
      const s = servidor(salidaGet(), { "POST /tools/mandate.approve": () => ({ mandato: mandato(), huella: HUELLA }) });
      const user = userEvent.setup();
      montarDetalle();
      await user.click(await screen.findByRole("button", { name: "Renovar" }));
      const dialogo = await screen.findByRole("dialog", { name: "Renovar el mandato pdf-a" });
      await user.click(within(dialogo).getByRole("button", { name: "Renovar mandato" }));
      await waitFor(() => expect(cuerpos(s, "mandate.approve")).toHaveLength(1));
      expect(cuerpos(s, "mandate.approve")[0]).toEqual({ alcance, id: "pdf-a", version_vista: 2, huella: HUELLA });
    });

    it("ante un 409 recarga el mandato, avisa y muestra la huella nueva sin cerrar el diálogo", async () => {
      const nueva = "f".repeat(64);
      let lecturas = 0;
      const s = servidor(salidaGet(), {
        "POST /tools/mandate.get": () => {
          lecturas += 1;
          return lecturas === 1 ? salidaGet() : salidaGet({ mandato: mandato({ version: 3 }), huella: nueva });
        },
        "POST /tools/mandate.approve": () => json(409, { codigo: "conflicto-version", detalle: "el contenido cambió", version_estado: 3 }),
      });
      const user = userEvent.setup();
      montarDetalle();
      await user.click(await screen.findByRole("button", { name: "Renovar" }));
      const dialogo = await screen.findByRole("dialog");
      await user.click(within(dialogo).getByRole("button", { name: "Renovar mandato" }));

      expect(await within(dialogo).findByText(/El mandato cambió mientras lo revisabas; ya se recargó/)).toBeInTheDocument();
      await waitFor(() => expect(within(dialogo).getByTestId("huella-mandato")).toHaveTextContent(nueva));
      expect(screen.getByRole("dialog")).toBeInTheDocument();

      // Al volver a aprobar se manda lo nuevo: la huella y la versión recargadas.
      await user.click(within(dialogo).getByRole("button", { name: "Renovar mandato" }));
      await waitFor(() => expect(cuerpos(s, "mandate.approve")).toHaveLength(2));
      expect(cuerpos(s, "mandate.approve")[1]).toMatchObject({ version_vista: 3, huella: nueva });
    });
  });

  describe("revocar", () => {
    it("exige motivo, lo manda recortado con la versión vista y cierra", async () => {
      const s = servidor(salidaGet(), { "POST /tools/mandate.revoke": () => ({ mandato: mandato({ estado: "revocado" }), huella: HUELLA }) });
      const user = userEvent.setup();
      montarDetalle();
      await user.click(await screen.findByRole("button", { name: "Revocar" }));
      const dialogo = await screen.findByRole("dialog", { name: "Revocar el mandato pdf-a" });
      const confirmar = within(dialogo).getByRole("button", { name: "Revocar definitivamente" });

      expect(confirmar).toBeDisabled();
      await user.type(within(dialogo).getByLabelText("Motivo"), "   ");
      expect(confirmar).toBeDisabled();
      await user.type(within(dialogo).getByLabelText("Motivo"), "  ya no hace falta  ");
      expect(confirmar).toBeEnabled();
      await user.click(confirmar);

      await waitFor(() => expect(cuerpos(s, "mandate.revoke")).toHaveLength(1));
      expect(cuerpos(s, "mandate.revoke")[0]).toEqual({ alcance, id: "pdf-a", version_vista: 2, motivo: "ya no hace falta" });
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    });

    it("ante un 409 avisa y no cierra", async () => {
      servidor(salidaGet(), { "POST /tools/mandate.revoke": () => json(409, { codigo: "conflicto-version", detalle: "versión vieja" }) });
      const user = userEvent.setup();
      montarDetalle();
      await user.click(await screen.findByRole("button", { name: "Revocar" }));
      const dialogo = await screen.findByRole("dialog");
      await user.type(within(dialogo).getByLabelText("Motivo"), "x");
      await user.click(within(dialogo).getByRole("button", { name: "Revocar definitivamente" }));
      expect(await within(dialogo).findByText(/cambió mientras lo revisabas; ya se recargó/)).toBeInTheDocument();
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });

  describe("editar", () => {
    it("avisa que volverá a propuesto, parte del contenido actual y manda la versión vista", async () => {
      const s = servidor(salidaGet(), {
        [`GET ${RAIZ}/repositorios`]: () => [vinculo("api")],
        "POST /tools/mandate.propose": () => ({ mandato: mandato({ estado: "propuesto", version: 3 }), huella: HUELLA }),
      });
      const user = userEvent.setup();
      montarDetalle();
      await user.click(await screen.findByRole("button", { name: "Editar" }));
      const dialogo = await screen.findByRole("dialog", { name: "Editar el mandato pdf-a" });
      expect(within(dialogo).getByText(/volver a «propuesto»|vuelve a «propuesto»|volverá a «propuesto»/)).toBeInTheDocument();
      expect(await within(dialogo).findByLabelText("Título")).toHaveValue("Migrar emisión de certificados a PDF/A");
      expect(within(dialogo).getByLabelText(/^Id \(es el plan/)).toBeDisabled();
      await user.clear(within(dialogo).getByLabelText("Máx. unidades"));
      await user.type(within(dialogo).getByLabelText("Máx. unidades"), "4");
      await user.click(within(dialogo).getByRole("button", { name: "Guardar como propuesto" }));

      await waitFor(() => expect(cuerpos(s, "mandate.propose")).toHaveLength(1));
      const enviado = cuerpos(s, "mandate.propose")[0];
      expect(enviado).toMatchObject({ alcance, id: "pdf-a", version_vista: 2 });
      expect(enviado.contenido.limites.max_unidades).toBe(4);
      expect(enviado.contenido.delegaciones).toHaveLength(3);
    });

    it("ante un 409 avisa que no se guardó nada y no cierra", async () => {
      servidor(salidaGet(), {
        [`GET ${RAIZ}/repositorios`]: () => [vinculo("api")],
        "POST /tools/mandate.propose": () => json(409, { codigo: "conflicto-version", detalle: "versión vieja", version_estado: 3 }),
      });
      const user = userEvent.setup();
      montarDetalle();
      await user.click(await screen.findByRole("button", { name: "Editar" }));
      const dialogo = await screen.findByRole("dialog");
      await within(dialogo).findByLabelText("Título");
      await user.click(within(dialogo).getByRole("button", { name: "Guardar como propuesto" }));
      expect(await within(dialogo).findByText(/tus cambios no se guardaron/)).toBeInTheDocument();
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });

  describe("revisar decisiones", () => {
    const detalleUnidad = (version: number) => ({ estado: estadoUnidad({ version }), orden_vigente: null });
    const conUnidad = (s: Parameters<typeof servidorFalso>[0] = {}) => ({
      [`GET ${RAIZ}/unidades/0001-emitir-pdfa`]: () => detalleUnidad(11),
      "POST /tools/mandate.review": () => ({ estado: estadoUnidad() }),
      ...s,
    });

    it("revertir exige comentario y manda la versión de la unidad leída al revisar", async () => {
      const s = servidor(salidaGet(), conUnidad());
      const user = userEvent.setup();
      montarDetalle();
      const revertir = await screen.findByRole("button", { name: "Revertir DD-1" });

      expect(revertir).toBeDisabled();
      await user.type(screen.getByLabelText("Comentario de DD-1"), "   ");
      expect(revertir).toBeDisabled();
      await user.type(screen.getByLabelText("Comentario de DD-1"), "  el nombre rompe el estilo  ");
      expect(revertir).toBeEnabled();
      await user.click(revertir);

      await waitFor(() => expect(cuerpos(s, "mandate.review")).toHaveLength(1));
      expect(cuerpos(s, "mandate.review")[0]).toEqual({
        unidad: { org: "acme", workspace: "cert", unidad: "0001-emitir-pdfa" },
        decision: "DD-1",
        resultado: "revertida",
        comentario: "el nombre rompe el estilo",
        version_vista: 11,
      });
    });

    it("aceptar puede ir sin comentario y no manda el campo", async () => {
      const s = servidor(salidaGet(), conUnidad());
      const user = userEvent.setup();
      montarDetalle();
      await user.click(await screen.findByRole("button", { name: "Aceptar DD-1" }));
      await waitFor(() => expect(cuerpos(s, "mandate.review")).toHaveLength(1));
      expect(cuerpos(s, "mandate.review")[0]).toEqual({
        unidad: { org: "acme", workspace: "cert", unidad: "0001-emitir-pdfa" },
        decision: "DD-1",
        resultado: "aceptada",
        version_vista: 11,
      });
    });

    it("una decisión ya revisada muestra quién y cómo, sin acciones", async () => {
      servidor(
        salidaGet({
          decisiones: [
            {
              unidad: "0001-emitir-pdfa",
              decision: decision("DD-1", {
                revision: { resultado: "revertida", actor: { tipo: "humano", canal: "consola", login: "ana" }, en: "2026-10-01T09:00:00Z", comentario: "no cuadra" },
              }),
            },
          ],
        }),
        conUnidad(),
      );
      montarDetalle();
      expect(await screen.findByText("revertida")).toBeInTheDocument();
      expect(screen.getByText(/«no cuadra»/)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Aceptar DD-1" })).toBeNull();
    });

    it("un 409 al revisar avisa que la unidad cambió", async () => {
      servidor(salidaGet(), conUnidad({ "POST /tools/mandate.review": () => json(409, { codigo: "conflicto-version", detalle: "versión vieja" }) }));
      const user = userEvent.setup();
      montarDetalle();
      await user.click(await screen.findByRole("button", { name: "Aceptar DD-1" }));
      expect(await screen.findByText(/La unidad cambió mientras revisabas; ya se recargó/)).toBeInTheDocument();
    });
  });
});

describe("diálogo de aprobación", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sin la huella del servidor no se puede aprobar ni se envía nada", async () => {
    const s = servidorFalso({ "POST /tools/mandate.approve": () => ({ mandato: mandato(), huella: HUELLA }) });
    const user = userEvent.setup();
    montarConQuery(<DialogoAprobarMandato salida={salidaGet({ huella: "" })} org="acme" ws="cert" alCerrar={() => undefined} />);

    expect(screen.getByText(/no devolvió la huella/)).toBeInTheDocument();
    const boton = screen.getByRole("button", { name: "Renovar mandato" });
    expect(boton).toBeDisabled();
    await user.click(boton);
    expect(s.de("POST", "/tools/mandate.approve")).toHaveLength(0);
  });
});

describe("aviso de mandatos parados en el tablero", () => {
  afterEach(() => vi.unstubAllGlobals());
  const montar = () => montarEnRuta(() => <MandatosParados org="acme" ws="cert" />, "/$org/$ws/tablero", "/acme/cert/tablero");

  it("cuenta los parados y enlaza a la pantalla de mandatos", async () => {
    const s = servidorFalso({
      "POST /tools/mandate.list": () => ({ mandatos: [resumen({ estado: "parado" }), resumen({ id: "otro", estado: "parado" })] }),
    });
    montar();
    expect(await screen.findByText(/2 mandatos están parados/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ver mandatos" })).toHaveAttribute("href", "/acme/cert/mandatos");
    expect(s.de("POST", "/tools/mandate.list")[0]!.cuerpo).toMatchObject({ estado: ["parado"] });
  });

  it("sin parados no pinta nada", async () => {
    servidorFalso({ "POST /tools/mandate.list": () => ({ mandatos: [] }) });
    const { container } = montar();
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});
