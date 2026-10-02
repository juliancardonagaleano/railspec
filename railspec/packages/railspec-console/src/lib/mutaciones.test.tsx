import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { ErrorApi } from "../api/cliente";
import { clienteDePrueba } from "../pruebas/servidor";
import { AvisoGuardado, ErrorGuardado, useGuardado, useGuardar, type Guardado } from "./mutaciones";

// `useGuardar` deja constancia del guardado bajo una clave de la caché de consultas: los formularios
// se remontan al subir la versión y los diálogos se cierran al guardar, y aun así el aviso se ve.

function envoltura() {
  const cliente = clienteDePrueba();
  const Envoltura = ({ children }: { children: ReactNode }) => <QueryClientProvider client={cliente}>{children}</QueryClientProvider>;
  return { cliente, Envoltura };
}

const guardado = (g: Partial<Guardado> = {}): Guardado => ({ version: 4, avisos: [], en: Date.parse("2026-10-02T14:03:00"), ...g });

describe("useGuardar: constancia del guardado", () => {
  it("expone la versión que devolvió el servidor y sigue ahí tras desmontar el formulario", async () => {
    const { Envoltura } = envoltura();
    const alExito = vi.fn();
    const formulario = renderHook(
      () => useGuardar(async (_: void) => ({ version: 7, nombre: "x" }), [], alExito, { clave: "cosa:acme" }),
      { wrapper: Envoltura },
    );
    expect(formulario.result.current.guardado).toBeNull();

    await act(async () => formulario.result.current.mutate(undefined));
    await waitFor(() => expect(formulario.result.current.guardado?.version).toBe(7));
    expect(formulario.result.current.guardado?.avisos).toEqual([]);
    expect(alExito).toHaveBeenCalledWith({ version: 7, nombre: "x" });

    // El formulario desaparece (diálogo cerrado, remontaje por versión): otro lector con la misma clave lo ve.
    formulario.unmount();
    const lector = renderHook(() => useGuardado("cosa:acme"), { wrapper: Envoltura });
    expect(lector.result.current?.version).toBe(7);
  });

  it("no lo comparte entre claves", async () => {
    const { Envoltura } = envoltura();
    const guardar = renderHook(() => useGuardar(async (_: void) => ({ version: 2 }), [], undefined, { clave: "a" }), { wrapper: Envoltura });
    const otro = renderHook(() => useGuardado("b"), { wrapper: Envoltura });
    await act(async () => guardar.result.current.mutate(undefined));
    await waitFor(() => expect(guardar.result.current.guardado).not.toBeNull());
    expect(otro.result.current).toBeNull();
  });

  it("calcula la versión y los avisos con las funciones dadas, y `version: null` la omite", async () => {
    const { Envoltura } = envoltura();
    const compuesto = renderHook(
      () =>
        useGuardar(async (_: void) => ({ perfil: { version: 9 }, avisos: ["uno", "dos"] }), [], undefined, {
          clave: "perfil",
          version: (r) => r.perfil.version,
          avisos: (r) => r.avisos,
        }),
      { wrapper: Envoltura },
    );
    await act(async () => compuesto.result.current.mutate(undefined));
    await waitFor(() => expect(compuesto.result.current.guardado).toMatchObject({ version: 9, avisos: ["uno", "dos"] }));

    const sinVersion = renderHook(() => useGuardar(async (_: void) => ({ version: 1 }), [], undefined, { clave: "rol", version: null }), {
      wrapper: Envoltura,
    });
    await act(async () => sinVersion.result.current.mutate(undefined));
    await waitFor(() => expect(sinVersion.result.current.guardado).not.toBeNull());
    expect(sinVersion.result.current.guardado?.version).toBeNull();
  });

  it("un resultado sin versión numérica (borrado, 204) no inventa una", async () => {
    const { Envoltura } = envoltura();
    const borrar = renderHook(() => useGuardar(async (_: void) => null, [], undefined, { clave: "x" }), { wrapper: Envoltura });
    await act(async () => borrar.result.current.mutate(undefined));
    await waitFor(() => expect(borrar.result.current.guardado).not.toBeNull());
    expect(borrar.result.current.guardado?.version).toBeNull();
  });

  it("empezar otro guardado lo borra y un error no deja el anterior", async () => {
    const { Envoltura } = envoltura();
    let falla = false;
    const guardar = renderHook(
      () =>
        useGuardar(
          async (_: void) => {
            if (falla) throw new ErrorApi(422, { detalle: "no" });
            return { version: 3 };
          },
          [],
          undefined,
          { clave: "k" },
        ),
      { wrapper: Envoltura },
    );
    await act(async () => guardar.result.current.mutate(undefined));
    await waitFor(() => expect(guardar.result.current.guardado?.version).toBe(3));

    falla = true;
    await act(async () => guardar.result.current.mutate(undefined));
    await waitFor(() => expect(guardar.result.current.isError).toBe(true));
    expect(guardar.result.current.guardado).toBeNull();
  });

  it("sin `aviso` no deja nada y `useGuardado(undefined)` es nulo", async () => {
    const { cliente, Envoltura } = envoltura();
    const guardar = renderHook(() => useGuardar(async (_: void) => ({ version: 5 }), []), { wrapper: Envoltura });
    await act(async () => guardar.result.current.mutate(undefined));
    await waitFor(() => expect(guardar.result.current.isSuccess).toBe(true));
    expect(guardar.result.current.guardado).toBeNull();
    expect(cliente.getQueryCache().findAll({ queryKey: ["guardado"] })).toHaveLength(1); // solo el lector de `undefined`
    expect(renderHook(() => useGuardado(undefined), { wrapper: Envoltura }).result.current).toBeNull();
  });

  it("invalida las claves dadas al terminar", async () => {
    const { cliente, Envoltura } = envoltura();
    const invalidar = vi.spyOn(cliente, "invalidateQueries");
    const guardar = renderHook(() => useGuardar(async (_: void) => ({ version: 1 }), [["a", "b"], ["c"]], undefined, { clave: "z" }), {
      wrapper: Envoltura,
    });
    await act(async () => guardar.result.current.mutate(undefined));
    await waitFor(() => expect(guardar.result.current.isSuccess).toBe(true));
    expect(invalidar).toHaveBeenCalledWith({ queryKey: ["a", "b"] });
    expect(invalidar).toHaveBeenCalledWith({ queryKey: ["c"] });
  });
});

