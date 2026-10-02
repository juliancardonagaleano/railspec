import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AsignacionRol, Rol, VinculoRepositorio, Workspace } from "../../api/tipos";
import {
  asignacion,
  json,
  montarEnRuta,
  organizacion,
  servidorFalso,
  vinculo,
  workspace,
  yo,
  type Llamada,
  type Responder,
} from "../../pruebas/servidor";
import { AdminOrganizacion } from "./AdminOrganizacion";
import { AdminWorkspace } from "./AdminWorkspace";

// Administración: organización, workspace, roles y repositorios vinculados. Se simula la API
// de la consola con un `fetch` falso (nunca la red); el rol sale de `/yo`, como en la SPA.

const maria = asignacion("r1");
const equipo = asignacion("r2", {
  workspace: null,
  rol: "org-admin",
  sujeto: { tipo: "equipo", github_org: "acme-gh", equipo: "plataforma", equipo_id: 99 },
});

interface Estado {
  workspaces: Workspace[];
  roles: AsignacionRol[];
  repos: VinculoRepositorio[];
}

/** Servidor con estado en memoria: las escrituras que acepta se reflejan en las lecturas siguientes. */
function servidor(rolOrg: Rol | null, rolWs: Rol | undefined, extra: Record<string, Responder> = {}) {
  const estado: Estado = { workspaces: [workspace("cert")], roles: [maria, equipo], repos: [vinculo("api")] };
  const s = servidorFalso({
    "GET /yo": () => yo(rolOrg, rolWs),
    "GET /orgs": () => [organizacion()],
    "GET /orgs/acme/workspaces": () => estado.workspaces,
    "GET /orgs/acme/roles": (l: Llamada) => estado.roles.filter((r) => !l.consulta.get("workspace") || r.workspace === l.consulta.get("workspace")),
    "GET /orgs/acme/workspaces/cert/repositorios": () => estado.repos,
    ...extra,
  });
  return { ...s, estado };
}

const montarOrg = () => montarEnRuta(() => <AdminOrganizacion />, "/$org/administracion", "/acme/administracion");
const montarWs = () => montarEnRuta(() => <AdminWorkspace />, "/$org/$ws/administracion", "/acme/cert/administracion");

const boton = (nombre: string | RegExp) => screen.queryByRole("button", { name: nombre });

