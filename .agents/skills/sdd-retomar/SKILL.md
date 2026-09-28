---
name: sdd-retomar
description: "Use when: retomar una unidad de trabajo SDD existente (posiblemente de otra sesión) — leer su estado en disco y reportar exactamente dónde continuar. Keywords: retomar, continuar, handoff, dónde quedó, resume."
compatibility: "Claude-first (lee estado en disco). Ver `.agents/skills/COMPATIBILIDAD.md`"
license: "Proprietary"
metadata:
  user-invocable: "true"
---

# Skill: SDD — Retomar

Punto de entrada para continuar una unidad de trabajo existente, iniciada en otra sesión. Reconstruye el contexto desde los artefactos en disco — la única fuente de verdad que sobrevive al fin de una conversación.

> **Decisión directa `0123-D2` (Julian, 2026-09-21, bajo CR-2):** la lógica
> pesada de leer estado ya está en `.spec/scripts/sdd_retomar.py` (stdin
> determinista, ≤ 2 k tokens de salida, sin lanzar un agente). Esta skill
> ahora **delega** a ese script y resume su salida — antes era un agente
> Haiku `low` que, según la medición de `research.md §2.8`, llegó a gastar
> hasta 6,4 M tokens de salida en una sola sesión heredando el modelo del
> padre. La nueva ruta paga un solo Bash + un JSON de pocas líneas.

## Cuándo Usar / NO Usar

| Usar | NO Usar |
|------|---------|
| Continuar una unidad existente | Iniciar una nueva → `sdd-especificar` |
| Reconstruir contexto tras reiniciar la sesión | — |

## Flujo

### 1. Localizar la unidad (delegado al script)

- Si quien te invocó dio un id/slug o una ruta de directorio explícita (plan
  maestro, fixture), pasarlo como argumento a
  `python3 .spec/scripts/sdd_retomar.py <hint>`. Si dio un directorio bajo
  `.spec/units/<NNNN-slug>/`, el script lo abre por ruta literal; si dio un
  id, lo busca; si no dio nada, el script lista candidatas (`exit 2`).
- Esta skill corre inline (no forkeada — `0123-D2` retiró el `context: fork`
  y el `model/effort` del frontmatter porque ya no se invoca como agente).
  Si el script devuelve candidatas, **preguntar** al humano cuál retomar
  antes de seguir — eso es ahora responsabilidad de esta skill, no del
  script.

### 2. Reconstruir contexto (delegado al script + los pasos locales que siguen)

El script emite un JSON con `fase`, `modo`, `riesgo`, `gates`, `aprobacion`,
`governance_refs`, `tasks_pending`, `last_handoff`, `comando_validacion` y
`next` (la skill siguiente). Tu trabajo:

1. Transcribir el JSON del script al humano en prosa breve — sin copiar el
   JSON crudo, sino un resumen: fase, modo, qué falta, bloqueos.
2. **Reportar el estado de los gates**: el script ya entrega el bloque
   `gates`; tú lo conviertes en "qué fases fueron validadas, con qué
   veredicto, y si alguna quedó `escalado` con hallazgos abiertos". Un gate
   `escalado` es lo primero que hay que resolver: bloquea el avance.
3. `tasks.md` → qué está `[x]` y qué falta `[ ]` (si existe). El script ya
   devuelve `tasks_pending` con las primeras 10; para una vista completa,
   `Read` del archivo (no asumas, lee).
4. `spec.md` / `plan.md` según la fase — solo si el resumen del script no
   alcanza; el script no los copia, los cita por ruta.
5. Si `_estado.yaml` declara `modo: supervisado` (conducido por
   `sdd-supervisado`, `0109a`/`0109b`): correr el validador de
   mandatos en su modo `--resumen`, sobre el mandato referenciado por el
   campo `mandato` (id de plan o ruta de la unidad), y transcribir su salida
   íntegra en el reporte — `forma`, `mandato`, `estado-mandato`,
   `ultima-aprobacion` (o "sin aprobar"), `punto-de-retoma` (o "sin
   retoma"), `ultima-bitacora`, y la línea literal `retoma sin punto
   verificado` cuando el script la emita. `python3 .spec/scripts/report_git_sync.py --mandate <mandato> --unit <ruta>` (unidad `0114`,
   solo lee, S-4) transcribe además `coincide`/`diverge` de la rama y el
   commit observados frente a esa misma entrada de `## Punto de retoma`,
   sin decidir nada por sí mismo.

**Compatibilidad con unidades v1.** Una unidad creada antes de SDD v2 no tiene
`modo`, `riesgo`, `gates` ni `aprobacion_paquete`, y en cambio suele traer
`ultimo_cli` (campo retirado en v2). No es un error ni hay que "migrarla":
aplicar los defaults al leerla — `modo: interactivo`, `riesgo: medio`,
`gates: {}` — ignorar `ultimo_cli`, y escribir los campos nuevos solo si la
unidad avanza de fase.

### 3. Reportar y enrutar

Si el script devolvió candidatas en el paso 1: pedirle al humano cuál retomar
y volver a invocar el script con esa resolución — esta skill sí tiene canal
para preguntar, a diferencia del script.

Si la unidad quedó identificada:
- Resumir: fase actual, modo, qué falta, bloqueos (los datos vienen del
  script; tu trabajo es el resumen en prosa).
- **Reportar el estado de los gates**: leer el bloque `gates` del JSON del
  script y listar veredictos por fase. Un gate `escalado` es lo primero que
  hay que resolver: bloquea el avance.
- Si el script expone `modelo_ejecucion`, mencionarlo — la auditoría de qué
  subagente/modelo corrió cada fase (mecanismo propio de este repo, ver
  `.spec/MODELO-AGENTES.md`). Si no, omitir.
- Indicar la skill siguiente según `next` que ya devolvió el script (mapeo
  idéntico al de antes):

| Fase | Siguiente |
|---|---|
| `research` | Paso 0 de `sdd-orquestar` ("Investigar"): retoma la investigación, no la especificación |
| `spec` | `sdd-planificar` |
| `plan` | `sdd-tareas` |
| `tasks` | modo `interactivo` → `sdd-implementar`; modo `semi-autonomo` → redactar/presentar el paquete de aprobación (`fase: aprobacion`) |
| `aprobacion` | **la decisión humana está pendiente**: presentar el paquete, no implementar |
| `implement` | `sdd-implementar` |
| `done` | nada (cerrada) |

Nota: modo `supervisado`: lo conduce `sdd-supervisado`; esta tabla no
aplica bajo ese modo — la retoma de una instancia supervisada pasa por el
validador de mandatos (paso 2.5), no por este mapeo fase→skill.

- Dos condiciones tienen prioridad sobre esa tabla: si el gate de la fase
  actual quedó `escalado`, lo primero es resolver sus hallazgos abiertos; y
  si `aprobacion_paquete.decision` no es `aprobado` en una unidad semi-autonoma,
  no se implementa aunque la fase diga `implement`.

## Output

Reporte en prosa del estado de la unidad (fase, modo, gates, pendientes) +
recomendación de la fase/skill siguiente. La lectura de disco la hizo el
script; tu valor agregado es el resumen + la decisión de qué skill invocar
después.
