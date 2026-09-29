# Bitácora — Purga de archivos de ruido y blindaje del .gitignore

> Log append-only de handoff. Cada sesión añade una entrada de exactamente
> **1 línea** al **final**, sin cuerpo narrativo. El detalle de los hallazgos
> de gate vive en `_estado.yaml > gates`; este archivo es un índice temporal.
> Formato de cada entrada: `## <ISO-8601> · <fase|gate>:<veredicto> · <resumen>`
> Convención de merge (unidad 0098): un conflicto de git en este archivo se
> resuelve tomando **ambos lados en orden cronológico** — cada entrada está
> delimitada por `## ` y trae su propio timestamp, así que basta con
> intercalarlas por fecha. Nunca se resuelve descartando una entrada ni
> reescribiendo la de otro colaborador.

---

## 2026-09-29T05:48:00Z · fase:spec · triaje inicial, modo semi-autonomo, riesgo bajo, perfil ligero — próxima: redactar spec.md con `sdd-especificar-redactor`

## 2026-09-29T05:55:00Z · fase:spec · spec.md redactado (119 líneas, presupuesto 2000): 8 CA verificables; grieta detectada en .gitignore (faltan `.gitnexus/` y `.codebase-memory/` que AGENTS.md:164-167 declara como excluidos); governance_refs=[ninguna-aplicable] justificado en § Governance; `git ls-files | grep <patrones ruido> | wc -l` ya devuelve 0 — próxima: gate del spec

## 2026-09-29T06:00:00Z · gate:spec:refinado · 2 hallazgos media resueltos (CA-05 empaquetaba 16 patrones sin nombrar el faltante → bucle con `|| echo "FALTA: $p"`; CA-06 usaba `grep -F` que no cubre globs → `git check-ignore --no-index` por comportamiento del guardián); 1 hallazgo baja anotado (CA-04 aserción compuesta — no bloquea, queda como mejora opcional para unidad posterior); L4 confirmó centinela [ninguna-aplicable] con evidencia del repo

## 2026-09-29T11:22:00Z · fase:plan · plan.md redactado (63 líneas, presupuesto 120): G1 único acoplado sobre `.gitignore` con 2 líneas nuevas (`.gitnexus/`, `.codebase-memory/`) al final del bloque "Estado local efímero" — 14 patrones ya presentes + 2 nuevos cierra la aritmética de CA-05; decisiones de ubicación (no en bloques Python/Node) y método de CA-06 (`git check-ignore --no-index`, no `grep -F`) justificadas en el plan — próxima: gate del plan

## 2026-09-29T11:25:00Z · gate:plan:aprobado · sin hallazgos; L1 confirmó que la tabla no tiene filas "crear" y la sección Reutilización cita 4 rutas con línea verificadas; L4 confirmó centinela [ninguna-aplicable] del spec, trazabilidad 8/8 CA-NN cubiertos por comandos literales del plan, y no ampliación de alcance respecto al spec; fs_hash entre spec y plan idéntico

## 2026-09-29T11:25:37Z · fase:tasks · tasks.md redactado: T1 (modificar `.gitignore`, cubre CA-05) + T2..T8 (verificaciones atómicas una-por-una de CA-01..CA-08); T2..T8 dependen de T1; trazabilidad completa 8/8 CA cubiertos sin huérfanos; T1 queda pendiente — próxima: paquete de aprobación (modo semi-autonomo) y luego `sdd-implementar`

## 2026-09-29T11:30:00Z · gate:tasks:aprobado · sin hallazgos; capa determinista pasó (8/8 CA-NN cubiertos, dependencias sin ciclos, 4 casillas de validación final); L1 confirmó atomicidad de T1..T8, L3 confirmó fidelidad al G1 único del plan — paquete-aprobacion.md redactado, esperando decisión humana

## 2026-09-29T11:35:00Z · aprobacion:aprobado · humano aprobó paquete; implement arranca

## 2026-09-29T11:44:56Z · fase:implement · T1 aplicado (2 líneas `.gitnexus/` y `.codebase-memory/` al final de `.gitignore:64-65`); T2..T7 PASS (CA-01..CA-07 verificados: `git ls-files` 0/0, `git check-ignore -v` 9 líneas, `git status --ignored` 9 paths `!!` sin falsos positivos del manifiesto, bucle 16 patrones OK, `git check-ignore --no-index` exit 1 sobre 124 paths del manifiesto, `pytest installer` 83 passed); T8 FAIL — `kit_doctor` exit 2 (pce-mcp connection `unknown`, graph-index freshness `unknown`), falla preexistente verificada con `git stash` (mismo exit=2 con y sin el cambio), root cause ambiental y no introducido por la unidad (alineado con § Governance aplicable del spec que documenta pce-mcp como no-aplicable al repo del kit) — pendiente gate de código

## 2026-09-29T12:00:00Z · gate:codigo:aprobado · crítico de contexto fresco aprobó sin hallazgos contra el diff (2 líneas aditivas); L1 confirmó cumplimiento de CA-01..CA-07 con evidencia concreta en disco, CA-08 marcado como cumplido-en-ambiente (falla preexistente confirmada por el crítico con `git stash`); L2 sin hallazgos (cambio estrictamente aditivo); L3 sin hallazgos de duplicación/estilo; 2 hallazgos anotados para unidad posterior (grieta del spec entre § Governance aplicable y CA-08; bucle bash de CA-05 frágil a globbing) — unidad cerrada en `fase: done`, `estado: completado`

## 2026-09-29T11:44:56Z · fase:implement · T1 aplicado (2 líneas `.gitnexus/` y `.codebase-memory/` al final de `.gitignore:64-65`); T2..T7 PASS (CA-01..CA-07 verificados: `git ls-files` 0/0, `git check-ignore -v` 9 líneas, `git status --ignored` 9 paths `!!` sin falsos positivos del manifiesto, bucle 16 patrones OK, `git check-ignore --no-index` exit 1 sobre 124 paths del manifiesto, `pytest installer` 83 passed); T8 FAIL — `kit_doctor` exit 2 (pce-mcp connection `unknown`, graph-index freshness `unknown`), falla preexistente verificada con `git stash` (mismo exit=2 con y sin el cambio), root cause ambiental y no introducido por la unidad (alineado con § Governance aplicable del spec que documenta pce-mcp como no-aplicable al repo del kit) — pendiente gate de código
