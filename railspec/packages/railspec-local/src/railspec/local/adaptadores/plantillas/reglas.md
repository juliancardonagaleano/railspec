## Railspec: reglas de conducta

Este repositorio trabaja con Railspec (servidor MCP `railspec`). Cuando haya una unidad en curso:

- El servidor decide fase, gate y presupuesto. No decidas fases, no te saltes gates y no
  reinterpretes una orden: ejecútala o repórtala como `bloqueado` con el motivo.
- Un checkpoint lo resuelve un humano. Nunca llames `unit_approve` con una decisión que el humano
  no haya dado explícitamente en esta conversación.
- El modo de la unidad lo fija y lo cambia un humano. No pases `modo` a `unit_start` ni llames
  `unit_set_mode` si el humano no lo pidió.
- Un mandato (`supervisado`/`desatendido`) lo aprueba solo una persona, en la consola web: puedes
  redactarlo (`mandate_propose`), nunca aprobarlo ni renovarlo. Ante `mandato-parado` o una parada con
  causa, detente y díselo al humano. No decidas nada `reservado` ni que ninguna delegación del mandato
  cubra: repórtalo como `bloqueado`.
- Trabaja solo en el worktree de la unidad y solo en los archivos que la orden permite.
- Si un hook de Railspec rechaza una escritura, no lo esquives (tampoco con la shell): ajusta el
  trabajo a la orden o repórtala como `bloqueado`. Apagar la guardia (`RAILSPEC_GUARDIA=0`) solo lo
  decide el humano.
- No copies código, diffs ni secretos en reportes, mensajes o tools de Railspec: el proxy decide
  qué sale del clon según el nivel del repositorio.
- Para arrancar usa `/railspec`; para continuar, la skill `railspec-bucle`.
