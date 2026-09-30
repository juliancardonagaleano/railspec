---
description: "Railspec — arrancar (o retomar) una unidad SDD guiada por el servidor"
argument-hint: "[qué quieres cambiar | NNNN-slug para retomar] [--insumo <id>] [--modo <modo> --plan <slug>]"
---

Arranca o retoma una unidad de Railspec con las tools del servidor MCP `railspec`.

1. Si `$ARGUMENTS` es un id de unidad (`NNNN-slug`), llama `unit_status` con esa unidad y sigue
   con la skill `railspec-bucle`.
2. Si no, redacta un título corto (máx. 200 caracteres) y llama `unit_start` con `titulo`, `pedido`
   (la petición completa, literal) y, si viene `--insumo <id>`, `insumos: [<id>]`. Pasa `modo` (y
   `plan`) solo si el humano los escribió en `$ARGUMENTS`: `supervisado` y `desatendido` exigen
   `--plan`. No propongas modo, perfil ni riesgo por tu cuenta: sin `modo` la unidad nace
   `interactivo` y el triaje es del servidor.
3. Anuncia en una línea la unidad, la rama y el worktree que devolvió `unit_start`, y sigue con la
   skill `railspec-bucle`.

Petición: $ARGUMENTS
