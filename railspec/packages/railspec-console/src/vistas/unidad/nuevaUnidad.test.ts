import { describe, expect, it } from "vitest";
import type { RepositorioGrafo, VinculoRepositorio } from "../../api/tipos";
import type { Insumo } from "../../chat/tipos";
import { FORM_VACIO, formDesdeInsumo, insumosDeTexto, validarNuevaUnidad, valoresPorDefecto, type FormNuevaUnidad } from "./nuevaUnidad";

const alcance = { org: "acme", workspace: "cert" };
const SHA = "a1b2c3d4e5".repeat(4);
const UUID = "0b8f6c1e-2d3a-4b5c-8d7e-9f0a1b2c3d4e";

const valido = (o: Partial<FormNuevaUnidad> = {}): FormNuevaUnidad => ({
  titulo: "Emitir PDF",
  pedido: "Firmar los certificados",
  insumos: "",
  repositorios: [{ repositorio: "api", rama: "main", base_commit: SHA }],
  ...o,
});

describe("insumosDeTexto", () => {
  it("separa por espacios, comas y saltos de línea, sin vacíos ni repetidos", () => {
    expect(insumosDeTexto(" a, b\nc  b ,,")).toEqual(["a", "b", "c"]);
    expect(insumosDeTexto("")).toEqual([]);
  });
});

describe("validarNuevaUnidad", () => {
  it("un formulario completo da la entrada de unit.start, recortada, con el sha en minúsculas", () => {
    const { errores, entrada } = validarNuevaUnidad(
      valido({
        titulo: "  Emitir PDF  ",
        pedido: "  Firmar  ",
        insumos: `${UUID}, ${UUID}`,
        repositorios: [{ repositorio: " api ", rama: " main ", base_commit: ` ${SHA.toUpperCase()} ` }],
      }),
      alcance,
    );
    expect(errores).toEqual([]);
    expect(entrada).toMatchObject({
      alcance,
      titulo: "Emitir PDF",
      pedido: "Firmar",
      insumos: [UUID],
      repositorios: [{ repositorio: "api", rama: "main", base_commit: SHA }],
    });
  });

  it("sin insumos no manda el campo", () => {
    expect(validarNuevaUnidad(valido(), alcance).entrada).not.toHaveProperty("insumos");
  });

  it("el formulario vacío lista lo que falta y no da entrada", () => {
    const { errores, entrada } = validarNuevaUnidad(FORM_VACIO, alcance);
    expect(entrada).toBeNull();
    expect(errores).toEqual([
      "El título es obligatorio.",
      "El pedido es obligatorio.",
      "Elige el repositorio 1.",
      "Falta la rama de repositorio 1.",
      "El commit base de repositorio 1 debe ser un sha completo (40 caracteres hexadecimales).",
    ]);
  });

  it("rechaza insumos que no son uuid, shas cortos, repositorios repetidos y textos largos", () => {
    const { errores } = validarNuevaUnidad(
      valido({
        titulo: "t".repeat(201),
        pedido: "p".repeat(20001),
        insumos: `${UUID} no-es-uuid`,
        repositorios: [
          { repositorio: "api", rama: "main", base_commit: "abc123" },
          { repositorio: "api", rama: "main", base_commit: SHA },
        ],
      }),
      alcance,
    );
    expect(errores).toEqual([
      "El título admite hasta 200 caracteres.",
      "El pedido admite hasta 20000 caracteres.",
      "«no-es-uuid» no es un id de insumo (uuid).",
      "El commit base de api debe ser un sha completo (40 caracteres hexadecimales).",
      "El repositorio api está repetido.",
    ]);
  });

  it("sin repositorios pide al menos uno", () => {
    expect(validarNuevaUnidad(valido({ repositorios: [] }), alcance).errores).toContain("Elige al menos un repositorio.");
  });
});

describe("valoresPorDefecto", () => {
  const vinculos = [{ alcance: { ...alcance, repositorio: "api" }, rama_por_defecto: "trunk" }] as VinculoRepositorio[];
  const grafos: RepositorioGrafo[] = [{ repositorio: "api", nivel_codigo: "restringido", rol: "primario", commit: SHA }];

  it("toma la rama del vínculo y el commit canónico del grafo", () => {
    expect(valoresPorDefecto("api", vinculos, grafos)).toEqual({ rama: "trunk", base_commit: SHA });
  });

  it("deja vacío lo que no se conoce", () => {
    expect(valoresPorDefecto("otro", vinculos, grafos)).toEqual({ rama: "", base_commit: "" });
    expect(valoresPorDefecto("api", vinculos, [{ ...grafos[0]!, commit: null }])).toEqual({ rama: "trunk", base_commit: "" });
  });
});

describe("formDesdeInsumo", () => {
  const insumo = {
    id: UUID,
    objetivo: "Firmar certificados",
    restricciones: ["sin romper la API", "con pruebas"],
    repositorios: [
      { repositorio: "lib", rol: "transversal", base_commit: "b".repeat(40) },
      { repositorio: "api", rol: "primario", base_commit: SHA },
    ],
  } as Insumo;

  it("precarga id, objetivo con restricciones y repositorios con el primario primero y sin rama", () => {
    expect(formDesdeInsumo(insumo)).toEqual({
      titulo: "Firmar certificados",
      pedido: "Firmar certificados\n\nRestricciones:\n- sin romper la API\n- con pruebas",
      insumos: UUID,
      repositorios: [
        { repositorio: "api", rama: "", base_commit: SHA },
        { repositorio: "lib", rama: "", base_commit: "b".repeat(40) },
      ],
    });
  });

  it("sin restricciones ni repositorios deja el pedido limpio y una fila vacía", () => {
    const f = formDesdeInsumo({ ...insumo, restricciones: [], repositorios: [] });
    expect(f.pedido).toBe("Firmar certificados");
    expect(f.repositorios).toEqual(FORM_VACIO.repositorios);
  });

  it("recorta el título a 200 caracteres", () => {
    expect(formDesdeInsumo({ ...insumo, objetivo: "x".repeat(300) }).titulo).toHaveLength(200);
  });
});
