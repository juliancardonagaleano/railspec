import { describe, expect, it } from "vitest";
import { mandato } from "../../pruebas/mandatos";
import { filasPresupuesto } from "./ContenidoMandato";
import {
  estadoVisible,
  EXPLICACION_CAUSA,
  FORM_MANDATO_VACIO,
  formDesdeMandato,
  siguienteIdDelegacion,
  validarMandato,
  type FormMandato,
} from "./mandato";
import { CAUSAS_PARADA } from "../../api/tipos";

// Lógica pura del mandato: el estado que se muestra, el formulario y las mismas validaciones que el contrato.

const alcance = { org: "acme", workspace: "cert" };
const valido = (o: Partial<FormMandato> = {}): FormMandato => ({
  ...FORM_MANDATO_VACIO,
  id: "pdf-a",
  titulo: "Migrar a PDF/A",
  objetivo: "Dejar la emisión en PDF/A.",
  repositorios: ["api"],
  ...o,
});
const errores = (o: Partial<FormMandato>) => validarMandato(valido(o), alcance).errores;

describe("estadoVisible", () => {
  it("un aprobado fuera de vigencia es «caducado»; los demás estados no cambian", () => {
    expect(estadoVisible("aprobado", true)).toBe("aprobado");
    expect(estadoVisible("aprobado", false)).toBe("caducado");
    expect(estadoVisible("propuesto", false)).toBe("propuesto");
    expect(estadoVisible("parado", false)).toBe("parado");
    expect(estadoVisible("revocado", false)).toBe("revocado");
  });
});

describe("explicaciones de parada", () => {
  it("hay una por cada causa del contrato, con su ámbito", () => {
    for (const c of CAUSAS_PARADA) expect(EXPLICACION_CAUSA[c].texto).not.toBe("");
    expect(EXPLICACION_CAUSA["gate-escalado"].ambito).toBe("mandato");
    expect(EXPLICACION_CAUSA["unidad-amparada-fallida"].ambito).toBe("unidad");
    expect(EXPLICACION_CAUSA["reintentos-agotados"].ambito).toBe("unidad");
  });
});

