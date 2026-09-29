# Capa determinista de `sdd-gate` (paso 2)

Barata y primero: si algo de esto falla, no se gasta un solo agente.

En `fase: plan`/`fase: tasks` (`0117-D6`, 2026-09-20), esta capa **es todo el
gate**: si pasa, veredicto `aprobado` de inmediato, sin panel — no "no se
lanza el panel esta vez", sino que esas dos fases nunca lo lanzan.

- Todas las secciones de la plantilla están presentes y ninguna dice "TBD",
  "..." ni quedó con el texto de ejemplo de la plantilla.
- Los chequeos específicos de la fase que declara su rúbrica (sección
  "Capa determinista" de `references/rubrica-<fase>.md`).
- **Presupuesto de tamaño del artefacto** (unidad 0116, CA-01/CA-02/CA-03):
  en `fase: spec`/`plan`, correr `validate_artifact_size.py spec|plan
  <artefacto>` sobre el artefacto en evaluación; en `fase: tasks`/`codigo` no
  aplica (esos artefactos no tienen presupuesto propio — CA-01/02 solo cubren
  `spec.md`/`plan.md`). Si `bitacora.md` también forma parte de lo que se
  cierra en esta iteración, correr además `validate_artifact_size.py
  bitacora <bitacora.md>`. Mismo tratamiento que cualquier otro chequeo
  determinista de esta lista: si excede el presupuesto, **no se lanza el
  panel** — se devuelve el control a la skill de fase con el conteo real de
  líneas que excedió, para que lo recorte antes de reintentar.
- **Re-chequeo de hash de filesystem entre fases** (unit 0003, fix 2,
  CA-04/CA-05): en `fase: plan` y `fase: tasks`, calcular el `fs_hash`
  actual del filesystem raíz con el mismo algoritmo del script
  `check_governance_surface.py` (excluye `.git/`, `.spec/`, `.gitnexus/`,
  `.venv/`, `.pytest_cache/`, `.ruff_cache/`, `.tmp/`, `.codebase-memory/`;
  orden estable; sha256 agregado). Comparar con el `fs_hash` persistido al
  cerrar la fase anterior (en `_estado.yaml > gates.<fase_anterior>.fs_hash`
  o, si se prefiere, en un campo paralelo del gate). Si difiere, escalar con
  causa `superficie-cambiada` y forzar re-ejecución del chequeo de
  superficie del paso 3 (no del paso 2 entero — el resto de los chequeos
  deterministas son invariantes bajo cambios de filesystem). Si coincide,
  atajo: no invocar el script del paso 3 entero; el gate lo aprueba
  directamente. **Opt-in** para re-verificación explícita: si el orquestador
  quiere saltarse el atajo, fija `force_recheck: true` (no expuesto en CLI
  hoy; queda para una iteración posterior).

Si algún chequeo determinista falla, **no se lanza el panel**: se devuelve el
control a la skill de fase con la lista de faltantes para que los complete. Eso
no consume iteración, pero **sí se cuenta**: a la **tercera** devolución por la
misma causa, el gate emite `escalado` con causa `determinista-irresoluble`. Una
fase que no consigue satisfacer un chequeo en dos intentos no lo va a conseguir
en el décimo, y el lazo fase→gate→fase no puede quedar abierto sin dejar rastro
en disco.

**Excepción — la validación en rojo no es una omisión rellenable.** En
`fase: codigo`, si el `comando_validacion` falla, el gate no devuelve el control
para "completar" nada: emite directamente `escalado` con causa
`validacion-en-rojo`. Es un hecho sobre el código, no una sección que falte.

**Subset mínimo por archivos tocados (unidad 0121, CA-11/CA-12/CA-13).** En
`fase: codigo`, antes de ejecutar `comando_validacion`, ver
`references/test-subset-codigo.md` (cargar en este punto): cuando el mapeo de
`test_subset.py` es confiable, el comando de subset que produce reemplaza la
porción correspondiente de `comando_validacion` para ese subárbol; ante
cualquier duda de mapeo, cae de forma conservadora al `comando_validacion`
completo — nunca corre menos de lo que correría hoy.
