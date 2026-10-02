import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { claves, grafo, repositorios, unidades } from "../../api/endpoints";
import type { Insumo } from "../../chat/tipos";
import { Cargando, ErrorVista } from "../../componentes/Estados";
import { Button } from "../../componentes/ui/button";
import { Dialog } from "../../componentes/ui/dialog";
import { Campo, Input, Textarea } from "../../componentes/ui/input";
import { Select } from "../../componentes/ui/select";
import {
  formDesdeInsumo,
  FORM_VACIO,
  validarNuevaUnidad,
  valoresPorDefecto,
  type FilaRepositorio,
  type FormNuevaUnidad,
} from "./nuevaUnidad";

interface Props {
  org: string;
  ws: string;
  /** Insumo recién exportado con el que se precarga el formulario. */
  desdeInsumo?: Insumo | undefined;
  alCerrar: () => void;
}

/**
 * Arranca una unidad (`unit.start`) desde la consola. Con un insumo del chat lo precarga (objetivo,
 * repositorios y commits de la exportación); sin él, se puede pegar el id de uno ya exportado.
 * Se monta solo mientras está abierto: el estado inicial sale de las props.
 */
export function DialogoNuevaUnidad({ org, ws, desdeInsumo, alCerrar }: Props) {
  const navegar = useNavigate();
  const clienteQuery = useQueryClient();
  const vinculos = useQuery({ queryKey: claves.repositorios(org, ws), queryFn: () => repositorios.listar(org, ws) });
  const grafos = useQuery({ queryKey: [...claves.grafo(org, ws), "repositorios"], queryFn: () => grafo.repositorios(org, ws) });
  const [form, setForm] = useState<FormNuevaUnidad>(() => (desdeInsumo ? formDesdeInsumo(desdeInsumo) : FORM_VACIO));
  const [intentado, setIntentado] = useState(false);

  const listaVinculos = vinculos.data ?? [];
  const listaGrafos = grafos.data ?? [];
  const nombresVinculados = useMemo(() => listaVinculos.map((v) => v.alcance.repositorio), [listaVinculos]);

  const iniciar = useMutation({
    mutationFn: () => {
      if (!entrada) throw new Error("Formulario incompleto");
      return unidades.iniciar(entrada);
    },
    onSuccess: ({ estado }) => {
      void clienteQuery.invalidateQueries({ queryKey: claves.tablero(org, ws) });
      alCerrar();
      void navegar({ to: "/$org/$ws/unidades/$unidad", params: { org, ws, unidad: estado.unidad.unidad } });
    },
  });

  const cambiarFila = (i: number, parcial: Partial<FilaRepositorio>) =>
    setForm((f) => ({ ...f, repositorios: f.repositorios.map((r, j) => (j === i ? { ...r, ...parcial } : r)) }));
  const elegirRepositorio = (i: number, repositorio: string) =>
    cambiarFila(i, { repositorio, ...valoresPorDefecto(repositorio, listaVinculos, listaGrafos) });
  // Una rama vacía (p. ej. la de un insumo) se completa con la del vínculo en cuanto este llega.
  const filas = form.repositorios.map((r) => ({
    ...r,
    rama: r.rama || valoresPorDefecto(r.repositorio, listaVinculos, []).rama,
  }));
  const { errores, entrada } = validarNuevaUnidad({ ...form, repositorios: filas }, { org, workspace: ws });

  const enviar = (e: FormEvent) => {
    e.preventDefault();
    setIntentado(true);
    if (entrada) iniciar.mutate();
  };

  return (
    <Dialog
      abierto
      alCerrar={alCerrar}
      titulo="Nueva unidad"
      descripcion={
        desdeInsumo
          ? "Precargada con el insumo exportado del chat: la unidad lo recibirá como contexto."
          : "Arranca una unidad SDD en este workspace. Para usar un insumo del chat, pega su id."
      }
      className="max-w-2xl"
    >
      {vinculos.isPending || grafos.isPending ? <Cargando texto="Cargando repositorios…" /> : null}
      {vinculos.isError ? <ErrorVista error={vinculos.error} reintentar={() => void vinculos.refetch()} /> : null}
      {vinculos.isSuccess ? (
        <form onSubmit={enviar} className="flex flex-col gap-3" noValidate>
          <Campo etiqueta="Título" htmlFor="nu-titulo">
            <Input id="nu-titulo" required maxLength={200} value={form.titulo} onChange={(e) => setForm({ ...form, titulo: e.target.value })} />
          </Campo>
          <Campo etiqueta="Pedido" htmlFor="nu-pedido" ayuda="Qué se quiere y por qué; la spec se redacta a partir de esto.">
            <Textarea id="nu-pedido" required rows={5} maxLength={20000} value={form.pedido} onChange={(e) => setForm({ ...form, pedido: e.target.value })} />
          </Campo>
          <Campo
            etiqueta="Insumos del chat (ids)"
            htmlFor="nu-insumos"
            ayuda="Opcional. Uno por línea o separados por comas; solo valen los exportados en este workspace."
          >
            <Textarea id="nu-insumos" rows={2} value={form.insumos} onChange={(e) => setForm({ ...form, insumos: e.target.value })} />
          </Campo>
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-sm font-medium">Repositorios (el primero es el primario)</legend>
            {filas.map((fila, i) => (
              <div key={i} className="grid gap-2 rounded-md border border-borde p-2 sm:grid-cols-[1fr_1fr_2fr_auto]">
                <Campo etiqueta={`Repositorio ${i + 1}`} htmlFor={`nu-repo-${i}`}>
                  <Select
                    id={`nu-repo-${i}`}
                    vacio="Elige…"
                    value={fila.repositorio}
                    opciones={[
                      ...nombresVinculados.map((n) => ({ valor: n, etiqueta: n })),
                      ...(fila.repositorio && !nombresVinculados.includes(fila.repositorio)
                        ? [{ valor: fila.repositorio, etiqueta: `${fila.repositorio} (ya no vinculado)` }]
                        : []),
                    ]}
                    onChange={(e) => elegirRepositorio(i, e.target.value)}
                  />
                </Campo>
                <Campo etiqueta={`Rama ${i + 1}`} htmlFor={`nu-rama-${i}`}>
                  <Input id={`nu-rama-${i}`} value={fila.rama} maxLength={255} onChange={(e) => cambiarFila(i, { rama: e.target.value })} />
                </Campo>
                <Campo etiqueta={`Commit base ${i + 1}`} htmlFor={`nu-sha-${i}`}>
                  <Input
                    id={`nu-sha-${i}`}
                    className="font-mono text-xs"
                    value={fila.base_commit}
                    maxLength={40}
                    placeholder="sha completo de 40 caracteres"
                    onChange={(e) => cambiarFila(i, { base_commit: e.target.value })}
                  />
                </Campo>
                {form.repositorios.length > 1 ? (
                  <Button
                    variante="fantasma"
                    tamano="icono"
                    className="self-end"
                    aria-label={`Quitar repositorio ${i + 1}`}
                    onClick={() => setForm((f) => ({ ...f, repositorios: f.repositorios.filter((_, j) => j !== i) }))}
                  >
                    ✕
                  </Button>
                ) : null}
              </div>
            ))}
            <Button
              variante="secundario"
              tamano="pequeno"
              className="self-start"
              onClick={() => setForm((f) => ({ ...f, repositorios: [...f.repositorios, { repositorio: "", rama: "", base_commit: "" }] }))}
            >
              Añadir repositorio
            </Button>
          </fieldset>
          {intentado && errores.length > 0 ? (
            <ul role="alert" className="list-disc pl-5 text-sm text-peligro">
              {errores.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          ) : null}
          {iniciar.isError ? <ErrorVista error={iniciar.error} /> : null}
          <div className="flex justify-end gap-2">
            <Button variante="secundario" onClick={alCerrar}>
              Cancelar
            </Button>
            <Button type="submit" disabled={iniciar.isPending}>
              {iniciar.isPending ? "Arrancando…" : "Arrancar unidad"}
            </Button>
          </div>
        </form>
      ) : null}
    </Dialog>
  );
}
