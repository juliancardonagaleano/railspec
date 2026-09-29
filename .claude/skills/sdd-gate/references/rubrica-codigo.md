# Rúbrica del gate — fase `codigo`

> Estructura de evaluación del **diff de implementación**, antes de cerrar la
> unidad. Es el gate post-implementación: corre **sin intervención humana**
> (el humano ya aprobó en su checkpoint) y puede impedir el cierre.
>
> **No contiene reglas de gobernanza ni de estilo del repo**: las recupera del
> MCP (`pol-ia-no-embeber-conocimiento`).

## Quién lo ejecuta

Los críticos de este gate son agentes de **contexto fresco**: no participaron
en la implementación y no han visto la conversación que la produjo. Leen
`spec.md`, `tasks.md` y el diff. Esto es deliberado — el implementador no
certifica su propio trabajo.

## Capa determinista (antes del panel)

- [ ] Todas las tareas de `tasks.md` están `[x]`, o las pendientes están
      justificadas en "Notas de implementación".
- [ ] El `comando_validacion` de `_estado.yaml` se ejecutó y **pasó**. Si falla,
      el gate termina aquí con veredicto `escalado` y causa `validacion-en-rojo`,
      sin lanzar panel. Es la excepción declarada en el paso 2 de `SKILL.md`:
      los demás fallos deterministas devuelven el control a la fase sin
      veredicto, este no (no es una omisión rellenable, es un hecho del código).
- [ ] El diff no contiene restos de depuración, credenciales, rutas absolutas
      de la máquina del autor, ni archivos generados que deberían ignorarse.
- [ ] El diff no toca archivos fuera de lo que el plan declaró, salvo que las
      "Notas de implementación" lo expliquen.
- [ ] Para todo hallazgo `alta` en el diff, existe línea `causa_raíz:` con
      formato `<file:line>` junto a la corrección propuesta; si falta, el gate
      termina con veredicto `escalado` antes de lanzar el panel.

## Lentes del panel

Un crítico por lente, en paralelo.

| Lente | Tipo | Se ejecuta en |
|---|---|---|
| L1 — Cumplimiento de los `CA-NN` | **obligatorio** | todos los tiers |
| L2 — Correctitud | **obligatorio** | todos los tiers |
| L3 — Encaje con el repo y gobernanza | **obligatorio** | todos los tiers |

> En el gate de código los tres lentes son obligatorios: un gate de código que
> no mira correctitud ni gobernanza no es un gate. Aquí el tier solo gradúa las
> iteraciones y si hay verificación adversarial (`alto`).

### L1 — Cumplimiento de los criterios de aceptación

El lente obligatorio. Recorrer `CA-NN` uno por uno:

- Localizar en el diff **la evidencia concreta** que lo cumple (archivo y
  línea). Un criterio que se "considera cumplido" sin evidencia localizable es
  hallazgo `alta`.
- Distinguir *cumplido* de *aparentemente cumplido*: código escrito ≠
  comportamiento verificado. Si el criterio habla de comportamiento, la
  evidencia es una prueba o una ejecución, no la existencia del código.
- Criterios marcados `[x]` en el spec sin respaldo en el diff: hallazgo `alta`.

### L2 — Correctitud

- Errores lógicos: condiciones invertidas, off-by-one, casos vacío/nulo,
  early-return que se salta trabajo necesario.
- Estado inconsistente si el proceso muere a mitad.
- Rutas de error silenciadas (excepciones tragadas, fallos que no se propagan).
- Compatibilidad hacia atrás: consumidores existentes del artefacto tocado.
- Cada hallazgo debe venir con un **escenario concreto de fallo** (entradas →
  resultado incorrecto). Sin escenario, el hallazgo no se sostiene y se
  descarta.

### L3 — Encaje con el repo y gobernanza

- **CONSULTAR** `pce-mcp` → tool `resolve_entity` → query: cada
  id de `governance_refs` → **APLICAR**: ¿el código cumple los mandatos MUST
  que le aplican?
- **APLICAR** el baseline indexado en `AGENTS.md` aunque no esté en
  `governance_refs`; descartar todo artefacto del dominio `ia`, rechazado por
  mandato de ese archivo.
- **CONSULTAR** `pce-mcp` → tool `search_catalog` acotado por `domain`/`query`
  al tipo de archivo y componente tocados, con `type` en `policy` y `adr` →
  **APLICAR**: artefactos específicos de ese objeto que el plan no consideró.
- Duplicación introducida: código nuevo que repite algo ya existente en el repo
  (citar la ruta del original).
- Coherencia con el estilo del código circundante (nombres, estructura,
  densidad de comentarios) — el diff no debe leerse como escrito por un
  extraño.
- Si el MCP no responde: no evaluar la gobernanza de memoria; `sin-gobernanza`
  y escalar.
  Eso vale cuando la superficie existe y no se alcanza; un repositorio
  **sin superficie de gobernanza** es el caso distinto que define
  `SKILL.md` § 3 ("Superficie caída ≠ superficie inexistente") — ahí el
  lente corre igual, para confirmar con evidencia del repo que no hay
  artefacto aplicable.

## Cómo puntuar la severidad

| Severidad | En esta fase significa |
|---|---|
| `alta` | Un `CA-NN` sin evidencia, un bug con escenario de fallo, o incumplimiento de gobernanza |
| `media` | Duplicación, manejo de error débil, deriva de estilo notable |
| `baja` | Detalle cosmético |

## Resultado

- Sin hallazgos `alta` ni `media` → el gate aprueba y `sdd-implementar` puede
  marcar `fase: done`.
- Con hallazgos `media` → se corrigen; si queda presupuesto de iteraciones se
  re-evalúa, y si no, el veredicto es `refinado` (ver la regla de convergencia
  del paso 7 de `SKILL.md`: resolver todo y agotar el tope no es escalar).
- Con hallazgos `alta` que sobreviven a la verificación adversarial y no se
  pueden corregir dentro del alcance → veredicto `escalado`: **la unidad no se
  cierra**. Queda en `estado: bloqueado` con los hallazgos en `_estado.yaml` y
  el detalle en bitácora.