describe("validarMandato", () => {
  it("un formulario completo da la entrada del contrato con sus defectos", () => {
    const { errores: e, entrada } = validarMandato(valido(), alcance);
    expect(e).toEqual([]);
    expect(entrada).toEqual({
      alcance,
      id: "pdf-a",
      contenido: {
        titulo: "Migrar a PDF/A",
        objetivo: "Dejar la emisión en PDF/A.",
        modo: "supervisado",
        limites: {
          repositorios: ["api"],
          max_unidades: 5,
          rutas_permitidas: [],
          presupuesto: {},
          reintentos_parada: 0,
          vigencia_horas: 24,
        },
        delegaciones: [],
      },
    });
    expect(entrada).not.toHaveProperty("version_vista");
  });

  it("editar manda la versión vista", () => {
    expect(validarMandato(valido(), alcance, 4).entrada?.version_vista).toBe(4);
  });

  it("recorta, parte las rutas por línea y solo manda los topes con valor", () => {
    const { entrada } = validarMandato(
      valido({ titulo: "  T  ", rutas: " src/pdf/** \n\n tests/** ", tokens_max: " 2000 ", costo_usd_max: "25.5", llamadas_max: "30" }),
      alcance,
    );
    expect(entrada?.contenido.titulo).toBe("T");
    expect(entrada?.contenido.limites.rutas_permitidas).toEqual(["src/pdf/**", "tests/**"]);
    expect(entrada?.contenido.limites.presupuesto).toEqual({ tokens_max: 2000, costo_usd_max: 25.5, llamadas_max: 30 });
  });

  it("desatendido exige al menos un tope de presupuesto; supervisado no", () => {
    expect(errores({ modo: "desatendido" })).toEqual([expect.stringMatching(/desatendido necesita al menos un tope/)]);
    expect(errores({ modo: "desatendido", segundos_max: "3600" })).toEqual([]);
    expect(errores({ modo: "supervisado" })).toEqual([]);
  });

  it("acota la vigencia (1 a 168 h), los reintentos (0 a 3) y las unidades (1 a 50)", () => {
    expect(errores({ vigencia_horas: "0" })).toHaveLength(1);
    expect(errores({ vigencia_horas: "169" })).toHaveLength(1);
    expect(errores({ vigencia_horas: "168" })).toEqual([]);
    expect(errores({ vigencia_horas: "1" })).toEqual([]);
    expect(errores({ reintentos_parada: "4" })).toHaveLength(1);
    expect(errores({ reintentos_parada: "3" })).toEqual([]);
    expect(errores({ reintentos_parada: "-1" })).toHaveLength(1);
    expect(errores({ max_unidades: "51" })).toHaveLength(1);
    expect(errores({ max_unidades: "0" })).toHaveLength(1);
    expect(errores({ max_unidades: "2,5" })).toHaveLength(1);
  });

  it("exige id slug, título, objetivo y al menos un repositorio", () => {
    expect(errores({ id: "PDF A" })).toHaveLength(1);
    expect(errores({ id: "-pdf" })).toHaveLength(1);
    expect(errores({ titulo: "  " })).toHaveLength(1);
    expect(errores({ objetivo: "" })).toHaveLength(1);
    expect(errores({ repositorios: [] })).toHaveLength(1);
    expect(validarMandato(valido({ id: "" }), alcance).entrada).toBeNull();
  });

  it("los topes deben ser positivos y las rutas relativas y sin «..»", () => {
    expect(errores({ costo_usd_max: "0" })).toHaveLength(1);
    expect(errores({ tokens_max: "1.5" })).toHaveLength(1);
    expect(errores({ segundos_max: "abc" })).toHaveLength(1);
    expect(errores({ rutas: "/etc/**" })).toHaveLength(1);
    expect(errores({ rutas: "src/../secretos/**" })).toHaveLength(1);
    expect(errores({ rutas: "src/**/*.py" })).toEqual([]);
  });

  it("valida las delegaciones: id D-n, único y con texto", () => {
    const d = (id: string, texto = "algo") => ({ id, tipo: "reservada" as const, texto });
    expect(errores({ delegaciones: [d("D-1"), d("D-2")] })).toEqual([]);
    expect(errores({ delegaciones: [d("D1")] })).toHaveLength(1);
    expect(errores({ delegaciones: [d("D-1"), d("D-1")] })).toEqual([expect.stringMatching(/repetida/)]);
    expect(errores({ delegaciones: [d("D-1", "  ")] })).toEqual([expect.stringMatching(/necesita texto/)]);
  });
});

describe("formulario", () => {
  it("formDesdeMandato y validarMandato son inversos sobre el contenido", () => {
    const m = mandato();
    const { entrada, errores: e } = validarMandato(formDesdeMandato(m), m.alcance, m.version);
    expect(e).toEqual([]);
    expect(entrada?.contenido).toEqual(m.contenido);
    expect(entrada?.version_vista).toBe(m.version);
  });

  it("la siguiente delegación toma el primer D-n libre", () => {
    expect(siguienteIdDelegacion([])).toBe("D-1");
    expect(siguienteIdDelegacion([{ id: "D-1" }, { id: "D-3" }])).toBe("D-2");
  });
});

describe("presupuesto frente al consumo", () => {
  it("lista solo los topes con valor, con lo consumido", () => {
    const filas = filasPresupuesto({ tokens_max: 1000, llamadas_max: 10 }, { tokens: 400, llamadas: 3 });
    expect(filas.map((f) => [f.etiqueta, f.consumido])).toEqual([
      ["Tokens", "400"],
      ["Llamadas al modelo", "3"],
    ]);
    expect(filasPresupuesto({})).toEqual([]);
    expect(filasPresupuesto({ segundos_max: 60 })[0]).toEqual({ etiqueta: "Segundos", tope: "60" });
  });
});
