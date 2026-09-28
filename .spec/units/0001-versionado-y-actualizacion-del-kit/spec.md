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

El kit tiene una estrategia de versión y actualización diseñada y
documentada — no necesariamente implementada por esta misma unidad (ver
Alcance) — que resuelve, para cualquier destino:

- Cómo se identifica "la versión del kit" que un destino tiene instalada.
- Cómo se distingue, para una ruta divergente, "el destino la editó a
  propósito" de "el destino está desactualizado frente al kit".
- Qué pasa con una ruta que una versión anterior instaló y la versión
  corriente ya no declara.

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
  motor, solo dejа el problema documentado y sembrado.

## Criterios de aceptación

- [x] CA-01 — `research.md` de esta unidad nombra explícitamente las tres
      capacidades (versión del kit, digests por ruta, retiro de rutas
      huérfanas) con evidencia verificable de que ninguna existe hoy.
- [x] CA-02 — Este `spec.md` nombra las mismas tres capacidades como
      Alcance de la unidad de diseño futura, sin implementarlas.
- [x] CA-03 — `_estado.yaml` de esta unidad declara que se gestiona en
      modo bootstrap (edición directa de la carga del kit, sin pasar por
      su propio instalador).

## Governance aplicable

Este repositorio no tiene acceso a un MCP de gobernanza propio (`pce-mcp`
es infraestructura de un consumidor concreto del kit, no del kit mismo) —
consistente con el mandato de origen de este protocolo de tratar "sin MCP
no hay gobernanza: se para y se dice, no se degrada a criterio propio"
(`.spec/README.md` § Governance). Sin gobernanza que consultar, ninguna
entidad aplica.

| Tipo | Id | Cómo aplica / restringe |
|---|---|---|
| — | ninguna-aplicable | Sin MCP de gobernanza propio del kit; ver justificación arriba |

## Preguntas abiertas

Ninguna — esta unidad no diseña la solución, solo documenta el problema y
lo siembra en el backlog para que una unidad futura lo tome.
