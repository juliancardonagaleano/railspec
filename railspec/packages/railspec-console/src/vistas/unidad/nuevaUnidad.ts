import type { Insumo } from "../../chat/tipos";
import {
  VERSION_CONTRATO_CLIENTE,
  type AlcanceWorkspace,
  type RepositorioGrafo,
  type UnitStartEntrada,
  type VinculoRepositorio,
} from "../../api/tipos";

export interface FilaRepositorio {
  repositorio: string;
  rama: string;
  base_commit: string;
}

export interface FormNuevaUnidad {
  titulo: string;
  pedido: string;
  /** Ids de insumo separados por espacios, comas o saltos de línea. */
  insumos: string;
  repositorios: FilaRepositorio[];
}

export const PATRON_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const PATRON_SHA = /^[0-9a-f]{40}$/;

export const FORM_VACIO: FormNuevaUnidad = {
  titulo: "",
  pedido: "",
  insumos: "",
  repositorios: [{ repositorio: "", rama: "", base_commit: "" }],
};

/** Ids únicos y en orden de aparición. */
export function insumosDeTexto(texto: string): string[] {
  return [...new Set(texto.split(/[\s,]+/).filter((t) => t !== ""))];
}

/** Rama por defecto del vínculo y commit canónico del grafo para un repositorio, si se conocen. */
export function valoresPorDefecto(
  repositorio: string,
  vinculos: readonly VinculoRepositorio[],
  grafos: readonly RepositorioGrafo[],
): Pick<FilaRepositorio, "rama" | "base_commit"> {
  return {
    rama: vinculos.find((v) => v.alcance.repositorio === repositorio)?.rama_por_defecto ?? "",
    base_commit: grafos.find((g) => g.repositorio === repositorio)?.commit ?? "",
  };
}

/**
 * Formulario precargado con un insumo recién exportado: su id, su objetivo como pedido y sus
 * repositorios (el primario primero) con el commit con que se exportó. La rama queda vacía: la
 * completa quien lo muestra con la del vínculo (`valoresPorDefecto`).
 */
export function formDesdeInsumo(insumo: Insumo): FormNuevaUnidad {
  const restricciones = insumo.restricciones.length > 0 ? `\n\nRestricciones:\n${insumo.restricciones.map((r) => `- ${r}`).join("\n")}` : "";
  const orden = [...insumo.repositorios].sort((a, b) => Number(b.rol === "primario") - Number(a.rol === "primario"));
  return {
    titulo: insumo.objetivo.slice(0, 200),
    pedido: `${insumo.objetivo}${restricciones}`,
    insumos: insumo.id,
    repositorios: orden.length
      ? orden.map((r) => ({ repositorio: r.repositorio, rama: "", base_commit: r.base_commit }))
      : FORM_VACIO.repositorios,
  };
}

export function validarNuevaUnidad(
  f: FormNuevaUnidad,
  alcance: AlcanceWorkspace,
): { errores: string[]; entrada: UnitStartEntrada | null } {
  const errores: string[] = [];
  const titulo = f.titulo.trim();
  const pedido = f.pedido.trim();
  if (!titulo) errores.push("El título es obligatorio.");
  if (titulo.length > 200) errores.push("El título admite hasta 200 caracteres.");
  if (!pedido) errores.push("El pedido es obligatorio.");
  if (pedido.length > 20000) errores.push("El pedido admite hasta 20000 caracteres.");

  const insumos = insumosDeTexto(f.insumos);
  for (const i of insumos) if (!PATRON_UUID.test(i)) errores.push(`«${i}» no es un id de insumo (uuid).`);

  const repositorios = f.repositorios.map((r) => ({
    repositorio: r.repositorio.trim(),
    rama: r.rama.trim(),
    base_commit: r.base_commit.trim().toLowerCase(),
  }));
  if (repositorios.length === 0) errores.push("Elige al menos un repositorio.");
  repositorios.forEach((r, i) => {
    const nombre = r.repositorio || `repositorio ${i + 1}`;
    if (!r.repositorio) errores.push(`Elige el repositorio ${i + 1}.`);
    if (!r.rama) errores.push(`Falta la rama de ${nombre}.`);
    if (!PATRON_SHA.test(r.base_commit)) errores.push(`El commit base de ${nombre} debe ser un sha completo (40 caracteres hexadecimales).`);
  });
  const vistos = new Set<string>();
  for (const r of repositorios) {
    if (r.repositorio && vistos.has(r.repositorio)) errores.push(`El repositorio ${r.repositorio} está repetido.`);
    vistos.add(r.repositorio);
  }

  if (errores.length > 0) return { errores, entrada: null };
  return {
    errores,
    entrada: {
      alcance,
      repositorios,
      titulo,
      pedido,
      ...(insumos.length > 0 ? { insumos } : {}),
      version_contrato_cliente: VERSION_CONTRATO_CLIENTE,
    },
  };
}
