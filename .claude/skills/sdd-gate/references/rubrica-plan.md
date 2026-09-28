# Rúbrica del gate — fase `plan`

> Estructura de evaluación del `plan.md`. **No contiene reglas de gobernanza**:
> las recupera del MCP (`pol-ia-no-embeber-conocimiento`).

## Capa determinista (antes del panel)

- [ ] Existen las secciones: Enfoque, Archivos a crear/modificar, Reutilización,
      Decisiones de diseño, Grupos de tareas paralelizables, Riesgos y
      mitigaciones, Comando de validación.
- [ ] La tabla de archivos tiene al menos una fila y ninguna ruta es un
      placeholder (`ruta/al/archivo`).
- [ ] Hay un comando de validación concreto y **ejecutable** (no "correr los
      tests"), registrado también en `_estado.yaml > comando_validacion`.
- [ ] Cada riesgo tiene su mitigación (ninguno queda enunciado y suelto).
- [ ] Los grupos paralelizables tienen archivos **disjuntos** entre sí, o el
      plan declara explícitamente "grupo único — cambios acoplados".

## Lentes del panel

Un crítico por lente, en paralelo.

| Lente | Tipo | Se ejecuta en |
|---|---|---|
| L1 — Reutilización | **obligatorio** | todos los tiers |
| L4 — Gobernanza y coherencia con el spec | **obligatorio** | todos los tiers |
| L3 — Riesgos y ejecutabilidad | opcional | `medio` y `alto` |
| L2 — Simplicidad / YAGNI | opcional | `alto` |

### L1 — Reutilización (no reinventar)

- **CONSULTAR** `pce-mcp` → tool `resolve_entity` → query:
  `pol-dev-buscar-antes-de-crear` → **APLICAR**: el mandato recuperado es el
  criterio; no asumir su contenido.
- Por cada archivo "crear" del plan: ¿existe ya algo que haga eso? Buscar en el
  repo antes de aceptar la fila. Un "crear" que duplica algo existente es
  hallazgo `alta` **con la ruta del original como evidencia** — sin ruta, el
  hallazgo no se sostiene.
- La sección Reutilización cita rutas concretas (`ruta:linea`), no categorías
  ("usaremos los helpers existentes").

### L2 — Simplicidad / YAGNI

- Elementos del plan que no rastrean a ningún `CA-NN` del spec: si nada los
  pide, sobran.
- Abstracciones introducidas para un solo caso de uso.
- Configurabilidad, parámetros o puntos de extensión que el spec no pidió.
- Reescrituras de código que funciona, colgadas del cambio real.
- Contrapeso honesto: señalar **también** cuando el plan es demasiado delgado
  para cumplir un criterio (under-engineering es fallo, no virtud).

### L3 — Riesgos y ejecutabilidad

- ¿El comando de validación **realmente** demuestra los criterios de
  aceptación, o solo prueba que compila?
- Riesgos obvios ausentes: migración de datos, compatibilidad hacia atrás,
  artefactos que otros repos consumen, estado en disco que sobrevive al cambio.
- Orden de ejecución: ¿el plan deja el repo roto entre pasos?
- Fan-out: ¿los grupos paralelizables son realmente independientes? Dos grupos
  que tocan el mismo archivo producirán conflictos, no velocidad — hallazgo
  `alta`.
- Reversibilidad: si esto sale mal a mitad, ¿cómo se vuelve atrás?

### L4 — Gobernanza y coherencia con el spec

- **CONSULTAR** `pce-mcp` → tool `resolve_entity` → query: cada
  id de `governance_refs` → **APLICAR**: ¿alguna decisión de diseño contradice
  un mandato MUST?
- Cada decisión de diseño no trivial cita el ADR/policy que la respalda, o
  argumenta por qué no aplica ninguno.
- Trazabilidad: cada `CA-NN` del spec tiene algo en el plan que lo hará posible.
  Un criterio sin sustento en el plan es hallazgo `alta`.
- El plan no amplía el alcance del spec por su cuenta (si hace falta más,
  el spec se corrige; no se extiende de tapadillo).
- Si el MCP no responde: no evaluar este lente de memoria; devolver
  `sin-gobernanza` y escalar.

## Cómo puntuar la severidad

| Severidad | En esta fase significa |
|---|---|
| `alta` | Duplica algo existente, incumple gobernanza, un `CA-NN` sin sustento, o grupos paralelos con archivos compartidos |
| `media` | Sobre-ingeniería, riesgo sin mitigar, validación débil |
| `baja` | Mejora de orden o claridad del plan |

## Hallazgos que requieren decisión humana (escalan, no se refinan)

- Dos enfoques técnicos viables con trade-off de negocio (costo vs. tiempo vs.
  deuda) — el gate no elige por el humano.
- Reutilizar algo existente exigiría cambiarlo de forma que rompa a sus
  consumidores actuales.
