# Rúbrica del gate — fase `tasks`

> Estructura de evaluación del `tasks.md`. **No contiene reglas de gobernanza**:
> las recupera del MCP (`pol-ia-no-embeber-conocimiento`).
>
> Este gate es el más mecánico de los cuatro: su lente principal (cobertura) se
> decide contando, no interpretando.

## Capa determinista (antes del panel)

- [ ] Existen las secciones: Pendientes, Validación final, Notas de
      implementación.
- [ ] Toda tarea tiene id `TN`, descripción y `archivos:`.
- [ ] Toda tarea declara `cubre:` con al menos un `CA-NN`.
- [ ] **Cobertura completa**: cada `CA-NN` de `spec.md` aparece en el `cubre:`
      de al menos una tarea. Un criterio huérfano hace fallar el gate aquí
      mismo, sin gastar panel.
- [ ] Todo `CA-NN` referenciado en tareas existe en el spec (no hay criterios
      inventados).
- [ ] Las dependencias (`depende de:`) apuntan a tareas que existen y no forman
      ciclos.
- [ ] El bloque "Validación final" conserva sus cuatro casillas.

## Lentes del panel

Un crítico por lente, en paralelo.

| Lente | Tipo | Se ejecuta en |
|---|---|---|
| L1 — Atomicidad y verificabilidad | **obligatorio** | todos los tiers |
| L3 — Fidelidad al plan (incluye gobernanza) | **obligatorio** | todos los tiers |
| L2 — Orden y dependencias | opcional | `medio` y `alto` |

> La cobertura `CA-NN`, que es el control principal de esta fase, ya se
> comprueba en la capa determinista: no depende de ningún lente ni del tier.

### L1 — Atomicidad y verificabilidad

- Una tarea = un cambio coherente que se puede terminar y verificar por sí
  solo. Señales de fallo:
  - Tareas que son en realidad fases ("implementar el backend").
  - Tareas cuyo enunciado no permite saber cuándo está hecha.
  - Tareas que tocan 15 archivos por razones distintas (hay que partirla).
  - Tareas de una línea que solo existen para inflar el checklist (hay que
    fusionarlas).
- El enunciado dice **qué queda distinto**, no "revisar" o "analizar".

### L2 — Orden y dependencias

- ¿El orden deja el repo en estado válido entre tareas, o hay un tramo roto?
- Dependencias faltantes: dos tareas que tocan el mismo archivo y una asume el
  resultado de la otra, sin declararlo.
- Dependencias sobrantes: tareas secuenciadas que en realidad son
  independientes — cuestan paralelismo en la implementación.
- Coherencia con los "Grupos de tareas paralelizables" del plan: si el plan
  declaró grupos, las tareas deben poder repartirse en ellos sin que dos grupos
  toquen el mismo archivo.

### L3 — Fidelidad al plan

- Tareas que hacen algo que el plan no contempla (deriva silenciosa).
- Elementos de la tabla "Archivos a crear/modificar" del plan que ninguna tarea
  toca (trabajo que se va a olvidar).
- Tareas que amplían el alcance "porque ya que estamos".
- **CONSULTAR** `pce-mcp` → tool `resolve_entity` → query: cada
  id de `governance_refs` → **APLICAR**: si un mandato exige un paso concreto
  (p. ej. un chequeo en el comando de validación, un piloto, un artefacto de
  gobernanza), debe existir una tarea que lo haga. Si no, hallazgo `alta`.
- Si el MCP no responde: no evaluar la parte de gobernanza de memoria; devolver
  `sin-gobernanza` y escalar.
  Eso vale cuando la superficie existe y no se alcanza; un repositorio
  **sin superficie de gobernanza** es el caso distinto que define
  `SKILL.md` § 3 ("Superficie caída ≠ superficie inexistente") — ahí el
  lente corre igual, para confirmar con evidencia del repo que no hay
  artefacto aplicable.

## Cómo puntuar la severidad

| Severidad | En esta fase significa |
|---|---|
| `alta` | `CA-NN` sin cobertura, dependencia circular, o un mandato de gobernanza sin tarea |
| `media` | Tarea no atómica, orden que rompe el repo, deriva respecto del plan |
| `baja` | Granularidad mejorable, redacción del enunciado |

## Hallazgos que requieren decisión humana (escalan, no se refinan)

- Un `CA-NN` que no se puede cubrir con las tareas posibles dentro del alcance
  declarado (el spec o el alcance tienen que cambiar).
- Descubrir en la descomposición que el plan es inviable como está.
