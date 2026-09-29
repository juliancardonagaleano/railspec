# Rúbrica del gate — fase `spec`

> Estructura de evaluación del `spec.md`. **No contiene reglas de gobernanza**:
> las recupera del MCP (`pol-ia-no-embeber-conocimiento`). Lo que vive aquí es
> qué mirar y cómo decidir, no qué dice cada ADR.

## Capa determinista (antes del panel)

- [ ] Existen las secciones: Problema/Motivación, Resultado esperado, Alcance
      (incluye **y** no incluye), Criterios de aceptación, Governance aplicable,
      Preguntas abiertas.
- [ ] Hay al menos un criterio de aceptación y **todos** llevan id `CA-NN`
      correlativo, sin saltos ni duplicados.
- [ ] La sección "No incluye" no está vacía (un alcance sin frontera no es un
      alcance).
- [ ] `_estado.yaml > governance_refs` está poblado **y** cada id de la tabla
      "Governance aplicable" aparece en `governance_refs` (y viceversa).
      Si de verdad no aplica ninguna gobernanza, vale el centinela explícito
      `governance_refs: [ninguna-aplicable]` con la justificación en la tabla
      del spec; lo que no vale es la lista vacía por omisión. El centinela exime
      de la comprobación de correspondencia, no del lente L4: el crítico debe
      confirmar que efectivamente no hay artefacto aplicable.
- [ ] Ninguna sección conserva el texto de ejemplo de la plantilla.

## Lentes del panel

Un crítico por lente, en paralelo.

| Lente | Tipo | Se ejecuta en |
|---|---|---|
| L1 — Testeabilidad | **obligatorio** | todos los tiers |
| L4 — Gobernanza | **obligatorio** | todos los tiers |
| L2 — Ambigüedad | opcional | `medio` y `alto` |
| L3 — Alcance | opcional | `alto` |

### L1 — Testeabilidad de los criterios

Por cada `CA-NN`, preguntar: ¿puede alguien que no participó en la conversación
decidir si se cumple, mirando solo el repo? Señales de fallo:

- Adjetivos no medibles: "robusto", "limpio", "mejor", "eficiente", "adecuado".
- Criterios que describen actividad en vez de resultado ("se revisa X" en lugar
  de "X cumple Y").
- Criterios que requieren leer la mente del autor para saber qué cuenta como
  cumplido.
- Dos cosas verificables metidas en un solo criterio (hay que partirlo: si una
  se cumple y la otra no, la casilla no se puede marcar).

### L2 — Ambigüedad y omisiones

- Términos usados con más de un significado dentro del propio spec.
- Comportamiento en el caso de error / vacío / concurrente **no** especificado
  cuando el cambio lo toca.
- Actores no nombrados: quién dispara el cambio, quién lo consume.
- Resultado esperado que describe la solución técnica en vez del estado final
  (el CÓMO invadiendo el QUÉ — eso pertenece a `plan.md`).

### L3 — Alcance

- Elementos del "Incluye" que no responden al problema declarado (scope creep).
- Problema declarado que ningún criterio de aceptación cubre (scope gap) —
  **este es el hallazgo más caro de encontrar tarde**.
- "No incluye" que en realidad es necesario para que los criterios se cumplan.
- Unidad tan grande que ningún humano puede revisarla de una sentada: proponer
  partirla (hallazgo de severidad `media`, no `alta`).

### L4 — Gobernanza

Evaluar contra los artefactos recuperados en el paso 3 del gate, no de memoria.

- **CONSULTAR** `pce-mcp` → tool `resolve_entity` → query: cada
  id de `governance_refs` → **APLICAR**: ¿el spec contradice algún mandato
  MUST? ¿La tabla explica *cómo aplica* cada uno, o solo lo cita?
- **APLICAR** el baseline indexado en `AGENTS.md` aunque no esté en
  `governance_refs` (su ausencia ahí no es hallazgo); descartar todo artefacto
  del dominio `ia`, rechazado por mandato de ese archivo.
- **CONSULTAR** `pce-mcp` → tools `traverse_knowledge` (query = objeto de la
  unidad) y `search_catalog` acotado por `domain`/`query` a ese objeto, con
  `type` en `adr`, `policy` y `principle` → **APLICAR**: ¿hay un artefacto
  **específico del objeto** que el spec no listó? Si lo hay, es hallazgo
  `alta` ("gobernanza omitida").
- Precedencia: un conflicto `Principio > ADR > Policy` mal resuelto es hallazgo
  `alta`.
- Si el MCP no responde: **no evaluar este lente de memoria**. Devolver el
  hallazgo `sin-gobernanza` y dejar que el gate escale.
  Eso vale cuando la superficie existe y no se alcanza; un repositorio
  **sin superficie de gobernanza** es el caso distinto que define
  `SKILL.md` § 3 ("Superficie caída ≠ superficie inexistente") — ahí el
  lente corre igual, para confirmar con evidencia del repo que no hay
  artefacto aplicable.

## Cómo puntuar la severidad

| Severidad | En esta fase significa |
|---|---|
| `alta` | Incumple gobernanza, o un criterio no es verificable, o hay scope gap |
| `media` | Ambigüedad real que obligará a decidir en implementación |
| `baja` | Redacción mejorable sin consecuencia operativa |

## Hallazgos que requieren decisión humana (escalan, no se refinan)

- Conflicto entre dos artefactos de gobernanza del mismo tier.
- Ambigüedad cuya resolución cambia el alcance del trabajo.
- Criterio de aceptación que el autor no puede hacer verificable sin decidir
  algo de negocio.
