import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { ErrorApi } from "../../api/cliente";
import { claves, mandatos, repositorios } from "../../api/endpoints";
import { TIPOS_DELEGACION, type Mandato, type ModoConMandato, type TipoDelegacion } from "../../api/tipos";
import { Aviso, Cargando, ErrorVista } from "../../componentes/Estados";
import { Button } from "../../componentes/ui/button";
import { Dialog } from "../../componentes/ui/dialog";
import { Campo, Input, Textarea } from "../../componentes/ui/input";
import { Select } from "../../componentes/ui/select";
import {
  FORM_MANDATO_VACIO,
  formDesdeMandato,
  siguienteIdDelegacion,
  TEXTO_TIPO_DELEGACION,
  validarMandato,
  type FormMandato,
} from "./mandato";

/**
 * Crea un mandato nuevo o edita uno existente (`mandate.propose`). Editar lo devuelve a «propuesto» y
 * exige una aprobación nueva. Se monta solo mientras está abierto: el estado inicial sale de las props, y la
 * versión que se edita queda fijada al abrir (si el mandato cambia, el 409 obliga a reabrir).
 */
export function FormularioMandato({ org, ws, mandato, alCerrar }: { org: string; ws: string; mandato?: Mandato; alCerrar: () => void }) {
  const clienteQuery = useQueryClient();
  const navegar = useNavigate();
  const vinculos = useQuery({ queryKey: claves.repositorios(org, ws), queryFn: () => repositorios.listar(org, ws) });
  const [form, setForm] = useState<FormMandato>(() => (mandato ? formDesdeMandato(mandato) : FORM_MANDATO_VACIO));
  const [versionVista] = useState(mandato?.version);
  const [intentado, setIntentado] = useState(false);
  const [desactualizado, setDesactualizado] = useState(false);
  const editando = mandato !== undefined;

  const alcance = { org, workspace: ws };
  const { errores, entrada } = validarMandato(form, alcance, versionVista);

  const guardar = useMutation({
    mutationFn: () => {
      if (!entrada) throw new Error("Formulario incompleto");
      return mandatos.proponer(entrada);
    },
    onSuccess: ({ mandato: m }) => {
      void clienteQuery.invalidateQueries({ queryKey: claves.mandatos(org, ws) });
      alCerrar();
      if (!editando) void navegar({ to: "/$org/$ws/mandatos/$mandato", params: { org, ws, mandato: m.id } });
    },
    onError: (e) => {
      if (e instanceof ErrorApi && e.status === 409) {
        setDesactualizado(true);
        void clienteQuery.invalidateQueries({ queryKey: claves.mandatos(org, ws) });
      }
    },
  });

  const enviar = (e: FormEvent) => {
    e.preventDefault();
    setIntentado(true);
    setDesactualizado(false);
    if (entrada) guardar.mutate();
  };

  const poner = <K extends keyof FormMandato>(k: K, v: FormMandato[K]) => setForm((f) => ({ ...f, [k]: v }));
  const nombresVinculados = (vinculos.data ?? []).map((v) => v.alcance.repositorio);
  // Un repositorio del mandato que ya no está vinculado se sigue mostrando, para poder quitarlo.
  const nombres = [...new Set([...nombresVinculados, ...form.repositorios])].sort();
  const alternar = (r: string) =>
    poner("repositorios", form.repositorios.includes(r) ? form.repositorios.filter((x) => x !== r) : [...form.repositorios, r]);
  const cambiarDelegacion = (i: number, parcial: Partial<FormMandato["delegaciones"][number]>) =>
    poner("delegaciones", form.delegaciones.map((d, j) => (j === i ? { ...d, ...parcial } : d)));

  return (
    <Dialog
      abierto
      alCerrar={alCerrar}
      titulo={editando ? `Editar el mandato ${mandato.id}` : "Nuevo mandato"}
      descripcion={
        editando
          ? "Al guardar, el mandato vuelve a «propuesto» y exigirá una aprobación nueva: las unidades que corrían quedan retenidas hasta entonces."
          : "Un mandato es una autorización acotada y con caducidad para varias unidades. Redactarlo no lo aprueba: eso se hace aparte, sobre el contenido exacto."
      }
      className="max-w-3xl"
    >
      {vinculos.isPending ? <Cargando texto="Cargando repositorios…" /> : null}
      {vinculos.isError ? <ErrorVista error={vinculos.error} reintentar={() => void vinculos.refetch()} /> : null}
      {vinculos.isSuccess ? (
        <form onSubmit={enviar} className="flex flex-col gap-3" noValidate>
          {editando && mandato.estado !== "propuesto" ? (
            <Aviso tono="aviso">
              Este mandato está {mandato.estado}. Guardar cambios lo devuelve a <strong>propuesto</strong>, borra la parada y exige aprobarlo de nuevo.
            </Aviso>
          ) : null}
          <div className="grid gap-3 sm:grid-cols-2">
            <Campo etiqueta="Id (es el plan de las unidades)" htmlFor="mf-id" ayuda="Minúsculas, dígitos y guiones; no se puede cambiar después.">
              <Input id="mf-id" required maxLength={63} disabled={editando} value={form.id} onChange={(e) => poner("id", e.target.value)} />
            </Campo>
            <Campo etiqueta="Modo" htmlFor="mf-modo" ayuda="Todas las unidades del mandato comparten este modo.">
              <Select
                id="mf-modo"
                value={form.modo}
                onChange={(e) => poner("modo", e.target.value as ModoConMandato)}
                opciones={[
                  { valor: "supervisado", etiqueta: "supervisado (un gate rojo congela el mandato)" },
                  { valor: "desatendido", etiqueta: "desatendido (un gate rojo difiere la unidad)" },
                ]}
              />
            </Campo>
          </div>
          <Campo etiqueta="Título" htmlFor="mf-titulo">
            <Input id="mf-titulo" required maxLength={200} value={form.titulo} onChange={(e) => poner("titulo", e.target.value)} />
          </Campo>
          <Campo etiqueta="Objetivo" htmlFor="mf-objetivo" ayuda="Qué se quiere lograr y cuándo se da por terminado.">
            <Textarea id="mf-objetivo" required rows={4} maxLength={4000} value={form.objetivo} onChange={(e) => poner("objetivo", e.target.value)} />
          </Campo>

          <fieldset className="flex flex-col gap-1">
            <legend className="mb-1 text-sm font-medium">Repositorios (los vinculados al workspace)</legend>
            {nombres.length === 0 ? <p className="text-sm text-suave">Este workspace aún no tiene repositorios vinculados.</p> : null}
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {nombres.map((r) => (
                <label key={r} className="flex items-center gap-1 text-sm">
                  <input type="checkbox" checked={form.repositorios.includes(r)} onChange={() => alternar(r)} />
                  {r}
                  {nombresVinculados.includes(r) ? "" : " (ya no vinculado)"}
                </label>
              ))}
            </div>
          </fieldset>

          <div className="grid gap-3 sm:grid-cols-3">
            <Campo etiqueta="Máx. unidades" htmlFor="mf-max" ayuda="1 a 50.">
              <Input id="mf-max" inputMode="numeric" value={form.max_unidades} onChange={(e) => poner("max_unidades", e.target.value)} />
            </Campo>
            <Campo etiqueta="Reintentos de parada" htmlFor="mf-reintentos" ayuda="0 a 3 por orden fallida.">
              <Input id="mf-reintentos" inputMode="numeric" value={form.reintentos_parada} onChange={(e) => poner("reintentos_parada", e.target.value)} />
            </Campo>
            <Campo etiqueta="Vigencia (horas)" htmlFor="mf-vigencia" ayuda="1 a 168; cuánto vale cada aprobación.">
              <Input id="mf-vigencia" inputMode="numeric" value={form.vigencia_horas} onChange={(e) => poner("vigencia_horas", e.target.value)} />
            </Campo>
          </div>

          <Campo
            etiqueta="Rutas permitidas"
            htmlFor="mf-rutas"
            ayuda="Una por línea, relativas a la raíz del repositorio (glob). Vacío: sin restricción adicional a la del plan de cada unidad."
          >
            <Textarea id="mf-rutas" rows={3} className="font-mono text-xs" value={form.rutas} onChange={(e) => poner("rutas", e.target.value)} />
          </Campo>

          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-sm font-medium">Presupuesto total del mandato</legend>
            <p className="text-xs text-suave">
              Suma del consumo de todas sus unidades; al alcanzarse detiene el mandato entero. Vacío = sin tope.
              {form.modo === "desatendido" ? " Un mandato desatendido exige al menos un tope." : ""}
            </p>
            <div className="grid gap-3 sm:grid-cols-4">
              <Campo etiqueta="Tokens máx." htmlFor="mf-tokens">
                <Input id="mf-tokens" inputMode="numeric" value={form.tokens_max} onChange={(e) => poner("tokens_max", e.target.value)} />
              </Campo>
              <Campo etiqueta="Costo USD máx." htmlFor="mf-costo">
                <Input id="mf-costo" inputMode="decimal" value={form.costo_usd_max} onChange={(e) => poner("costo_usd_max", e.target.value)} />
              </Campo>
              <Campo etiqueta="Segundos máx." htmlFor="mf-segundos">
                <Input id="mf-segundos" inputMode="numeric" value={form.segundos_max} onChange={(e) => poner("segundos_max", e.target.value)} />
              </Campo>
              <Campo etiqueta="Llamadas máx." htmlFor="mf-llamadas">
                <Input id="mf-llamadas" inputMode="numeric" value={form.llamadas_max} onChange={(e) => poner("llamadas_max", e.target.value)} />
              </Campo>
            </div>
          </fieldset>

          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-sm font-medium">Delegaciones</legend>
            <p className="text-xs text-suave">
              Decisiones que el mandato ya tomó (pre-decidida), deja a criterio (con-criterio) o nunca delega (reservada).
            </p>
            {form.delegaciones.map((d, i) => (
              <div key={i} className="grid gap-2 rounded-md border border-borde p-2 sm:grid-cols-[7rem_14rem_1fr_auto]">
                <Campo etiqueta={`Id de la delegación ${i + 1}`} htmlFor={`mf-d-id-${i}`}>
                  <Input id={`mf-d-id-${i}`} className="font-mono text-xs" maxLength={6} value={d.id} onChange={(e) => cambiarDelegacion(i, { id: e.target.value })} />
                </Campo>
                <Campo etiqueta={`Tipo de la delegación ${i + 1}`} htmlFor={`mf-d-tipo-${i}`}>
                  <Select
                    id={`mf-d-tipo-${i}`}
                    value={d.tipo}
                    onChange={(e) => cambiarDelegacion(i, { tipo: e.target.value as TipoDelegacion })}
                    opciones={TIPOS_DELEGACION.map((t) => ({ valor: t, etiqueta: TEXTO_TIPO_DELEGACION[t] }))}
                  />
                </Campo>
                <Campo etiqueta={`Texto de la delegación ${i + 1}`} htmlFor={`mf-d-texto-${i}`}>
                  <Textarea id={`mf-d-texto-${i}`} rows={2} maxLength={2000} value={d.texto} onChange={(e) => cambiarDelegacion(i, { texto: e.target.value })} />
                </Campo>
                <Button
                  variante="fantasma"
                  tamano="icono"
                  className="self-end"
                  aria-label={`Quitar la delegación ${i + 1}`}
                  onClick={() => poner("delegaciones", form.delegaciones.filter((_, j) => j !== i))}
                >
                  ✕
                </Button>
              </div>
            ))}
            <Button
              variante="secundario"
              tamano="pequeno"
              className="self-start"
              onClick={() =>
                poner("delegaciones", [...form.delegaciones, { id: siguienteIdDelegacion(form.delegaciones), tipo: "con-criterio", texto: "" }])
              }
            >
              Añadir delegación
            </Button>
          </fieldset>

          {intentado && errores.length > 0 ? (
            <ul role="alert" className="list-disc pl-5 text-sm text-peligro">
              {errores.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          ) : null}
          {desactualizado ? (
            <Aviso tono="aviso">
              El mandato cambió mientras lo editabas; ya se recargó y tus cambios no se guardaron. Cierra este formulario y ábrelo de nuevo para partir de la
              versión vigente.
            </Aviso>
          ) : null}
          {guardar.isError && !desactualizado ? <ErrorVista error={guardar.error} /> : null}
          <div className="flex justify-end gap-2">
            <Button variante="secundario" onClick={alCerrar}>
              Cancelar
            </Button>
            <Button type="submit" disabled={guardar.isPending}>
              {guardar.isPending ? "Guardando…" : editando ? "Guardar como propuesto" : "Crear mandato"}
            </Button>
          </div>
        </form>
      ) : null}
    </Dialog>
  );
}
