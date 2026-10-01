import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Actor, EstadoUnidad } from "../../api/tipos";
import { PestanaGates } from "./PestanaGates";

// La API de la consola sirve los hallazgos sin `evidencia` ni `propuesta` (texto libre de los
// críticos que puede citar código): la ficha del gate debe pintarse completa con esa forma.

const julian: Actor = { tipo: "humano", canal: "consola", github_id: 1, login: "julian" };

const estado: EstadoUnidad = {
  unidad: { org: "acme", workspace: "cert", unidad: "0001-firma" },
  version: 4,
  titulo: "Emitir PDF firmado",
  dueno: julian,
  repositorios: [{ repositorio: "api", rol: "primario", base_commit: "a".repeat(40) }],
  fase: "implement",
  estado: "en-progreso",
  modo: "interactivo",
  riesgo: "medio",
  perfil: "estandar",
  creado_en: "2026-09-30T10:00:00Z",
  actualizado_en: "2026-09-30T11:00:00Z",
  actualizado_por: julian,
  gates: {
    spec: {
      veredicto: "aprobado",
      iteraciones: 1,
      gobernanza_consultada: "si",
      cerrado_en: "2026-09-30T10:30:00Z",
      criticos: ["alcance", "seguridad"],
      hallazgos: [
        {
          id: "H-1",
          gate: "spec",
          lente: "seguridad",
          severidad: "baja",
          titulo: "Falta validar la firma",
          cita: { ruta: "src/pdf.py", linea_inicio: 12, linea_fin: 20 },
          criterio: "CA-01",
          refutado: false,
        },
        {
          id: "H-2",
          gate: "spec",
          lente: "alcance",
          severidad: "baja",
          titulo: "Sección ambigua",
          cita: { seccion: "Alcance" },
          refutado: true,
        },
      ],
    },
  },
};

describe("pestaña de gates con la forma que sirve la API", () => {
  it("pinta título, lente y cita de cada hallazgo sin evidencia ni propuesta", () => {
    render(<PestanaGates estado={estado} />);
    expect(screen.getByText("Falta validar la firma")).toBeInTheDocument();
    expect(screen.getByText(/Lente seguridad · src\/pdf\.py:12/)).toBeInTheDocument();
    expect(screen.getByText(/Lente alcance · sección Alcance/)).toBeInTheDocument();
    expect(screen.getByText("CA-01")).toBeInTheDocument();
    expect(screen.getByText("refutado")).toBeInTheDocument();
  });
});
