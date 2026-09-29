# Spec — Versionado y actualización del kit

> Fase 1 (Especificar). Define el QUÉ y el POR QUÉ. **No** describe el cómo técnico (eso va en `plan.md`, de otra unidad — ver Alcance).

## Problema / Motivación

El kit instala y verifica su carga contra un destino, pero no tiene ningún
concepto de **versión propia**, ninguna forma de distinguir una edición
local intencional del destino frente a un destino que simplemente quedó
desactualizado, y ningún mecanismo que retire de un destino una ruta que
una versión anterior del kit instaló ahí y una versión posterior ya no
declara. `research.md` confirma las tres ausencias con evidencia directa
del código actual (H1, H3, H6): no existe ni siquiera parcialmente ninguna
de las tres.

Si esto no se resuelve: cada destino que instale el kit y luego lo
actualice corre el riesgo de (a) no saber contra qué versión del kit está,
(b) recibir el mismo tratamiento de "conflicto" tanto si editó algo a
propósito como si simplemente no ha corrido el instalador en un tiempo, y
(c) acumular rutas huérfanas que ninguna versión futura del kit vuelve a
tocar ni a limpiar.

## Resultado esperado

El problema de versionado y actualización del kit queda **documentado con
evidencia y sembrado en el backlog propio del kit**, de forma que una
unidad futura pueda tomarlo sin volver a investigar: las tres ausencias
están confirmadas contra el código actual, nombradas como alcance de esa
unidad futura, y el motor del kit sigue intacto — esta unidad no adelanta
ninguna decisión de diseño.

**Resultado esperado de la unidad de diseño futura** (fuera de alcance
aquí, y por tanto fuera de los `CA-NN` de abajo): una estrategia de versión
y actualización diseñada y documentada que resuelva, para cualquier
destino, cómo se identifica "la versión del kit" que tiene instalada; cómo
se distingue, para una ruta divergente, "el destino la editó a propósito"
de "el destino está desactualizado"; y qué pasa con una ruta que una
versión anterior instaló y la versión corriente ya no declara.

## Alcance

**Incluye:**
- Sembrar esta unidad en el backlog propio del kit (`.spec/units/`),
  gestionada en modo bootstrap: el kit edita directamente su propia carga
  para producir este `research.md`/`spec.md`, sin instalarse sobre sí
  mismo — no hay instalador de por medio para el propio backlog del kit.
- Nombrar explícitamente, como alcance de la unidad de **diseño** que
  continúe este trabajo (no de ésta), las tres capacidades: versión del
  kit, digests por ruta para distinguir edición local de desactualización,
  y mecanismo de retiro de rutas huérfanas.

**No incluye (fuera de alcance):**
- Diseñar el esquema concreto de versión (semver, fecha, hash de árbol) —
  decisión humana, de la unidad que implemente esto.
- Implementar el cálculo o almacenamiento de digests por ruta.
- Implementar cualquier mecanismo de retiro de rutas huérfanas.
- Cualquier cambio a `installer/installer.py`, `installer/verifier.py` o al
  formato de `installer/kit_manifest.yaml` — esta unidad no toca código del
  motor, solo deja el problema documentado y sembrado.

## Criterios de aceptación

- [x] CA-01 — `research.md` documenta las tres capacidades ausentes, y cada
      una tiene al menos un hallazgo cuya evidencia es un comando o una
      `ruta:línea` reproducible: versión del kit → H1; digests por ruta →
      H2 y H3; retiro de rutas huérfanas → H6.
- [x] CA-02 — `spec.md` § Alcance (bloque "Incluye") nombra las mismas tres
      capacidades como alcance de la unidad de diseño futura.
- [x] CA-03 — Las tres capacidades siguen ausentes del motor al cerrar esta
      unidad, comprobable sin interpretar: `grep -n "^version\|digest\|sha256\|hash" installer/kit_manifest.yaml`
      no devuelve nada, y `grep -n "unlink\|rmtree\|os.remove\|\.remove(" installer/installer.py`
      tampoco.
- [x] CA-04 — `_estado.yaml` de esta unidad contiene la clave
      `bootstrap: true`.

## Governance aplicable

Este repositorio **no tiene superficie de gobernanza propia que consultar**.
Hechos comprobables: no existe `AGENTS.md` en el repo; no existe `.mcp.json`;
y `pce-mcp` no está configurado como dependencia del kit sobre sí mismo —
aparece únicamente como *carga que el kit instala en un destino*
(plantillas, skills y scripts que el destino usará contra su propio
`pce-mcp`). No hay, por tanto, ningún artefacto de gobernanza recuperable
contra el cual evaluar este spec.

Esto **no** es el caso que cubre la regla "sin MCP no hay gobernanza: se
para y se dice, no se degrada a criterio propio" (`.spec/README.md` §
Governance). Esa regla gobierna a los **destinos**, donde `pce-mcp` sí es la
superficie de gobernanza y quedar sin él significa perder acceso a
artefactos que existen y aplican. Aquí no hay superficie que se haya caído:
no hay ninguna. Son condiciones distintas y el centinela
`governance_refs: [ninguna-aplicable]` registra la segunda de forma
explícita, en vez de dejar la lista vacía por omisión.

| Tipo | Id | Cómo aplica / restringe |
|---|---|---|
| — | ninguna-aplicable | Sin superficie de gobernanza propia del kit (sin `AGENTS.md`, sin `.mcp.json`, sin `pce-mcp` propio); ver justificación arriba |

## Preguntas abiertas

- La **unidad de diseño futura sí debe reabrir el lente de gobernanza**: al
  decidir el esquema de versión y el almacenamiento de digests estará
  fijando una fuente de verdad, y eso puede caer bajo artefactos que el kit
  cita en su prosa normativa (por ejemplo `pri-gob-fuente-verdad-unica`).
  Esta unidad no diseña nada, así que hoy no hay incumplimiento posible —
  pero la señal no debe perderse al cerrarla.
