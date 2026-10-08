---
description: "Railspec — arrancar (o retomar) una unidad SDD guiada por el servidor"
argument-hint: "[qué quieres cambiar | NNNN-slug para retomar] [--insumo <id>] [--perfil ligero|estandar|profundo] [--modo <modo> --plan <slug>]"
---

Arranca o retoma una unidad de Railspec con las tools del servidor MCP `railspec`.

1. Si `$ARGUMENTS` es un id de unidad (`NNNN-slug`), llama `unit_status` con esa unidad y sigue
   con la skill `railspec-bucle`.
2. Si no, redacta un título corto (máx. 200 caracteres) y llama `unit_start` con `titulo`, `pedido`
   (la petición completa, literal) y, si viene `--insumo <id>`, `insumos: [<id>]`. Pasa `modo` (y
   `plan`) solo si el humano los escribió en `$ARGUMENTS`: `supervisado` y `desatendido` exigen
   `--plan`, el id de un mandato que ya existe y está aprobado (compruébalo con `mandate_get`).
   Si no lo está, no arranques: dile que una persona debe aprobarlo en la consola web (tú puedes
   redactar el borrador con `mandate_propose`, nunca aprobarlo). Pasa `perfil` solo si viene `--perfil` con `ligero`, `estandar` o `profundo`; con
   otro valor no arranques: pídele el correcto. No propongas modo, perfil ni riesgo por tu cuenta:
   sin `modo` la unidad nace `interactivo`, sin `--perfil` el servidor usa el perfil por defecto
   del workspace y el triaje es del servidor.
3. Anuncia en una línea la unidad, la rama y el worktree que devolvió `unit_start`, y sigue con la
   skill `railspec-bucle`.

Petición: $ARGUMENTS
