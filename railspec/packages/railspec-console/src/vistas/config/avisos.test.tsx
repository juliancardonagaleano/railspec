import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ConfigAvisos, EstadoAvisos, InformeSemanal } from "../../api/tipos";
import { json, montarConQuery, servidorFalso } from "../../pruebas/servidor";
import { Avisos } from "./Avisos";

// Avisos e informes de la organización: canales, eventos, informe semanal, pruebas y vista previa.
// El URL del webhook es de solo escritura: nunca se pinta y solo viaja al cambiarlo.

const URL_TEAMS = "https://prod-12.westus.logic.azure.com/workflows/secreto-del-webhook";

const config = (o: Partial<ConfigAvisos> = {}): ConfigAvisos => ({
  version: 3,
  activo: false,
  teams: { configurado: true, host: "prod-12.westus.logic.azure.com" },
  correo: { destinatarios: ["equipo@empresa.com"] },
  eventos: { gate_escalado: true, presupuesto_agotado: true },
  informe: { activo: false, dia_semana: 0, hora_utc: 8 },
  ...o,
});

const estado = (o: Partial<EstadoAvisos> = {}): EstadoAvisos => ({
  cifrado: { disponible: true, variable: "RAILSPEC_CLAVE_MAESTRA" },
  canales: { teams: { disponible: true }, correo: { disponible: true } },
  hosts_teams: ["*.logic.azure.com", "*.webhook.office.com"],
  config: config(),
  ultimos: [
    { en: "2026-10-08T10:00:00Z", tipo: "gate-escalado", canal: "teams", resultado: "enviado", codigo: null, workspace: "cert", unidad: "U-001" },
    { en: "2026-10-07T09:00:00Z", tipo: "informe-semanal", canal: "correo", resultado: "error", codigo: "http-4xx", workspace: null, unidad: null },
  ],
  ...o,
});

const informe: InformeSemanal = {
  desde: "2026-10-01T00:00:00Z",
  hasta: "2026-10-08T00:00:00Z",
  totales: { unidades_cerradas: 7, gates_escalados: 2, costo_usd: 12.5, llamadas: 340 },
  por_tier: [
    { tier: "alto", costo_usd: 9, llamadas: 100 },
    { tier: null, costo_usd: 3.5, llamadas: 240 },
  ],
  por_workspace: [{ workspace: "cert", unidades_cerradas: 7, gates_escalados: 2, costo_usd: 12.5 }],
};

const base = (e: EstadoAvisos = estado()) => ({
  "GET /orgs/acme/avisos": () => e,
  "GET /orgs/acme/informe-semanal": () => informe,
});

