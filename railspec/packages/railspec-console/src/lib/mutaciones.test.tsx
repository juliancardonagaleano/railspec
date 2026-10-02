import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { ErrorApi } from "../api/cliente";
import { clienteDePrueba } from "../pruebas/servidor";
import { AvisoGuardado, useGuardado, useGuardar, type Guardado } from "./mutaciones";

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