describe("administración de la organización", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("un org-admin crea un workspace y la lista lo refleja", async () => {
    const user = userEvent.setup();
    const s = servidor("org-admin", undefined, {
      "POST /orgs/acme/workspaces": (l) => {
        const nuevo = workspace(l.cuerpo.workspace, { nombre: l.cuerpo.nombre, perfil_por_defecto: l.cuerpo.perfil_por_defecto });
        s.estado.workspaces.push(nuevo);
        return json(201, nuevo);
      },
    });
    montarOrg();

    await user.click(await screen.findByRole("button", { name: "Nuevo workspace" }));
    const dialogo = await screen.findByRole("dialog", { name: "Nuevo workspace" });
    await user.type(within(dialogo).getByLabelText("Identificador"), "firma");
    await user.type(within(dialogo).getByLabelText("Nombre"), "Firma digital");
    await user.type(within(dialogo).getByLabelText("Zona de datos Azure (opcional)"), "westeurope");
    await user.selectOptions(within(dialogo).getByLabelText("Perfil por defecto"), "profundo");
    await user.click(within(dialogo).getByRole("button", { name: "Guardar" }));

    // Se cierra y la tabla recargada muestra el nuevo workspace.
    expect(await screen.findByRole("link", { name: "Firma digital" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    // El diálogo ya no existe: el «Guardado» lo da la tarjeta de workspaces, con la versión que devolvió el servidor.
    expect(screen.getByText(/^Guardado · versión 3\b/)).toBeInTheDocument();
    const [crear] = s.de("POST", "/orgs/acme/workspaces");
    expect(crear!.cuerpo).toEqual({ workspace: "firma", nombre: "Firma digital", zona_datos_azure: "westeurope", perfil_por_defecto: "profundo" });
    expect(crear!.cabeceras["X-Railspec-Consola"]).toBe("1");
    expect(s.noSimuladas).toEqual([]);
  });

  it("al editar un workspace envía la versión vista y recarga la lista", async () => {
    const user = userEvent.setup();
    const s = servidor("org-admin", undefined, {
      "PUT /orgs/acme/workspaces/cert": (l) => {
        s.estado.workspaces = [workspace("cert", { nombre: l.cuerpo.nombre, version: 4, zona_datos_azure: l.cuerpo.zona_datos_azure })];
        return s.estado.workspaces[0];
      },
    });
    montarOrg();

    const fila = (await screen.findByRole("link", { name: "Certificados" })).closest("tr")!;
    await user.click(within(fila).getByRole("button", { name: "Editar" }));
    const dialogo = await screen.findByRole("dialog", { name: "Editar Certificados" });
    // El identificador no se edita: el formulario de edición no lo ofrece.
    expect(within(dialogo).queryByLabelText("Identificador")).toBeNull();
    const nombre = within(dialogo).getByLabelText("Nombre");
    await user.clear(nombre);
    await user.type(nombre, "Certificados 2");
    await user.click(within(dialogo).getByRole("button", { name: "Guardar" }));

    expect(await screen.findByRole("link", { name: "Certificados 2" })).toBeInTheDocument();
    expect(screen.getByText(/^Guardado · versión 4\b/)).toBeInTheDocument();
    expect(s.de("PUT", "/orgs/acme/workspaces/cert")[0]!.cuerpo).toEqual({
      nombre: "Certificados 2",
      zona_datos_azure: null,
      perfil_por_defecto: "estandar",
      version: 3,
    });
  });

  it("un 409 al editar se explica como conflicto de versión y un 422 lista los campos inválidos", async () => {
    const user = userEvent.setup();
    let respuesta = () => json(409, { detalle: "versión desactualizada", version_actual: 4 });
    servidor("org-admin", undefined, { "PUT /orgs/acme/workspaces/cert": () => respuesta() });
    montarOrg();

    const fila = (await screen.findByRole("link", { name: "Certificados" })).closest("tr")!;
    await user.click(within(fila).getByRole("button", { name: "Editar" }));
    const dialogo = await screen.findByRole("dialog", { name: "Editar Certificados" });
    await user.click(within(dialogo).getByRole("button", { name: "Guardar" }));
    expect(await within(dialogo).findByText(/Otra persona modificó este registro/)).toHaveTextContent("(versión actual 4)");
    expect(within(dialogo).getByText(/versión desactualizada/)).toBeInTheDocument();

    respuesta = () => json(422, { detalle: "Los datos no son válidos", errores: [{ ruta: "zona_datos_azure", mensaje: "zona desconocida" }] });
    await user.click(within(dialogo).getByRole("button", { name: "Guardar" }));
    const alerta = await within(dialogo).findByRole("alert");
    expect(alerta).toHaveTextContent("Los datos no son válidos (zona_datos_azure: zona desconocida)");
  });

  it("un org-admin ve los datos de la organización y puede editarlos con su versión", async () => {
    const user = userEvent.setup();
    const s = servidor("org-admin", undefined, {
      "PUT /orgs/acme": (l) => organizacion({ ...l.cuerpo, id: "acme", version: 3 }),
    });
    montarOrg();

    expect(await screen.findByText("Acme Corp")).toBeInTheDocument();
    expect(screen.getByText("acme-gh")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Editar" })[0]!);
    const dialogo = await screen.findByRole("dialog", { name: "Editar Acme Corp" });
    const region = within(dialogo).getByLabelText("Región de datos");
    await user.clear(region);
    await user.type(region, "us");
    await user.click(within(dialogo).getByRole("button", { name: "Guardar" }));

    await waitFor(() => expect(s.de("PUT", "/orgs/acme")).toHaveLength(1));
    expect(s.de("PUT", "/orgs/acme")[0]!.cuerpo).toEqual({ nombre: "Acme Corp", github_org: "acme-gh", region_datos: "us", version: 2 });
    expect(await screen.findByText(/^Guardado · versión 3\b/)).toBeInTheDocument();
  });

  it("sin rol de org-admin oculta crear, asignar y quitar", async () => {
    servidor("desarrollador", undefined);
    montarOrg();

    // Espera a que carguen /yo, los workspaces y los roles.
    expect(await screen.findByRole("link", { name: "Certificados" })).toBeInTheDocument();
    expect(await screen.findByText("@maria")).toBeInTheDocument();
    expect(screen.getByText(/Solo un admin\. de organización puede cambiar estos datos/)).toBeInTheDocument();
    expect(boton("Nuevo workspace")).toBeNull();
    expect(boton("Asignar rol")).toBeNull();
    expect(boton("Quitar")).toBeNull();
    // Ni editar un workspace desde la lista: el servidor lo reserva al org-admin (el workspace-admin lo hace en su página).
    expect(boton("Editar")).toBeNull();
    // Tampoco se ofrece editar los datos de la organización.
    const tarjeta = screen.getByRole("heading", { name: "Organización" }).closest("div")!.parentElement!;
    expect(within(tarjeta).queryByRole("button", { name: "Editar" })).toBeNull();
  });
});

describe("roles", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("asigna un rol de organización a un usuario (sin @, sin workspace) y recarga la lista", async () => {
    const user = userEvent.setup();
    const s = servidor("org-admin", undefined, {
      "POST /orgs/acme/roles": (l) => {
        s.estado.roles.push(asignacion("r3", { workspace: null, rol: l.cuerpo.rol, sujeto: { tipo: "usuario", github_id: 8, login: l.cuerpo.sujeto.login } }));
        return json(201, s.estado.roles.at(-1));
      },
    });
    montarOrg();

    await user.click(await screen.findByRole("button", { name: "Asignar rol" }));
    const dialogo = await screen.findByRole("dialog", { name: "Asignar rol" });
    // Por defecto: usuario con rol org-admin, que no pide workspace.
    expect(within(dialogo).getByLabelText("Rol")).toHaveValue("org-admin");
    expect(within(dialogo).queryByLabelText("Workspace")).toBeNull();
    await user.type(within(dialogo).getByLabelText("Login de GitHub"), "@carlos");
    await user.click(within(dialogo).getByRole("button", { name: "Asignar" }));

    expect(await screen.findByText("@carlos")).toBeInTheDocument();
    expect(s.de("POST", "/orgs/acme/roles")[0]!.cuerpo).toEqual({ workspace: null, rol: "org-admin", sujeto: { tipo: "usuario", login: "carlos" } });
    expect(screen.queryByRole("dialog")).toBeNull();
    // La versión de una asignación no se anuncia: solo que quedó asignada.
    expect(screen.getByText(/^Rol asignado · \d/)).not.toHaveTextContent("versión");
  });

  it("un rol por debajo de org-admin exige elegir workspace; un equipo envía su id numérico", async () => {
    const user = userEvent.setup();
    const s = servidor("org-admin", undefined, { "POST /orgs/acme/roles": () => json(201, asignacion("r9")) });
    montarOrg();

    await user.click(await screen.findByRole("button", { name: "Asignar rol" }));
    const dialogo = await screen.findByRole("dialog", { name: "Asignar rol" });
    await user.click(within(dialogo).getByRole("radio", { name: "Equipo de GitHub" }));
    await user.type(within(dialogo).getByLabelText("Organización GitHub"), "acme-gh");
    await user.type(within(dialogo).getByLabelText("Equipo (slug)"), "firmas");
    await user.type(within(dialogo).getByLabelText("Id del equipo"), "123");
    await user.selectOptions(within(dialogo).getByLabelText("Rol"), "workspace-admin");

    // Sin workspace elegido, no se envía nada.
    await user.click(within(dialogo).getByRole("button", { name: "Asignar" }));
    expect(s.de("POST", "/orgs/acme/roles")).toHaveLength(0);

    await user.selectOptions(await within(dialogo).findByLabelText("Workspace"), "cert");
    await user.click(within(dialogo).getByRole("button", { name: "Asignar" }));
    await waitFor(() => expect(s.de("POST", "/orgs/acme/roles")).toHaveLength(1));
    expect(s.de("POST", "/orgs/acme/roles")[0]!.cuerpo).toEqual({
      workspace: "cert",
      rol: "workspace-admin",
      sujeto: { tipo: "equipo", github_org: "acme-gh", equipo: "firmas", equipo_id: 123 },
    });
  });

  it("muestra los errores del servidor al asignar (422 con campos y 409 de duplicado)", async () => {
    const user = userEvent.setup();
    let respuesta = () => json(422, { detalle: "No existe el usuario de GitHub «nadie».", errores: [{ ruta: "sujeto.login", mensaje: "desconocido" }] });
    servidor("org-admin", undefined, { "POST /orgs/acme/roles": () => respuesta() });
    montarOrg();

    await user.click(await screen.findByRole("button", { name: "Asignar rol" }));
    const dialogo = await screen.findByRole("dialog", { name: "Asignar rol" });
    await user.type(within(dialogo).getByLabelText("Login de GitHub"), "nadie");
    await user.click(within(dialogo).getByRole("button", { name: "Asignar" }));
    expect(await within(dialogo).findByRole("alert")).toHaveTextContent("No existe el usuario de GitHub «nadie». (sujeto.login: desconocido)");

    respuesta = () => json(409, { detalle: "La asignación ya existe.", codigo: "conflicto-version" });
    await user.click(within(dialogo).getByRole("button", { name: "Asignar" }));
    await waitFor(() => expect(within(dialogo).getByRole("alert")).toHaveTextContent("La asignación ya existe."));
    expect(within(dialogo).getByRole("alert")).toHaveTextContent("Conflicto");
    // El diálogo sigue abierto para corregir.
    expect(screen.getByRole("dialog", { name: "Asignar rol" })).toBeInTheDocument();
  });

  it("quitar pide confirmación, cancelar no borra y confirmar llama al DELETE y recarga", async () => {
    const user = userEvent.setup();
    const s = servidor("org-admin", undefined, {
      "DELETE /orgs/acme/roles/r1": () => {
        s.estado.roles = s.estado.roles.filter((r) => r.id !== "r1");
        return null;
      },
    });
    montarOrg();

    const fila = (await screen.findByText("@maria")).closest("tr")!;
    await user.click(within(fila).getByRole("button", { name: "Quitar" }));
    const dialogo = await screen.findByRole("dialog", { name: "Quitar rol" });
    expect(within(dialogo).getByText("¿Quitar Desarrollador a @maria?")).toBeInTheDocument();
    await user.click(within(dialogo).getByRole("button", { name: "Cancelar" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(s.de("DELETE", "/orgs/acme/roles/r1")).toHaveLength(0);

    await user.click(within(fila).getByRole("button", { name: "Quitar" }));
    await user.click(within(await screen.findByRole("dialog", { name: "Quitar rol" })).getByRole("button", { name: "Quitar" }));
    await waitFor(() => expect(screen.queryByText("@maria")).toBeNull());
    expect(s.de("DELETE", "/orgs/acme/roles/r1")[0]!.cabeceras["X-Railspec-Consola"]).toBe("1");
    // El equipo sigue y se describe con su organización y slug.
    expect(screen.getByText("equipo acme-gh/plataforma")).toBeInTheDocument();
  });

  it("si el servidor rechaza quitar (403) lo muestra y no cierra el diálogo", async () => {
    const user = userEvent.setup();
    servidor("org-admin", undefined, {
      "DELETE /orgs/acme/roles/r2": () => json(403, { detalle: "No puedes quitar al último org-admin." }),
    });
    montarOrg();

    const fila = (await screen.findByText("equipo acme-gh/plataforma")).closest("tr")!;
    await user.click(within(fila).getByRole("button", { name: "Quitar" }));
    const dialogo = await screen.findByRole("dialog", { name: "Quitar rol" });
    await user.click(within(dialogo).getByRole("button", { name: "Quitar" }));
    const alerta = await within(dialogo).findByRole("alert");
    expect(alerta).toHaveTextContent("Sin permiso");
    expect(alerta).toHaveTextContent("No puedes quitar al último org-admin.");
    expect(screen.getByText("equipo acme-gh/plataforma")).toBeInTheDocument();
  });

  it("en un workspace lista solo sus asignaciones, no ofrece org-admin y fija el workspace", async () => {
    const user = userEvent.setup();
    const s = servidor("org-admin", "org-admin", { "POST /orgs/acme/roles": () => json(201, asignacion("r5")) });
    montarWs();

    expect(await screen.findByText("@maria")).toBeInTheDocument();
    // La del equipo es de toda la organización: no sale en el workspace.
    expect(screen.queryByText("equipo acme-gh/plataforma")).toBeNull();
    expect(s.de("GET", "/orgs/acme/roles")[0]!.consulta.get("workspace")).toBe("cert");

    await user.click(screen.getByRole("button", { name: "Asignar rol" }));
    const dialogo = await screen.findByRole("dialog", { name: "Asignar rol" });
    const opciones = within(within(dialogo).getByLabelText("Rol")).getAllByRole("option").map((o) => (o as HTMLOptionElement).value);
    expect(opciones).toEqual(["lector", "desarrollador", "workspace-admin"]);
    expect(within(dialogo).queryByLabelText("Workspace")).toBeNull();
    await user.type(within(dialogo).getByLabelText("Login de GitHub"), "lucia");
    await user.click(within(dialogo).getByRole("button", { name: "Asignar" }));
    await waitFor(() => expect(s.de("POST", "/orgs/acme/roles")).toHaveLength(1));
    expect(s.de("POST", "/orgs/acme/roles")[0]!.cuerpo).toEqual({ workspace: "cert", rol: "desarrollador", sujeto: { tipo: "usuario", login: "lucia" } });
  });
});

describe("administración del workspace", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("un workspace-admin edita el workspace con la versión vista", async () => {
    const user = userEvent.setup();
    const s = servidor("desarrollador", "workspace-admin", {
      "PUT /orgs/acme/workspaces/cert": () => workspace("cert", { version: 4, perfil_por_defecto: "ligero" }),
    });
    montarWs();

    const tarjeta = (await screen.findByRole("heading", { name: "Workspace" })).parentElement!;
    expect(await screen.findByText("Certificados")).toBeInTheDocument();
    await user.click(await within(tarjeta).findByRole("button", { name: "Editar" }));
    const dialogo = await screen.findByRole("dialog", { name: "Editar Certificados" });
    await user.selectOptions(within(dialogo).getByLabelText("Perfil por defecto"), "ligero");
    await user.click(within(dialogo).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(s.de("PUT", "/orgs/acme/workspaces/cert")).toHaveLength(1));
    expect(s.de("PUT", "/orgs/acme/workspaces/cert")[0]!.cuerpo).toMatchObject({ perfil_por_defecto: "ligero", version: 3 });
    // El diálogo se cerró; la tarjeta del workspace dice «Guardado» con la versión nueva.
    expect(await screen.findByText(/^Guardado · versión 4\b/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("tras un 409 recarga la versión vigente y el siguiente guardado la envía", async () => {
    const user = userEvent.setup();
    let vigente = workspace("cert", { version: 3 });
    const s = servidor("desarrollador", "workspace-admin", {
      "GET /orgs/acme/workspaces": () => [vigente],
      "PUT /orgs/acme/workspaces/cert": (l) => {
        if (l.cuerpo.version !== vigente.version) return json(409, { detalle: "versión desactualizada", version_actual: vigente.version });
        vigente = { ...vigente, nombre: l.cuerpo.nombre, version: vigente.version + 1 };
        return vigente;
      },
    });
    montarWs();

    const tarjeta = (await screen.findByRole("heading", { name: "Workspace" })).parentElement!;
    await user.click(await within(tarjeta).findByRole("button", { name: "Editar" }));
    await screen.findByRole("dialog", { name: "Editar Certificados" });
    // Otra persona edita el workspace mientras el diálogo está abierto.
    vigente = workspace("cert", { nombre: "Certificados (otra persona)", version: 4 });
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Guardar" }));

    // La vista recarga y el formulario muestra lo vigente, con su versión.
    await waitFor(() => expect(within(screen.getByRole("dialog")).getByLabelText("Nombre")).toHaveValue("Certificados (otra persona)"));
    expect(s.de("PUT", "/orgs/acme/workspaces/cert")[0]!.cuerpo.version).toBe(3);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(s.de("PUT", "/orgs/acme/workspaces/cert")[1]!.cuerpo.version).toBe(4);
  });

  it.each<[Rol, Rol]>([
    ["desarrollador", "desarrollador"],
    ["desarrollador", "lector"],
  ])("con rol %s en la org y %s en el workspace no ofrece ninguna acción de escritura", async (rolOrg, rolWs) => {
    servidor(rolOrg, rolWs);
    montarWs();

    expect(await screen.findByText("@maria")).toBeInTheDocument();
    expect(await screen.findByText("https://github.com/acme/api")).toBeInTheDocument();
    for (const nombre of ["Editar", "Vincular repositorio", "Desvincular", "Asignar rol", "Quitar"]) {
      expect(boton(nombre), nombre).toBeNull();
    }
  });
});

describe("repositorios vinculados", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("vincula un repositorio con su URL de GitHub y valores por defecto", async () => {
    const user = userEvent.setup();
    const s = servidor("org-admin", "org-admin", {
      "PUT /orgs/acme/workspaces/cert/repositorios/firmas": (l) => {
        s.estado.repos.push(vinculo("firmas", { url: l.cuerpo.url }));
        return json(201, s.estado.repos.at(-1));
      },
    });
    montarWs();

    await user.click(await screen.findByRole("button", { name: "Vincular repositorio" }));
    const dialogo = await screen.findByRole("dialog", { name: "Vincular repositorio" });
    expect(within(dialogo).getByLabelText("URL")).toHaveValue("https://github.com/");
    await user.type(within(dialogo).getByLabelText("Identificador"), "firmas");
    const url = within(dialogo).getByLabelText("URL");
    await user.clear(url);
    await user.type(url, "https://github.com/acme/firmas");
    await user.type(within(dialogo).getByLabelText("Exclusiones (glob, una por línea)"), "secrets/**{enter}  vendor/**  ");
    await user.click(within(dialogo).getByRole("button", { name: "Guardar" }));

    expect(await screen.findByText("https://github.com/acme/firmas")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText(/^Guardado · versión \d+/)).toBeInTheDocument();
    const [guardar] = s.de("PUT", "/orgs/acme/workspaces/cert/repositorios/firmas");
    // Alta: sin `version` ni `motivo`, y sin política de chat propia salvo que se personalice.
    expect(guardar!.cuerpo).toEqual({
      url: "https://github.com/acme/firmas",
      rol: "primario",
      rama_por_defecto: "main",
      nivel_codigo: "restringido",
      retencion_snapshots_dias: 30,
      exclusiones: ["secrets/**", "vendor/**"],
    });
  });

  it("no envía una URL que no sea https (validación del formulario) y muestra el 422 del servidor", async () => {
    const user = userEvent.setup();
    const s = servidor("org-admin", "org-admin", {
      "PUT /orgs/acme/workspaces/cert/repositorios/firmas": () =>
        json(422, {
          detalle: "Los datos enviados no son válidos.",
          errores: [{ ruta: "url", mensaje: "debe ser https://github.com/<owner>/<repo>" }],
        }),
    });
    montarWs();

    await user.click(await screen.findByRole("button", { name: "Vincular repositorio" }));
    const dialogo = await screen.findByRole("dialog", { name: "Vincular repositorio" });
    await user.type(within(dialogo).getByLabelText("Identificador"), "firmas");
    const url = within(dialogo).getByLabelText("URL");
    await user.clear(url);
    await user.type(url, "http://github.com/acme/firmas");
    await user.click(within(dialogo).getByRole("button", { name: "Guardar" }));
    expect(s.de("PUT", "/orgs/acme/workspaces/cert/repositorios/firmas")).toHaveLength(0);

    // https pero sin owner/repo: lo valida el servidor y la consola muestra su motivo.
    await user.clear(url);
    await user.type(url, "https://github.com/");
    await user.click(within(dialogo).getByRole("button", { name: "Guardar" }));
    expect(await within(dialogo).findByRole("alert")).toHaveTextContent("url: debe ser https://github.com/<owner>/<repo>");
    expect(s.de("PUT", "/orgs/acme/workspaces/cert/repositorios/firmas")).toHaveLength(1);
  });

  it("personalizar la política de chat la incluye en el cuerpo", async () => {
    const user = userEvent.setup();
    const s = servidor("org-admin", "org-admin", {
      "PUT /orgs/acme/workspaces/cert/repositorios/firmas": () => json(201, vinculo("firmas")),
    });
    montarWs();

    await user.click(await screen.findByRole("button", { name: "Vincular repositorio" }));
    const dialogo = await screen.findByRole("dialog", { name: "Vincular repositorio" });
    await user.type(within(dialogo).getByLabelText("Identificador"), "firmas");
    const url = within(dialogo).getByLabelText("URL");
    await user.clear(url);
    await user.type(url, "https://github.com/acme/firmas");
    await user.click(within(dialogo).getByRole("checkbox", { name: /Personalizar la política de chat/ }));
    await user.selectOptions(within(dialogo).getByLabelText("Hosting"), "cualquiera");
    // Se teclea con comas: el texto no se normaliza mientras se escribe.
    await user.type(within(dialogo).getByLabelText("Modelos permitidos"), "claude-opus-5-5, claude-sonnet-5-5");
    await user.click(within(dialogo).getByRole("button", { name: "Guardar" }));

    await waitFor(() => expect(s.de("PUT", "/orgs/acme/workspaces/cert/repositorios/firmas")).toHaveLength(1));
    expect(s.de("PUT", "/orgs/acme/workspaces/cert/repositorios/firmas")[0]!.cuerpo.chat_contexto_codigo).toEqual({
      hosting: "cualquiera",
      huella_tokens_n: 8,
      presupuesto_fuga_conversacion: 2000,
      presupuesto_fuga_usuario_dia: 20000,
      fragmentos_en_respuesta: false,
      modelos_permitidos: ["claude-opus-5-5", "claude-sonnet-5-5"],
      permitido: true,
    });
  });

  it("cambiar el nivel de código exige un motivo y lo envía junto con la versión", async () => {
    const user = userEvent.setup();
    const s = servidor("org-admin", "org-admin", {
      "PUT /orgs/acme/workspaces/cert/repositorios/api": (l) => {
        s.estado.repos = [vinculo("api", { nivel_codigo: l.cuerpo.nivel_codigo, version: 5 })];
        return s.estado.repos[0];
      },
    });
    montarWs();

    const fila = (await screen.findByText("https://github.com/acme/api")).closest("tr")!;
    await user.click(within(fila).getByRole("button", { name: "Editar" }));
    const dialogo = await screen.findByRole("dialog", { name: "Editar api" });
    expect(within(dialogo).getByLabelText("Identificador")).toBeDisabled();
    // Sin cambiar el nivel no se pide motivo.
    expect(within(dialogo).queryByLabelText("Motivo del cambio de nivel")).toBeNull();

    await user.selectOptions(within(dialogo).getByLabelText("Nivel de política de código"), "abierto");
    const guardar = within(dialogo).getByRole("button", { name: "Guardar" });
    expect(guardar).toBeDisabled();
    const motivo = within(dialogo).getByLabelText("Motivo del cambio de nivel");
    await user.type(motivo, "   ");
    expect(guardar).toBeDisabled(); // un motivo en blanco no vale
    await user.type(motivo, "Repo público desde el 1 de octubre");
    expect(guardar).toBeEnabled();
    await user.click(guardar);

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(s.de("PUT", "/orgs/acme/workspaces/cert/repositorios/api")[0]!.cuerpo).toEqual({
      url: "https://github.com/acme/api",
      rol: "primario",
      rama_por_defecto: "main",
      nivel_codigo: "abierto",
      retencion_snapshots_dias: 30,
      exclusiones: [],
      version: 4,
      motivo: "Repo público desde el 1 de octubre",
    });
    // La tabla recargada muestra el nuevo nivel.
    expect(await screen.findByText("abierto")).toBeInTheDocument();
  });

  it("editar sin cambiar el nivel no envía motivo; un 409 de versión se explica y no cierra el diálogo", async () => {
    const user = userEvent.setup();
    const s = servidor("org-admin", "org-admin", {
      "PUT /orgs/acme/workspaces/cert/repositorios/api": () => json(409, { detalle: "versión 4 desactualizada", version_actual: 6 }),
    });
    montarWs();

    const fila = (await screen.findByText("https://github.com/acme/api")).closest("tr")!;
    await user.click(within(fila).getByRole("button", { name: "Editar" }));
    const dialogo = await screen.findByRole("dialog", { name: "Editar api" });
    const rama = within(dialogo).getByLabelText("Rama por defecto");
    await user.clear(rama);
    await user.type(rama, "develop");
    await user.click(within(dialogo).getByRole("button", { name: "Guardar" }));

    expect(await within(dialogo).findByText(/Otra persona modificó este registro/)).toHaveTextContent("versión actual 6");
    const cuerpo = s.de("PUT", "/orgs/acme/workspaces/cert/repositorios/api")[0]!.cuerpo;
    expect(cuerpo).toMatchObject({ rama_por_defecto: "develop", version: 4 });
    expect(cuerpo).not.toHaveProperty("motivo");
  });

  it("desvincular exige escribir el nombre y un motivo, y envía el motivo en la query del DELETE", async () => {
    const user = userEvent.setup();
    const s = servidor("org-admin", "org-admin", {
      "DELETE /orgs/acme/workspaces/cert/repositorios/api": () => {
        s.estado.repos = [];
        return null;
      },
    });
    montarWs();

    const fila = (await screen.findByText("https://github.com/acme/api")).closest("tr")!;
    await user.click(within(fila).getByRole("button", { name: "Desvincular" }));
    const dialogo = await screen.findByRole("dialog", { name: "Desvincular api" });
    const confirmar = within(dialogo).getByRole("button", { name: "Desvincular" });
    expect(confirmar).toBeDisabled();

    await user.type(within(dialogo).getByLabelText("Motivo"), "Repositorio archivado");
    expect(confirmar).toBeDisabled(); // falta escribir el nombre
    await user.type(within(dialogo).getByLabelText("Escribe «api» para confirmar"), "ap");
    expect(confirmar).toBeDisabled(); // nombre incompleto
    await user.type(within(dialogo).getByLabelText("Escribe «api» para confirmar"), "i");
    expect(confirmar).toBeEnabled();
    await user.click(confirmar);

    expect(await screen.findByText("Sin repositorios vinculados")).toBeInTheDocument();
    const [borrar] = s.de("DELETE", "/orgs/acme/workspaces/cert/repositorios/api");
    expect(borrar!.consulta.get("motivo")).toBe("Repositorio archivado");
    expect(borrar!.cabeceras["X-Railspec-Consola"]).toBe("1");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("desvincular con un motivo en blanco no se puede confirmar", async () => {
    const user = userEvent.setup();
    const s = servidor("org-admin", "org-admin");
    montarWs();

    const fila = (await screen.findByText("https://github.com/acme/api")).closest("tr")!;
    await user.click(within(fila).getByRole("button", { name: "Desvincular" }));
    const dialogo = await screen.findByRole("dialog", { name: "Desvincular api" });
    await user.type(within(dialogo).getByLabelText("Escribe «api» para confirmar"), "api");
    await user.type(within(dialogo).getByLabelText("Motivo"), "   ");
    expect(within(dialogo).getByRole("button", { name: "Desvincular" })).toBeDisabled();
    expect(s.de("DELETE", "/orgs/acme/workspaces/cert/repositorios/api")).toHaveLength(0);
  });

  it("muestra el error del servidor al desvincular (409 de otro tipo) y conserva el repositorio", async () => {
    const user = userEvent.setup();
    servidor("org-admin", "org-admin", {
      "DELETE /orgs/acme/workspaces/cert/repositorios/api": () =>
        json(409, { detalle: "El repositorio tiene unidades en curso.", codigo: "conflicto-version" }),
    });
    montarWs();

    const fila = (await screen.findByText("https://github.com/acme/api")).closest("tr")!;
    await user.click(within(fila).getByRole("button", { name: "Desvincular" }));
    const dialogo = await screen.findByRole("dialog", { name: "Desvincular api" });
    await user.type(within(dialogo).getByLabelText("Escribe «api» para confirmar"), "api");
    await user.type(within(dialogo).getByLabelText("Motivo"), "Limpieza");
    await user.click(within(dialogo).getByRole("button", { name: "Desvincular" }));
    expect(await within(dialogo).findByRole("alert")).toHaveTextContent("El repositorio tiene unidades en curso.");
    expect(screen.getByText("https://github.com/acme/api")).toBeInTheDocument();
  });
});