describe("avisos e informes", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("pinta la configuración, los últimos avisos y la vista previa, sin mostrar el URL del webhook", async () => {
    const s = servidorFalso(base());
    montarConQuery(<Avisos org="acme" />);

    expect(await screen.findByText("Configurado: prod-12.westus.logic.azure.com")).toBeInTheDocument();
    expect(screen.getByLabelText("Avisos activos")).not.toBeChecked();
    const url = screen.getByLabelText("URL del webhook de Teams");
    expect(url).toHaveAttribute("type", "password");
    expect(url).toHaveValue("");
    expect(screen.getByLabelText("Destinatarios")).toHaveValue("equipo@empresa.com");
    expect(screen.getByLabelText("Gate escalado")).toBeChecked();
    expect(screen.getByLabelText("Hora (UTC)")).toHaveValue("8");
    expect(screen.getByLabelText("Día de la semana")).toHaveValue("0");

    const ultimos = await screen.findByRole("table", { name: "Últimos avisos" });
    expect(within(ultimos).getByText("U-001")).toBeInTheDocument();
    expect(within(ultimos).getByText("error: http-4xx")).toBeInTheDocument();

    expect(await screen.findByText("340")).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "Por workspace" })).getByText("cert")).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "Por tier" })).getByText("Sin tier")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("secreto-del-webhook");
    expect(s.noSimuladas).toEqual([]);
  });

  it("guarda con el cuerpo exacto: el URL solo viaja si se escribe y no vuelve a mostrarse", async () => {
    const user = userEvent.setup();
    let actual = config();
    const s = servidorFalso({
      ...base(),
      "GET /orgs/acme/avisos": () => estado({ config: actual }),
      "PUT /orgs/acme/avisos": () => {
        actual = config({ version: 4, activo: true });
        return actual;
      },
    });
    montarConQuery(<Avisos org="acme" />);

    await user.click(await screen.findByLabelText("Avisos activos"));
    await user.type(screen.getByLabelText("URL del webhook de Teams"), URL_TEAMS);
    const dest = screen.getByLabelText("Destinatarios");
    await user.clear(dest);
    await user.type(dest, "a@empresa.com{Enter}  b@empresa.com {Enter}");
    await user.click(screen.getByLabelText("Presupuesto agotado"));
    await user.click(screen.getByLabelText("Informe semanal activo"));
    await user.selectOptions(screen.getByLabelText("Día de la semana"), "4");
    await user.selectOptions(screen.getByLabelText("Hora (UTC)"), "17");
    await user.click(screen.getByRole("button", { name: "Guardar" }));

    expect(await screen.findByText(/^Guardado · versión 4\b/)).toBeInTheDocument();
    expect(s.de("PUT", "/orgs/acme/avisos")[0]?.cuerpo).toEqual({
      activo: true,
      teams_url: URL_TEAMS,
      quitar_teams: false,
      destinatarios: ["a@empresa.com", "b@empresa.com"],
      eventos: { gate_escalado: true, presupuesto_agotado: false },
      informe: { activo: true, dia_semana: 4, hora_utc: 17 },
      version: 3,
    });
    expect(screen.getByLabelText("URL del webhook de Teams")).toHaveValue("");
    expect(document.body.textContent).not.toContain("secreto-del-webhook");
  });

  it("sin cambiar el URL no lo envía, y «Quitar» pide quitarlo al guardar", async () => {
    const user = userEvent.setup();
    const s = servidorFalso({ ...base(), "PUT /orgs/acme/avisos": () => config({ version: 4, teams: { configurado: false, host: null } }) });
    montarConQuery(<Avisos org="acme" />);

    await user.click(await screen.findByRole("button", { name: "Quitar" }));
    expect(screen.getByText("Se quitará al guardar")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(s.de("PUT", "/orgs/acme/avisos")).toHaveLength(1));
    const cuerpo = s.de("PUT", "/orgs/acme/avisos")[0]!.cuerpo;
    expect(cuerpo).not.toHaveProperty("teams_url");
    expect(cuerpo.quitar_teams).toBe(true);
  });

  it("al crear (sin versión) no envía version", async () => {
    const user = userEvent.setup();
    const s = servidorFalso({
      ...base(estado({ config: config({ version: null, teams: { configurado: false, host: null }, correo: { destinatarios: [] } }) })),
      "PUT /orgs/acme/avisos": () => config({ version: 1 }),
    });
    montarConQuery(<Avisos org="acme" />);

    expect(await screen.findByText("Sin configurar")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Quitar" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(s.de("PUT", "/orgs/acme/avisos")).toHaveLength(1));
    expect(s.de("PUT", "/orgs/acme/avisos")[0]!.cuerpo).not.toHaveProperty("version");
  });

  it("sin clave maestra, Teams queda deshabilitado con el motivo y la variable", async () => {
    servidorFalso(base(estado({ cifrado: { disponible: false, variable: "RAILSPEC_CLAVE_MAESTRA" } })));
    montarConQuery(<Avisos org="acme" />);

    expect(await screen.findByText(/RAILSPEC_CLAVE_MAESTRA/)).toBeInTheDocument();
    expect(screen.getByLabelText("URL del webhook de Teams")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Quitar" })).toBeDisabled();
    expect(screen.getByLabelText("Destinatarios")).toBeEnabled();
  });

  it("si el correo no está disponible muestra el motivo y bloquea los destinatarios", async () => {
    servidorFalso(
      base(estado({ canales: { teams: { disponible: true }, correo: { disponible: false, motivo: "el servidor no tiene RAILSPEC_SMTP_HOST" } } })),
    );
    montarConQuery(<Avisos org="acme" />);

    expect(await screen.findByText("el servidor no tiene RAILSPEC_SMTP_HOST")).toBeInTheDocument();
    expect(screen.getByLabelText("Destinatarios")).toBeDisabled();
    expect(screen.getByLabelText("URL del webhook de Teams")).toBeEnabled();
  });

  it("más de 20 destinatarios impide guardar", async () => {
    const user = userEvent.setup();
    servidorFalso(base());
    montarConQuery(<Avisos org="acme" />);

    const dest = await screen.findByLabelText("Destinatarios");
    await user.clear(dest);
    await user.click(dest);
    await user.paste(Array.from({ length: 21 }, (_, i) => `p${i}@empresa.com`).join("\n"));
    expect(screen.getByText("Hay más de 20 destinatarios.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Guardar" })).toBeDisabled();
  });

  it("muestra el detalle de un 422 al guardar", async () => {
    const user = userEvent.setup();
    servidorFalso({ ...base(), "PUT /orgs/acme/avisos": () => json(422, { detalle: "el host del webhook no está permitido" }) });
    montarConQuery(<Avisos org="acme" />);

    await user.click(await screen.findByRole("button", { name: "Guardar" }));
    expect(await screen.findByText(/el host del webhook no está permitido/)).toBeInTheDocument();
  });

  it("envía el aviso de prueba y el informe, y muestra el resultado por canal", async () => {
    const user = userEvent.setup();
    const s = servidorFalso({
      ...base(),
      "POST /orgs/acme/avisos/prueba": (l) => ({
        resultados:
          l.cuerpo.que === "aviso"
            ? [{ canal: "teams", resultado: "enviado", codigo: null }]
            : [{ canal: "correo", resultado: "error", codigo: "destino-no-permitido" }],
      }),
    });
    montarConQuery(<Avisos org="acme" />);

    await user.click(await screen.findByRole("button", { name: "Enviar aviso de prueba" }));
    expect(await screen.findByText("Teams: enviado")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Enviar informe ahora" }));
    expect(await screen.findByText("Correo: error (destino-no-permitido)")).toBeInTheDocument();
    expect(s.de("POST", "/orgs/acme/avisos/prueba").map((l) => l.cuerpo)).toEqual([{ que: "aviso" }, { que: "informe" }]);
  });
});
