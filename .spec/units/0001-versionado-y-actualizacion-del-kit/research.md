# Investigación — Versionado y actualización del kit

> Fase 0. El kit hoy instala y verifica su carga contra destinos, pero no
> tiene ningún mecanismo de versión, de comparación por digest, ni de
> retiro de rutas huérfanas. Esta investigación reúne la evidencia de esa
> ausencia antes de que el spec formule qué se necesita construir.

## Preguntas que motivaron la investigación

1. ¿El kit declara una versión propia en algún lugar, más allá de menciones
   sueltas en prosa?
2. ¿El instalador puede distinguir "el destino editó esta ruta a propósito"
   de "el destino simplemente quedó desactualizado frente al kit"?
3. ¿Qué pasa hoy en un destino cuando una versión nueva del kit deja de
   declarar una ruta que una versión anterior sí instaló ahí?

## Hallazgos

| # | Hallazgo | Evidencia | Responde a |
|---|---|---|---|
| H1 | `.spec/README.md` menciona "Claude-first (desde v2.0.0)" en prosa, pero ningún archivo del repositorio declara esa versión como dato — no hay `VERSION`, no hay campo `version` en `kit_manifest.yaml`, no hay ninguna constante `__version__` en `installer/*.py` | `grep -rn "version\|VERSION" installer/*.py` → vacío; `.spec/README.md:10` | pregunta 1 |
| H2 | `kit_manifest.yaml` tiene un solo `updated_at` global (una marca de tiempo por archivo completo, no por entrada) — no hay un digest ni una marca por-ruta que permita saber cuándo cambió cada archivo del manifiesto individualmente | `installer/kit_manifest.yaml:17` (`updated_at: "..."`); ninguna entrada de `entries:` lleva su propio campo de fecha o hash | pregunta 1, 2 |
| H3 | El registro de instalación (`installer.py:_write_install_record`) solo persiste `installed_at` — ni versión del kit instalada, ni digest por ruta, ni lista de qué rutas se escribieron esa vez | `installer/installer.py:113-122` (`INSTALL_RECORD_NAME = "sdd-kit-install-record.yaml"`, cuerpo con un solo campo `installed_at`) | pregunta 1, 2, 3 |
| H4 | La clasificación de conflicto de `installer.py` (`_classify_conflicts`) compara **contenido byte a byte** entre el destino y la fuente — cualquier diferencia es "conflicto", sin importar si la causa es una edición local intencional del destino o simplemente que el destino quedó una versión atrás del kit. Ambos casos producen el mismo aviso y exigen el mismo `--force` | `installer/installer.py:54-65` (`_classify_conflicts`); su propio docstring: "regardless of whether a previous-install record is present" | pregunta 2 |
| H5 | `verifier.py` (`find_divergent`) tiene la misma limitación: reporta toda ruta cuyo contenido difiera del origen, sin distinguir edición local de desactualización | `installer/verifier.py:45-56` | pregunta 2 |
| H6 | No existe ningún mecanismo que borre del destino una ruta que el manifiesto dejó de declarar: `run_install` solo **escribe** las rutas que el manifiesto de la versión corriente declara (`_write_payload`); una ruta que una versión anterior instaló y la versión corriente ya no declara queda huérfana en el destino, sin que nada la señale ni la retire | `installer/installer.py:68-72` (`_write_payload` itera `pairs`, que vienen solo de `iter_payload_files(entries)` — nunca del contenido ya existente en el destino) | pregunta 3 |

## Lo que se creía y resultó falso

- **Se creía:** la mención "Claude-first (desde v2.0.0)" en `README.md`
  implica que el kit ya tiene un esquema de versión funcionando. —
  **Realidad:** es prosa descriptiva sin ningún mecanismo detrás (H1); no
  hay dónde leer "la versión actual del kit" de forma programática.

## Incógnitas que siguen abiertas

- Qué forma toma "una versión del kit" (semver, fecha, hash del árbol
  completo) — *requiere:* decisión de diseño humana, fuera del alcance de
  esta unidad de fundación (solo se siembra el problema, no se resuelve).
- Si el digest por ruta se guarda en el registro de instalación del
  destino, en el manifiesto del kit, o en ambos — *requiere:* diseño de la
  unidad que implemente esto.
- Qué política de retiro de huérfanas es segura por defecto (borrar
  siempre, preguntar, o listar sin tocar) — *requiere:* decisión humana,
  dado el riesgo de borrar una edición local del destino que nunca fue
  parte del kit.

## Qué implica para el spec

- El spec de esta unidad de fundación **no implementa** ninguna de las tres
  capacidades — las nombra explícitamente como alcance futuro y cierra sin
  código, dejando esta investigación y sus hallazgos como punto de partida
  verificado para la unidad que sí las construya.
- Los tres hallazgos H1/H3/H6 son evidencia suficiente de que ninguna de
  las tres capacidades existe hoy, ni parcialmente — no hay que descartar
  alcance por trabajo ya hecho.
