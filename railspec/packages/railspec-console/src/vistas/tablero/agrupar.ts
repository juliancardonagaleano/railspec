import { FASES, type Fase, type ResumenUnidad } from "../../api/tipos";

/** Agrupa las unidades por fase, conservando el orden de llegada y todas las columnas (aunque vacías). */
export function agruparPorFase(unidades: readonly ResumenUnidad[]): Record<Fase, ResumenUnidad[]> {
  const columnas = Object.fromEntries(FASES.map((f) => [f, [] as ResumenUnidad[]])) as Record<Fase, ResumenUnidad[]>;
  for (const u of unidades) {
    const col = columnas[u.fase];
    if (col) col.push(u);
  }
  return columnas;
}

/** Quita duplicados (por id de unidad) al concatenar páginas por cursor. */
export function unirPaginas(paginas: readonly { unidades: ResumenUnidad[] }[]): ResumenUnidad[] {
  const vistas = new Set<string>();
  const salida: ResumenUnidad[] = [];
  for (const p of paginas)
    for (const u of p.unidades) {
      if (vistas.has(u.unidad)) continue;
      vistas.add(u.unidad);
      salida.push(u);
    }
  return salida;
}
