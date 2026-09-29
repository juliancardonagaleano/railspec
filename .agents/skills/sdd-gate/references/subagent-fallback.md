# Fallback de `subagent_type` para roles `sdd-*` no registrados

> Documento de referencia creado por unit 0003 (fix 4, CA-08). Aplicable a
> cualquier rol `sdd-*` que `effort_profile.py` resuelva pero opencode no
> reconozca como subagent_type. Análogo al patrón de críticos del skill
> `sdd-gate/SKILL.md:189-195`.

## Política

Cuando el orquestador dispatcha un rol `sdd-*` y opencode devuelve
`Unknown agent type`, la degradación es **explícita y trazable**, no
silenciosa. Tres pasos:

1. **Anotar en `bitacora.md`** qué rol se degradó (formato canónico del
   nombre del rol, p.ej. `sdd-explorador`).
2. **Inyectar en el prompt** del subagente (o en el mensaje del
   orquestador, según el caso) las instrucciones del rol desde el archivo
   `.agents/agents/<rol>.md` (frontmatter + cuerpo). El orquestador debe
   leer ese archivo y pegar las instrucciones en el prompt antes de
   dispatchar.
3. **Persistir en `_estado.yaml > modelo_ejecucion.<fase>`** el campo
   `subagente` con el valor literal `general` (opencode) o `general-purpose`
   (claude), no con el nombre del rol previsto. El modelo y el perfil
   conservados son los resueltos por `effort_profile.py resolve --role <rol>`.

## Roles cubiertos

| Rol | Archivo fuente | Notas |
|---|---|---|
| `sdd-explorador` | `.agents/agents/sdd-explorador.md` | Vía / gobernanza / consumidores / historia |
| `sdd-especificar-redactor` | `.agents/agents/sdd-especificar-redactor.md` | Redacción de `spec.md` |
| `sdd-planificar-redactor` | `.agents/agents/sdd-planificar-redactor.md` | Redacción de `plan.md` |
| `sdd-tareas-redactor` | `.agents/agents/sdd-tareas-redactor.md` | Redacción de `tasks.md` |
| `sdd-implementador` | (a crear) | Implementación genérica |
| `sdd-implementador-xhigh` | (a crear) | Implementación con effort xhigh |
| `sdd-critico-profundo` | `.agents/agents/sdd-critico-profundo.md` | Crítico L1/L2/L3/L4 |
| `sdd-critico-estructural` | `.agents/agents/sdd-critico-estructural.md` | Crítico atómico/trazabilidad |
| `sdd-critico-cumplimiento` | `.agents/agents/sdd-critico-cumplimiento.md` | Crítico CA + encaje |
| `sdd-refutador` | `.agents/agents/sdd-refutador.md` | Verificación adversarial (tier alto o perfil profundo) |

Los roles marcados "(a crear)" no tienen archivo fuente todavía. La regla de
fallback **no aplica** a esos roles hasta que su archivo exista: el
orquestador escala con causa `rol-sin-definicion` en vez de degradar.

## Diferencia con la regla de críticos (sdd-gate/SKILL.md:189-195)

La regla de críticos es específica: solo se activa para `sdd-critico-*` y
`sdd-refutador`. La regla de fallback de este documento es **general**:
cubre cualquier rol `sdd-*` que opencode no reconozca, no solo los críticos.
El incidente del 2026-09-29 demostró que la inconsistencia se da en
cualquier rol, no solo en los críticos.

## Test de cumplimiento (CA-08)

Un test estructural (`u0003_ca08_*`) verifica:

- El archivo existe en
  `.agents/skills/sdd-gate/references/subagent-fallback.md`.
- La sección "Política" enumera los tres pasos (anotar, inyectar, persistir).
- La sección "Roles cubiertos" lista al menos `sdd-explorador`,
  `sdd-planificar-redactor`, `sdd-tareas-redactor` y los tres críticos.
- El archivo se referencia desde `sdd-gate/SKILL.md` en la sección de
  fallback (junto a la regla existente de críticos).