describe("AvisoGuardado", () => {
  it("no pinta nada sin guardado", () => {
    const { container } = render(<AvisoGuardado guardado={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("dice «Guardado · versión N» con la hora", () => {
    render(<AvisoGuardado guardado={guardado({ version: 4 })} />);
    const aviso = screen.getByRole("status");
    expect(aviso).toHaveTextContent(/^Guardado · versión 4 · \d{1,2}:\d{2}/);
  });

  it("sin versión solo dice el texto dado y la hora", () => {
    render(<AvisoGuardado guardado={guardado({ version: null })} texto="Rol asignado" />);
    expect(screen.getByRole("status")).toHaveTextContent(/^Rol asignado · \d{1,2}:\d{2}/);
    expect(screen.getByRole("status")).not.toHaveTextContent("versión");
  });

  it("con avisos del servidor los lista uno por línea", () => {
    render(<AvisoGuardado guardado={guardado({ avisos: ["primero", "segundo"] })} />);
    expect(screen.getByText("Guardado con avisos:")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["primero", "segundo"]);
    expect(screen.getByRole("status")).toHaveTextContent("versión 4");
  });
});

// Un 409 sube la versión: la vista recarga y el formulario se remonta (`key={version}`). El aviso de
// conflicto lo deja `useGuardar` en la caché, como el «Guardado», para que lo vea el formulario nuevo.

type Resultado = { version: number };

function Formulario({ clave, fn, conClave = true }: { clave: string; fn: () => Promise<Resultado>; conClave?: boolean }) {
  const guardar = useGuardar(fn, [], undefined, conClave ? { clave } : undefined);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        guardar.mutate(undefined);
      }}
    >
      <ErrorGuardado error={guardar.error} />
      <AvisoGuardado guardado={guardar.guardado} />
      <button type="submit">Guardar</button>
    </form>
  );
}

/** La pantalla dueña del formulario: lo remonta al cambiar `version`, como las vistas de la consola. */
function Pantalla({ version, ...props }: { version: number; clave: string; fn: () => Promise<Resultado>; conClave?: boolean }) {
  return <Formulario key={version} {...props} />;
}

const conflicto = (versionActual = 5) => new ErrorApi(409, { detalle: "versión desactualizada", version_actual: versionActual });
const alerta = () => screen.queryByText(/Otra persona modificó este registro/);
/** Deja correr los temporizadores a 0 ms: ahí se descarta el aviso de un formulario que no volvió. */
const turno = () => act(async () => void (await new Promise((r) => setTimeout(r, 0))));

describe("useGuardar: aviso de conflicto 409", () => {
  it("sobrevive al remontaje del formulario y conserva la versión vigente y el detalle", async () => {
    const user = userEvent.setup();
    const { Envoltura } = envoltura();
    const fn = vi.fn(async () => {
      throw conflicto(5);
    });
    const { rerender } = render(<Pantalla version={3} clave="cosa" fn={fn} />, { wrapper: Envoltura });

    await user.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByText(/Otra persona modificó este registro/)).toHaveTextContent("(versión actual 5)");

    // La recarga trae la versión 5: el formulario se remonta con el estado vacío.
    rerender(<Pantalla version={5} clave="cosa" fn={fn} />);
    await turno();
    const aviso = alerta();
    expect(aviso).toBeInTheDocument();
    expect(aviso).toHaveTextContent("(versión actual 5)");
    expect(aviso).toHaveTextContent("versión desactualizada");
  });

  it("empezar otro guardado lo borra, ya sea que salga bien o mal", async () => {
    const user = userEvent.setup();
    const { Envoltura } = envoltura();
    let siguiente: () => Promise<Resultado> = async () => ({ version: 6 });
    const fn = vi.fn(async () => {
      if (fn.mock.calls.length === 1) throw conflicto(5);
      return siguiente();
    });
    const { rerender } = render(<Pantalla version={3} clave="cosa" fn={fn} />, { wrapper: Envoltura });
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    await screen.findByText(/Otra persona modificó este registro/);
    rerender(<Pantalla version={5} clave="cosa" fn={fn} />);

    await user.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByText(/^Guardado · versión 6\b/)).toBeInTheDocument();
    expect(alerta()).toBeNull();

    // Un fallo que no es de versión reemplaza al aviso: el conflicto no se queda debajo.
    siguiente = async () => {
      throw new ErrorApi(422, { detalle: "datos inválidos" });
    };
    rerender(<Pantalla version={6} clave="cosa" fn={fn} />);
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("datos inválidos");
    expect(alerta()).toBeNull();
  });

  it("se descarta cuando el formulario se va de verdad (diálogo cerrado, otra pantalla)", async () => {
    const user = userEvent.setup();
    const { cliente, Envoltura } = envoltura();
    const fn = async (): Promise<Resultado> => {
      throw conflicto();
    };
    const primera = render(<Pantalla version={3} clave="cosa" fn={fn} />, { wrapper: Envoltura });
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    await screen.findByText(/Otra persona modificó este registro/);
    expect(cliente.getQueryCache().find({ queryKey: ["conflicto", "cosa"] })).toBeDefined();

    primera.unmount();
    await turno();
    expect(cliente.getQueryCache().find({ queryKey: ["conflicto", "cosa"] })).toBeUndefined();

    // Al volver a abrirlo no hay aviso viejo.
    render(<Pantalla version={5} clave="cosa" fn={fn} />, { wrapper: Envoltura });
    expect(alerta()).toBeNull();
  });

  it("no se comparte entre claves", async () => {
    const user = userEvent.setup();
    const { Envoltura } = envoltura();
    const fn = async (): Promise<Resultado> => {
      throw conflicto();
    };
    render(
      <>
        <Pantalla version={1} clave="a" fn={fn} />
        <Pantalla version={1} clave="b" fn={async () => ({ version: 2 })} />
      </>,
      { wrapper: Envoltura },
    );
    await user.click(screen.getAllByRole("button", { name: "Guardar" })[0]!);
    await screen.findByText(/Otra persona modificó este registro/);
    expect(screen.getAllByText(/Otra persona modificó este registro/)).toHaveLength(1);
  });

  it("solo guarda los 409: otros errores no sobreviven al remontaje", async () => {
    const user = userEvent.setup();
    const { Envoltura } = envoltura();
    const fn = async (): Promise<Resultado> => {
      throw new ErrorApi(422, { detalle: "datos inválidos" });
    };
    const { rerender } = render(<Pantalla version={1} clave="cosa" fn={fn} />, { wrapper: Envoltura });
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("datos inválidos");
    rerender(<Pantalla version={2} clave="cosa" fn={fn} />);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("sin `aviso` el 409 queda en la mutación y no en la caché", async () => {
    const user = userEvent.setup();
    const { cliente, Envoltura } = envoltura();
    const fn = async (): Promise<Resultado> => {
      throw conflicto();
    };
    const { rerender } = render(<Pantalla version={1} clave="cosa" conClave={false} fn={fn} />, { wrapper: Envoltura });
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    await screen.findByText(/Otra persona modificó este registro/);
    expect(cliente.getQueryCache().find({ queryKey: ["conflicto", "cosa"] })).toBeUndefined();
    rerender(<Pantalla version={2} clave="cosa" conClave={false} fn={fn} />);
    expect(alerta()).toBeNull();
  });

  it("un 409 sigue recargando las claves dadas", async () => {
    const user = userEvent.setup();
    const { cliente, Envoltura } = envoltura();
    const invalidar = vi.spyOn(cliente, "invalidateQueries");
    const Con409 = () => {
      const guardar = useGuardar(
        async (_: void) => {
          throw conflicto();
        },
        [["cosas", "acme"]],
        undefined,
        { clave: "cosa" },
      );
      return <button onClick={() => guardar.mutate(undefined)}>Guardar</button>;
    };
    render(<Con409 />, { wrapper: Envoltura });
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    await waitFor(() => expect(invalidar).toHaveBeenCalledWith({ queryKey: ["cosas", "acme"] }));
  });
});
