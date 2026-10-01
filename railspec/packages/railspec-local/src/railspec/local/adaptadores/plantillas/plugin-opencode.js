// Generado por `railspec instalar`; no editar (se reescribe al reinstalar).
//
// Reglas de conducta de Railspec aplicadas en OpenCode. Cada decisión la toma
// `railspec hook opencode` (la misma guardia que el hook de Claude Code):
// - tool.execute.before: rechaza escrituras fuera de la orden vigente de la unidad.
// - config: abre la carpeta de worktrees de las unidades en permission.external_directory
//   al arrancar (la ruta es de cada máquina, así que no puede ir en opencode.json).
//   El hook permission.ask no se invoca en OpenCode 1.18, por eso se usa config.
// Salida de emergencia: RAILSPEC_GUARDIA=0 en el entorno de OpenCode.

const ESCRITURAS = new Set(["edit", "write", "multiedit", "patch", "apply_patch"])

const apagada = () => ["0", "no", "false", "off"].includes((process.env.RAILSPEC_GUARDIA ?? "").trim().toLowerCase())

async function consultar(directory, entrada) {
  const proceso = Bun.spawn(["railspec", "hook", "opencode"], {
    cwd: directory,
    stdin: new Blob([JSON.stringify({ ...entrada, directory })]),
    stdout: "pipe",
    stderr: "pipe",
  })
  const [salida, error, codigo] = await Promise.all([
    new Response(proceso.stdout).text(),
    new Response(proceso.stderr).text(),
    proceso.exited,
  ])
  if (codigo !== 0) throw new Error(`railspec hook opencode falló (${codigo}): ${error.trim()}`)
  return JSON.parse(salida)
}

export const Railspec = async ({ directory }) => ({
  config: async (config) => {
    if (apagada()) return
    try {
      const { worktrees } = await consultar(directory, { evento: "config" })
      if (!worktrees) return
      config.permission ??= {}
      if (typeof config.permission.external_directory === "string") {
        config.permission.external_directory = { "*": config.permission.external_directory }
      }
      config.permission.external_directory ??= {}
      config.permission.external_directory[`${worktrees}/**`] ??= "allow"
    } catch {
      // Sin `railspec` en el PATH OpenCode sigue preguntando por la carpeta, como antes.
    }
  },

  "tool.execute.before": async (entrada, salida) => {
    if (apagada() || !ESCRITURAS.has(entrada.tool)) return
    let veredicto
    try {
      veredicto = await consultar(directory, { evento: "tool", tool: entrada.tool, args: salida.args })
    } catch (error) {
      throw new Error(`Railspec: no pude comprobar la escritura (${error.message}). RAILSPEC_GUARDIA=0 la salta.`)
    }
    if (veredicto.decision === "deny") throw new Error(veredicto.motivo)
  },
})
