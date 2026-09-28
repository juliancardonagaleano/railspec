# Paquete de aprobación — <título de la unidad>

> Artefacto del **modo semi-autonomo**. Es lo que se presenta al humano en el único
> checkpoint duro (antes de implementar). Su propósito es que el humano decida
> con evidencia en minutos, sin releer tres documentos: qué se va a hacer, qué
> criticaron los agentes, qué se supuso y qué se va a tocar.
>
> No sustituye a `spec.md` / `plan.md` / `tasks.md`: los resume y enlaza.

## Petición original

La premisa tal como la dio el humano, literal.

## Qué se va a hacer (2-5 líneas)

Resumen ejecutivo del cambio. Si el humano solo lee esto, debe bastarle para
aceptar o rechazar.

**Perfil de esfuerzo:** `<perfil>` (`ligero` | `estandar` | `profundo`) — se
puede cambiar en este mismo checkpoint con `/sdd-perfil <nombre>`; rige desde
la siguiente invocación de subagente.

## Criterios de aceptación

| Id | Criterio | Tareas que lo cubren |
|---|---|---|
| CA-01 | ... | T1, T4 |

## Alcance del cambio

- **Archivos a crear/modificar:** N archivos (ver `plan.md`) — los de mayor impacto: `...`
- **Fuera de alcance (declarado):** ...
- **Comando de validación:** `...`

## Historial de gates

Qué criticaron los agentes en cada fase y qué se corrigió. Es la evidencia de
que el trabajo fue revisado, no solo generado.

Todas las columnas salen de `_estado.yaml > gates > <fase>`: `veredicto`,
`iteraciones`, `hallazgos_resueltos` y el tamaño de `hallazgos` (los que
quedaron abiertos), más `governance_consultada`.

| Fase | Veredicto | Iteraciones | Hallazgos resueltos | Hallazgos sin resolver | Governance |
|---|---|---|---|---|---|
| spec | refinado | 2 | 3 | 0 | si |
| plan | aprobado | 1 | 0 | 0 | si |
| tasks | ... | ... | ... | ... | ... |

**Correcciones más relevantes:** las 2-3 que cambiaron el diseño (no la lista completa; esa vive en `bitacora.md`).

## Suposiciones tomadas (requieren confirmación)

En modo semi-autonomo las preguntas abiertas se resuelven con la opción más
conservadora. Cada una se lista aquí para que el humano la confirme o corrija.

| # | Pregunta | Suposición tomada | Impacto si es errada |
|---|---|---|---|
| 1 | ... | ... | ... |

## Riesgos aceptados

- ...

## Gobernanza aplicada

`governance_refs`: `...` — consultada vía MCP el `<fecha>`.
Si alguna consulta quedó `parcial` o `no`, decirlo aquí explícitamente.

## Decisión

- [ ] **Aprobar** — implementar según `tasks.md`
- [ ] **Aprobar con cambios** — indicar cuáles (vuelve a la fase que corresponda)
- [ ] **Rechazar** — la unidad queda en `estado: bloqueado` con el motivo en bitácora
