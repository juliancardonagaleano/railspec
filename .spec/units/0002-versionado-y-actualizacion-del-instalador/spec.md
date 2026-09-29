# Spec — Versionado y actualización del instalador

> Fase 1 (Especificar). Define el QUÉ y el POR QUÉ. **No** describe el cómo técnico (eso va en `plan.md`).

## Problema / Motivación

Esta unidad es la sucesora de `0001-versionado-y-actualizacion-del-kit`, que
documentó el problema y lo sembró sin diseñar nada. Su evidencia
(`.spec/units/0001-versionado-y-actualizacion-del-kit/research.md`, H1..H6) sigue
vigente contra el código actual y se reusa aquí sin repetir la investigación.
El instalador (`installer/`) hoy carece de tres capacidades:

1. **Versión propia del kit.** Ningún archivo declara la versión del kit como
   dato: no hay `VERSION`, ni campo `version` en `installer/kit_manifest.yaml`,
   ni `__version__` en `installer/*.py` (H1). Un destino no puede saber
   programáticamente contra qué versión del kit está. El registro de instalación
   (`installer/installer.py:113-122`, `_write_install_record`) persiste solo
   `installed_at` (H3).
2. **Distinguir desactualización de drift.** `_classify_conflicts`
   (`installer/installer.py:54-65`, H4) y `find_divergent`
   (`installer/verifier.py:45-56`, H5) comparan contenido byte a byte contra la
   fuente: un destino que quedó una versión atrás y un destino donde alguien
   editó a mano un archivo del kit producen el mismo "conflicto" y exigen el
   mismo `--force`. El manifiesto solo tiene un `updated_at` global, sin marca ni
   digest por entrada (H2). Hoy el operador no puede saber cuál de los dos casos
   tiene delante, y los dos se resuelven con el mismo martillo.

   **Premisa de diseño que gobierna esta capacidad** (condición fijada por el
   dueño del kit, 2026-09-28): *en un destino nadie edita a mano una ruta que el
   kit instaló; toda modificación es un reempaquetamiento — se cambia la fuente
   canónica en el repo del kit, se publica una versión nueva y el destino la
   recibe instalándola*. Por eso una edición local **no es trabajo que haya que
   proteger: es drift, y el drift es el defecto**. La clasificación no existe
   para decidir si se pisa (se pisa siempre: ver Resultado esperado), sino para
   **decir cuál de las dos divergencias es**: "está en una versión vieja"
   (inocuo, se corrige instalando) o "alguien lo editó a mano" (violación de la
   regla, hay que investigarlo y llevar el cambio a la fuente canónica).
3. **Retiro de rutas huérfanas.** `_write_payload`
   (`installer/installer.py:68-72`, H6) solo escribe lo que el manifiesto
   corriente declara; una ruta que una versión anterior instaló y la corriente ya
   no declara queda para siempre en el destino, sin que nada la señale ni la
   retire. Como el destino además recibe espejos generados desde esa carga
   (`.claude/skills|agents|commands`), una skill o agente retirado del kit sigue
   estando disponible en el destino.

Hecho adicional verificable: `installer/manifest.py:27-32` ya define
`sha256_bytes` y `sha256_file`, y ningún otro módulo de `installer/` ni de
`installer/tests/` los usa: **en el flujo de instalación y verificación no hay
hoy ningún cálculo de digest en uso**. Sí lo hay fuera de `installer/`: los tres
`scripts/materialize_claude_*.py` calculan un `sha256` por archivo y lo guardan
en sus manifiestos de espejo. Es antecedente de reutilización, no un conflicto:
`plan.md` debe decidir si el digest de la línea base reusa esos helpers.

Si no se hace: el operador nunca puede distinguir un destino desactualizado de
uno con drift, así que el drift no se detecta ni se corrige; el destino acumula
carga retirada que nadie vuelve a tocar; y los tres guards que el kit ya envía
(`guard_bash_spec_writes.py`, `guard_generated_paths.py`,
`guard_written_state_shape.py`, manifiesto líneas 47-52) siguen llegando
**inertes**, porque ninguna carga del kit los cablea y sin cableado un hook no se
ejecuta nunca.

## Resultado esperado

Para cualquier destino instalado por una versión del kit que ya incluya esta
unidad:

- El kit tiene **una versión declarada en un único lugar**, legible por código, y
  el destino registra con qué versión fue instalado por última vez.
- El instalador guarda en el destino qué contenido escribió por ruta (la **línea
  base de instalación**) y con ella clasifica cada ruta divergente en una de tres
  categorías, definidas **siempre contra la línea base y nunca contra la fuente**
  (el hook que corre en el destino no tiene la fuente a mano: `installer/` no se
  instala, así que la línea base es lo único que ambos lados pueden comparar):
  **desactualizada** (el contenido del destino coincide con su entrada en la línea
  base; solo el kit cambió), **drift** (el contenido del destino difiere de su
  entrada en la línea base, sin importar si coincide o no con la fuente) y
  **ausente** (la ruta que la línea base registra ya no existe en el destino).

  > *Nota de terminología:* el repo ya usa "drift" para dos cosas distintas —el
  > desfase entre `.agents/` y su espejo `.claude/` que detectan los
  > materializadores, y el de `validate_protocol_drift.py`. Aquí **drift** designa
  > siempre el desfase entre el destino y su línea base de instalación. Cuando
  > haga falta distinguirlo, se dice "drift de instalación".
  Las tres se corrigen **sin `--force`**: el destino converge siempre al contenido
  del kit, porque una ruta que la línea base registra no es un lugar donde el
  destino tenga trabajo propio que perder. La diferencia está en el reporte: las
  de drift se nombran una por una en la salida, como violaciones de la regla de
  reempaquetamiento, para que alguien las lleve a la fuente canónica. `verificar`
  reporta las tres categorías por separado.

- **Excepción, y única causa de aborto que queda:** si el manifiesto declara una
  ruta que la línea base **no** registra (una ruta nueva de esta versión del kit)
  y el destino ya tiene un archivo ahí, `--install` **aborta sin escribir nada** y
  la nombra. Ese archivo sí es trabajo propio del destino —el kit nunca lo
  instaló— y la regla "nunca toques lo que el kit no instaló" pesa más que la
  convergencia. Es el caso que resuelve el choque entre ambas.

- **Contrato nuevo de `--force` y del código 3.** Con lo anterior, `--force` deja
  de significar "sobrescribí conflictos" (ya no hay conflictos por divergencia) y
  pasa a significar exactamente una cosa: **autoriza borrar una huérfana con
  drift**. El código 3 conserva un único productor: la colisión de ruta nueva de
  la viñeta anterior. El texto de `installer/cli.py` y los docstrings de
  `installer/installer.py` que describen el contrato viejo se corrigen (CA-30).
- Una ruta que la línea base registra como instalada y el manifiesto corriente ya
  no declara se detecta como **huérfana** y se trata con garantías explícitas
  (ver Alcance y CA-17..CA-28): nunca se toca lo que el kit no instaló, y una
  huérfana con drift no se borra en silencio — se retiene y se reporta, y solo
  `--force` la retira.
- Un destino instalado antes de esta unidad (registro con solo `installed_at`, o
  sin registro) **sigue funcionando**: su carga se sobrescribe con el contenido
  del kit, cada divergencia se nombra como "divergencia sin clasificar" —sin
  línea base no se puede decir si era desactualización o drift— y no se retira
   ninguna huérfana. Migra al formato nuevo en su primera instalación exitosa.

### Configuration contract

El kit gobierna estas rutas raíz de configuración (cubre CA-51..CA-55;
verificado por `.spec/scripts/check_governance_surface.py` creado por
unit 0003):

```
configuration_contract: [".mcp.json", "opencode.jsonc", "AGENTS.md", "scripts/mcp-pce.sh"]
```

Cuatro entradas, todas archivos que el kit **produce** y entrega al destino
como carga. Tres van con fusión con marcador (`.mcp.json` key `mcpServers`,
`opencode.jsonc` key `mcp`, ambos con discriminador `_sdd_kit: true`); una
es prosa que viaja con overwrite (AGENTS.md). El script detecta cualquier
otro archivo que declare MCP/gob y no esté en esta lista como
`superficie-no-declarada` (abort) — coherente con el incidente del
2026-09-29 (CA-55 explicita la cobertura doble).

### Hechos de compatibilidad que el spec fija

- `installer/kit_manifest.yaml` y `installer/` **no se instalan en el destino**
  (ninguna entrada del manifiesto los nombra): el destino nunca contiene un
  manifiesto "viejo". La retrocompatibilidad relevante es la del **registro de
  instalación** que el destino ya tiene (solo `installed_at`, bajo su directorio
  git, fuera de la carga), no la del manifiesto.
- Un destino legado no tiene línea base, así que **no se puede saber qué instaló
  el kit en él** ni, por tanto, clasificar sus divergencias. Consecuencias
  aceptadas de forma explícita: (a) en su primera corrida toda ruta de la carga
  se sobrescribe con el contenido del kit y la salida las nombra como
  "divergencia sin clasificar", porque sin línea base no se puede decir si era
  desactualización o drift; (b) no se detecta ni retira ninguna huérfana de
  versiones anteriores a esta unidad — lo que una versión pre-línea-base instaló
  y luego dejó de declarar queda en el destino y **no** se limpia
  automáticamente. Es el costo de no adivinar qué borrar. La asimetría es
  deliberada: **sobrescribir** una ruta que el kit declara es siempre seguro bajo
  la premisa de reempaquetamiento; **borrar** una ruta que el kit ya no declara
  no lo es, porque sin línea base no hay forma de saber si la puso el kit.
- **Decidido (resuelve P-4):** la línea base vive bajo el directorio git del
  destino, junto al registro actual (`installer.py:113-122`), fuera de la carga y
  fuera del worktree. Es por clon: un clon nuevo del mismo destino nace sin línea
  base y se comporta como legado hasta su primera instalación. Se acepta esa
  limitación a cambio de no versionar un archivo de estado en el worktree del
  destino (CA-13). Lo que **no** se adopta es un arranque en frío conservador
  ("sin línea base no piso nada"): bajo la premisa de reempaquetamiento eso
  congelaría el drift de un clon nuevo en vez de corregirlo.

## Alcance

**Incluye:**
- Una versión del kit declarada como dato en una única fuente del repo, expuesta
  por la CLI y persistida en el registro de instalación del destino.
- Una línea base por ruta en el registro de instalación del destino, escrita solo
  al cierre exitoso de una instalación.
- Clasificación de rutas divergentes en desactualizada / drift en `--install` y
  en `verificar`, con el comportamiento de sobrescritura y reporte que describe
  el Resultado esperado.
- **Colisión de ruta nueva**

- [ ] CA-40 — El manifiesto declara una ruta que la línea base **no** registra y
      el destino ya tiene un archivo ahí: `--install` termina con código 3, nombra
      la ruta en stdout, y **no escribe ninguna ruta de la carga** (todo-o-nada).
      Es la única causa de código 3 que queda tras el contrato nuevo, y `--force`
      **no** la autoriza: `--force` solo autoriza borrar huérfanas con drift
      (CA-22). Un test comprueba además que con `--force` el resultado es el mismo
      código 3.

**Protocolo antidrift en el destino** (CA-33..CA-36, CA-40, CA-45..CA-47):
  extender el hook `pre-push` que el instalador ya instala para que rechace un
  push que deja rutas de la carga en drift, y **cablear en el destino los guards
  que el kit ya envía y que hoy llegan inertes**. Detectar el drift sin nada que
  lo prevenga ni lo vigile deja la capacidad a medias.
- **Cableado de hooks en `.claude/settings.json` del destino, por fusión con
  marcador** (CA-45..CA-47): el instalador escribe y actualiza **solo** las
  entradas de hook propias del kit, marcadas como suyas, y deja intacto todo lo
  demás del archivo (permisos, variables y hooks propios del destino). No es una
  copia byte a byte, así que no puede ser una entrada de manifiesto común; es el
  mismo patrón de marcador que `scripts/install_pre_push_hook.sh` ya aplica al
  hook de git, incluida su negativa a pisar lo ajeno.
- **`output-styles` como carga del kit** (CA-44): entradas de manifiesto nuevas
  bajo `.claude/output-styles/`. Son archivos planos, sin el problema de espacio
  compartido de `settings.json`, así que heredan sin más el tratamiento de
  cualquier ruta de la carga (sobrescritura, clasificación, retiro si dejan de
  declararse).
- **Cobertura doble Claude + Opencode del contrato de configuración** (CA-51..CA-55):
  `.mcp.json` (Claude) y `opencode.jsonc` (Opencode) son entradas de manifiesto
  que el instalador fusiona con marcador en el destino, mismo patrón que
  `.claude/settings.json` (CA-45..CA-47). `AGENTS.md` (prosa de gobernanza que el
  kit ahora porta) y `scripts/mcp-pce.sh` (launcher del cliente MCP) son carga
  ordinaria con overwrite. Las tres configuraciones (Claude settings, `.mcp.json`,
  `opencode.jsonc`) **deben viajar juntas**: ausencia de una aborta con código 2
  (CA-55), no 3 ni drift. El instalador del kit cubre ambos ecosistemas por igual:
  si una ruta funciona para Claude, debe funcionar para Opencode, y viceversa.
  Esta unidad **no** añade un contrato asimétrico entre los dos.
- Detección y retiro de rutas huérfanas en `--install` bajo las garantías de
  CA-17..CA-28; detección (sin escritura) en `verificar`.
- Comportamiento definido y probado contra destinos legados (CA-14..CA-16).
- Corregir el texto del propio instalador que quedará falso: hoy afirma que el
  registro "nunca" se lee para clasificar (docstring de `installer.py` y
  comentario de `_write_install_record`).
- Tests en `installer/tests/` para cada criterio, con la convención de nombre de
  CA-29.

**No incluye (fuera de alcance):**
- Elegir el esquema concreto de versión (semver, fecha, hash de árbol), el
  algoritmo de digest, el formato del registro ni dónde vive físicamente cada dato
  (manifiesto, archivo `VERSION`, registro): son decisiones de `plan.md`, nombradas
  como pendientes más abajo con el criterio que deben satisfacer.
- Retiro de huérfanas de destinos legados, o cualquier heurística para inferir la
  línea base de un destino sin registro (por ejemplo, "adoptar" el estado actual
  como línea base).
- Mecanismos de **migración de contenido** entre versiones (transformar archivos
  del destino), más allá de escribir la carga nueva y retirar la huérfana.
- Compartir la línea base entre clones o colaboradores de un mismo destino.
- La regla de gobernanza de `sdd-gate` § 3 ("superficie caída ≠ superficie
  inexistente"): ya se resolvió en el commit `9d827c5` de esta rama, fuera del
  ciclo SDD por decisión del humano.
- Cambios a los materializadores de espejos (`scripts/materialize_claude_*.py`)
  ni a la lógica de `git_target.py`. El hook de pre-push **sí** se toca, por el
  protocolo antidrift de arriba.
- Impedir de forma absoluta la edición manual en el destino. Ningún mecanismo
  local puede garantizarlo (un humano siempre puede editar y saltear un hook con
  `--no-verify`). El alcance es **disuadir y detectar**; la garantía dura, si se
  quiere, es un paso de CI en el destino que corra `verificar`, y eso es
  configuración de cada destino, no carga del kit.
- Gestionar cualquier entrada de `.claude/settings.json` que no sea un hook
  propio del kit. Permisos, variables de entorno y hooks del destino quedan
  fuera: el instalador los preserva, nunca los define ni los valida.
- Una política de compatibilidad entre versiones del kit (qué versión puede
  actualizar a cuál, bajadas de versión). Esta unidad registra la versión; no
  decide qué transiciones son válidas.

### Decisiones pendientes para `plan.md`, con su criterio

| Decisión | Criterio que la elegida debe satisfacer |
|---|---|
| Esquema de versión | Comparable o al menos comparable por igualdad de forma determinista; producible sin intervención manual propensa a olvido, o con un chequeo que falle si se olvida; ver P-5 |
| Dónde vive la versión | Un solo lugar autoritativo; ningún otro lugar la repite a mano (CA-01, CA-02) |
| Algoritmo de digest | Determinista, sobre los bytes del archivo, sin depender de plataforma; el que produce el registro es el mismo que compara en `install` y `verificar` (CA-08, CA-09, CA-10) |
| Origen del digest de la fuente | Si el manifiesto declara digests por entrada, un test debe fallar cuando difieran del contenido real de la fuente (mismo espíritu que `installer/tests/test_manifest_matches_inventory.py`); si se calculan al vuelo, no se declaran |
| Formato y ubicación del registro | Sigue fuera de la carga y fuera del worktree (CA-13); parseable con la misma dependencia de YAML que ya usa `manifest.py` |
| Directorios vaciados por un retiro | Un retiro no deja directorios vacíos que él mismo vació, y no toca directorios que ya estaban vacíos o contienen otros archivos |
| Forma del marcador en `settings.json` | **Decidido**: fusión con marcador (ver Alcance). `plan.md` fija la forma concreta del marcador y cómo se identifican las entradas del kit para poder actualizarlas y retirarlas sin tocar las ajenas; debe sobrevivir a que el destino reordene o reformatee el archivo |
| Alcance del chequeo de drift en el pre-push | `check_range()` de `.spec/scripts/pre-push-gate.sh:63-118` hace PASS temprano si el rango empujado no toca rutas de protocolo. `plan.md` decide si el chequeo de drift es incondicional o se suma a esa condición, y si compara el worktree o el árbol de los commits empujados (CA-33, CA-34) |
| Cómo el pre-push obtiene la línea base | El hook corre en el destino, donde no hay `installer/`; el chequeo de drift debe poder correr con lo que la carga sí deja en el destino y leyendo la línea base del directorio git |

## Criterios de aceptación

Cada criterio se decide sí/no mirando solo el repo. **Convención de
verificación:** cada CA de comportamiento tiene al menos un test en
`installer/tests/` cuyo nombre contiene `u0002_ca<NN>` (los prefijos `test_ca<NN>_`
ya existentes pertenecen a la unidad de origen del instalador y no se reutilizan);
se comprueba con `python3 -m pytest installer -q -k u0002_ca<NN>` (debe
seleccionar al menos un test y pasar). "Destino con línea base" es un destino ya
instalado por el instalador de esta unidad; "destino legado" es uno cuyo registro
tiene solo `installed_at` o no existe.

**Versión del kit**

- [ ] CA-01 — La versión del kit se obtiene por código desde una única fuente
      declarada en el repo, y un test la lee y verifica que es una cadena no vacía.
- [ ] CA-43 — Unicidad: un test falla si la cadena de versión de CA-01 aparece
      declarada en cualquier otro archivo de datos del repo (manifiesto, `VERSION`,
      constante de módulo) fuera de esa única fuente. Sin este criterio, la
      afirmación de fuente única de la tabla de decisiones y de la salvedad de
      § Governance no está respaldada por nada.
- [ ] CA-02 — La CLI imprime la versión del kit en stdout, en una invocación
      listada en `python3 installer/cli.py --help`, y un test verifica que **el
      stdout completo, sin espacios al inicio ni al final, es exactamente** el
      valor de CA-01 (no "lo contiene": una invocación de versión no imprime nada
      más).
- [ ] CA-03 — Tras un `--install` exitoso, el registro de instalación del destino
      contiene la versión del kit, igual al valor de CA-01; un test lo comprueba
      leyendo el registro.
- [ ] CA-04 — Si la fuente de versión falta o no se puede leer, `--install`
      termina con código 2 y el destino queda sin cambios (mismo listado de rutas y
      mismos bytes, dentro del worktree y del directorio git), como ya exige
      `installer/tests/test_installation.py::test_ca16`/`test_ca28` para un
      manifiesto roto.
- [ ] CA-05 — En la misma condición de CA-04, `verificar` termina con código 2.
- [ ] CA-06 — Sobre un destino con línea base, `verificar` imprime la versión
      registrada en el destino y la versión del kit.
- [ ] CA-07 — Sobre un destino legado, `verificar` indica de forma explícita que
      el destino no tiene versión registrada, en lugar de omitir la línea o
      imprimir un valor inventado.

**Línea base y clasificación**

- [ ] CA-08 — Tras un `--install` exitoso, el registro contiene una entrada por
      cada ruta que `iter_payload_files(entries)` produce (misma cantidad y mismas
      rutas), cada una con el digest del contenido que quedó escrito en el destino;
      un test recalcula el digest del archivo del destino y lo compara. **Este
      criterio queda explícitamente sujeto a `plan.md`**: no es verificable hasta
      que el plan fije el algoritmo (fila "Algoritmo de digest" de las decisiones
      pendientes), y el test se escribe recién entonces.
- [ ] CA-09 — Ruta **desactualizada** (destino con línea base; el contenido del
      destino coincide con su entrada en la línea base y la fuente difiere de ella):
      `--install` sin `--force` termina con código 0 y deja la ruta con el
      contenido de la fuente.
- [ ] CA-10 — Ruta en **drift** (el contenido del destino difiere de su entrada
      en la línea base, coincida o no con la fuente): `--install` sin `--force`
      termina con código 0, deja la ruta con el contenido de la fuente, e imprime
      una línea propia que contiene la palabra literal `drift` y la ruta.
- [ ] CA-11 — Con una ruta desactualizada y una en drift a la vez, `--install`
      sin `--force` termina con código 0 y deja **ambas** con el contenido de la
      fuente; la salida contiene una línea con `drift` para la segunda y **ninguna
      línea con `drift` para la primera** (que puede imprimirse con otra marca o
      no imprimirse).
- [ ] CA-37 — Ruta en drift cuyo contenido coincide con la fuente pero difiere de
      la línea base (estado que deja una instalación interrumpida): se clasifica
      como drift igual que CA-10, y el hook de CA-33 la ve igual que `--install`.
      Es el caso que hace que la definición sea contra la línea base y no contra
      la fuente.
- [ ] CA-38 — Ruta **ausente** (la línea base la registra, el manifiesto la sigue
      declarando, el destino ya no la tiene): `--install` la re-escribe con el
      contenido de la fuente y termina con código 0, nombrándola con una marca
      distinta de `drift`.
- [ ] CA-12 — `verificar` sobre un destino con línea base que tiene una ruta
      desactualizada, una en drift y una ausente termina con código 1 e imprime
      las tres rutas, cada una con una marca de categoría distinta de las otras
      dos; sobre un destino sin divergencias **y sin huérfanas** termina con
      código 0.
- [ ] CA-39 — `verificar` sobre un destino cuya única anomalía es una huérfana
      termina con código 1 (decidido en P-6: el destino no coincide con el kit, y
      `verificar` en CI es la garantía dura contra el drift).
- [ ] CA-13 — Tras un `--install` exitoso, el worktree del destino contiene
      exactamente la carga, los espejos y `.claude/settings.json`, y nada más. La
      línea base y la versión siguen viviendo fuera de la carga y fuera del
      worktree. `.claude/settings.json` es **excepción explícita** y la única:
      el instalador lo fusiona (CA-45), no lo copia, así que no es una entrada de
      la carga aunque viva en el worktree.
- [ ] CA-14 — Destino legado: una ruta cuyo contenido difiere de la fuente se
      sobrescribe con el contenido de la fuente y `--install` termina con código 0,
      nombrándola en la salida como divergencia sin clasificar (sin línea base no
      se puede decir si era desactualización o drift).
- [ ] CA-15 — Un `--install` exitoso sobre un destino legado deja un registro que
      cumple CA-03 y CA-08 (migración al formato nuevo en la primera instalación
      exitosa).
- [ ] CA-16 — Sobre un destino legado, `--install` no elimina ningún archivo del
      destino: el listado de rutas del destino tras la corrida contiene todas las
      rutas que tenía antes.

**Retiro de rutas huérfanas** (P-1, P-2 y P-3 ya fueron decididas por el humano el
2026-09-28; CA-20, CA-21 y CA-22 reflejan esas decisiones. Nótese la asimetría
deliberada frente a la carga declarada: una ruta de la carga se **sobrescribe**
siempre, pero **borrar** exige más cautela — una huérfana con drift se retiene por
defecto y solo `--force` la borra.)

- [ ] CA-17 — Detección: dado un destino con línea base que registra una ruta P
      que el manifiesto corriente no declara, `--install` sin flags adicionales
      nombra P en su salida como huérfana.
- [ ] CA-18 — Nunca borra lo que el kit no instaló: un archivo del destino cuya
      ruta no figura en la línea base (incluso si está bajo un directorio que el
      manifiesto expande, como `.agents/skills/<propia>/SKILL.md`) sigue existiendo
      con los mismos bytes tras `--install`.
- [ ] CA-19 — Nunca borra fuera del destino: si la línea base contiene una ruta
      que resuelve fuera de la raíz git del destino (por ejemplo `../x` o una ruta
      absoluta), el archivo apuntado sigue existiendo tras `--install`.
- [ ] CA-41 — Huérfana **ya ausente** (la línea base la registra, el manifiesto
      ya no la declara, y el destino ya no la tiene — estado que deja una corrida
      que la retiró y falló después): `--install` termina con código 0, no la
      nombra como error, y la línea base resultante ya no la contiene.
      `verificar` tampoco la nombra.
- [ ] CA-42 — Una entrada de la línea base cuyo destino en el repo es un symlink
      que resuelve fuera de la raíz git, o un directorio en vez de un archivo, no
      se borra: tras `--install` el objetivo del symlink y el directorio siguen
      existiendo. Extiende CA-19 (que solo cubre `../x` y rutas absolutas) a los
      dos casos que el filesystem permite y la ruta textual no revela.
- [ ] CA-20 — Huérfana sin drift (contenido del destino igual a su entrada
      en la línea base): tras `--install` la ruta ya no existe en el destino y la
      salida la nombra como retirada. *(decidido en P-1)*
- [ ] CA-21 — Huérfana con drift (contenido del destino distinto de su
      entrada en la línea base): tras `--install` sin flags la ruta sigue existiendo
      con los mismos bytes, `--install` termina con código 0 pese a ello, y la
      salida la nombra como retenida por drift. *(decidido en P-2)*
- [ ] CA-22 — `--force` sí autoriza el borrado de una huérfana con drift:
      tras `--install --force` esa ruta ya no existe en el destino y la salida la
      nombra como retirada. *(decidido en P-3)*
- [ ] CA-23 — Tras retirar una huérfana que era fuente de un espejo (por ejemplo
      una skill bajo `.agents/skills/`), el destino no conserva el espejo
      correspondiente bajo `.claude/`.
- [ ] CA-24 — La línea base escrita tras un `--install` que retiró una huérfana ya
      no contiene la ruta retirada.
- [ ] CA-25 — `verificar` sobre un destino con una huérfana la nombra en su salida
      como huérfana.
- [ ] CA-26 — `verificar` nunca borra: con una huérfana presente, el listado de
      rutas y los bytes del destino son idénticos antes y después de la corrida.
- [ ] CA-27 — Dos `--install` consecutivos sobre el mismo destino: el segundo
      termina con código 0, no nombra ninguna huérfana ni retira nada, y deja el
      destino byte-idéntico.
- [ ] CA-28 — Un `--install` que termina con código distinto de 0 (código 2 por
      error operativo, o código 3 por la colisión de CA-40) deja el archivo de
      registro byte-idéntico al que tenía antes de la corrida.

**Protocolo antidrift en el destino**

- [ ] CA-33 — El hook `pre-push` instalado en un destino con línea base rechaza
      (código distinto de 0) un push cuando alguna ruta de la carga está en drift,
      y nombra cada ruta en drift en su salida.
- [ ] CA-34 — Ese mismo hook, sobre un destino recién instalado y sin tocar, deja
      pasar el push (código 0): el chequeo de drift no produce falsos positivos
      sobre un destino intacto. El escenario de prueba fija un rango de commits
      que **no** toca rutas de protocolo, para que el PASS temprano de
      `check_range()` no enmascare el resultado ni el marcador
      `.spec/.pilot-verde` lo bloquee por una causa ajena a la línea base.
- [ ] CA-48 — Sin línea base (destino legado o clon nuevo) el hook de CA-33 y el
      guard de CA-35 **no bloquean** y lo dicen: el push pasa (código 0) y el guard
      permite la edición, ambos emitiendo una línea que informa que no hay línea
      base contra la cual comparar. Fallan abierto a propósito: un clon nuevo no
      puede quedar impedido de trabajar por una línea base que todavía no existe.
- [ ] CA-49 — Registro de instalación presente pero ilegible (YAML inválido,
      truncado, o con una entrada sin digest): `--install` lo trata como destino
      legado —sobrescribe la carga, no retira huérfanas, y lo reemplaza por un
      registro válido al cerrar con éxito— e informa en stdout que lo descartó.
      `verificar` sobre ese destino termina con código 1 y nombra el registro
      ilegible. No se aborta con código 2: un registro corrupto no debe dejar un
      destino sin poder actualizarse.
- [ ] CA-35 — El guard, ejecutado con la interfaz `PreToolUse` que ya usan
      `guard_generated_paths.py` y `guard_bash_spec_writes.py` (payload JSON por
      stdin con `tool_name` y `tool_input.file_path`, decisión `deny` por stdout),
      deniega una ruta que la línea base registra como instalada por el kit y
      **permite** una ruta propia del destino que el kit nunca instaló. Se
      comprueba invocándolo sobre las dos clases de ruta y verificando las dos
      respuestas.
- [ ] CA-36 — **Cableado**: tras `--install`, `.claude/settings.json` del destino
      registra ese guard en el evento `PreToolUse`, y una invocación real del guard
      a través de esa configuración deniega la edición. Sin este criterio CA-35 se
      cumpliría con un guard inerte — que es exactamente el estado de hoy.
- [ ] CA-45 — **Fusión con marcador**: partiendo de un `.claude/settings.json` del
      destino que ya contiene permisos, variables y un hook propio ajeno al kit,
      tras `--install` esas entradas siguen presentes y sin cambios, y las del kit
      quedan añadidas y marcadas como suyas.
- [ ] CA-46 — **Idempotencia**: dos `--install` consecutivos dejan
      `.claude/settings.json` byte-idéntico entre la primera y la segunda corrida
      (las entradas del kit no se duplican ni se reordenan).
- [ ] CA-47 — **No pisar lo ajeno, en los dos canales**: el hook de git preexistente
      y ajeno al kit no se sobrescribe (como ya garantiza
      `scripts/install_pre_push_hook.sh` con su marcador y comprueba
      `test_installation.py::test_ca27`), y un hook `PreToolUse` ajeno ya presente
      en `.claude/settings.json` tampoco se sobrescribe ni se elimina.

**Disciplina de versión y carga nueva**

- [ ] CA-50 — Un test falla si el contenido de la carga que el manifiesto declara
      cambió sin que cambiara la cadena de versión de CA-01 (decidido en P-5:
      versión manual con red de seguridad). El test compara un digest agregado de
      la carga contra el que quedó registrado junto a la versión vigente; `plan.md`
      fija dónde vive ese digest agregado.
- [ ] CA-44 — `.claude/output-styles/` figura en `installer/kit_manifest.yaml` con
      al menos una entrada, y tras un `--install` exitoso esos archivos existen en
      el destino con los mismos bytes que la fuente. Al ser rutas de la carga
      ordinarias, quedan cubiertas sin excepción por CA-08 (línea base), CA-10
      (drift) y CA-17..CA-27 (huérfanas); un test lo comprueba retirando una del
      manifiesto y verificando que se detecta como huérfana.

**Cobertura doble Claude + Opencode** (delta del 2026-09-29; antes la cobertura
era solo Claude; ahora ambos ecosistemas son carga del kit con el mismo contrato
de fusión con marcador, ver Alcance):

- [ ] CA-51 — `.mcp.json` figura en `installer/kit_manifest.yaml` y, tras un
      `--install` exitoso, existe en el destino. El instalador lo fusiona con
      marcador: cada server entry declarada por el kit lleva un campo
      `_sdd_kit: true`; entries del destino ajenas al kit se preservan sin
      cambios; las del kit se reemplazan limpio en cada `--install`. El
      discriminador es por valor de campo (no por posición ni número de línea)
      y sobrevive a que el destino reordene o reformatee el archivo. Idempotente
      byte-a-byte entre dos `--install` consecutivos (análogo a CA-46).

- [ ] CA-52 — `opencode.jsonc` figura en `installer/kit_manifest.yaml` y, tras
      un `--install` exitoso, existe en el destino. El instalador lo fusiona
      con marcador bajo la key `mcp` (sintaxis JSONC propia de Opencode), con
      el mismo discriminador `_sdd_kit: true` por server y la misma
      idempotencia que CA-51. La diferencia con `.mcp.json` es solo de
      sintaxis y key raíz (`mcpServers` vs `mcp`); el contrato es idéntico.

- [ ] CA-53 — `AGENTS.md` figura en `installer/kit_manifest.yaml` y, tras un
      `--install` exitoso, el destino contiene el archivo con los mismos bytes
      que la fuente (overwrite, no fusión). Como carga ordinaria, queda
      cubierto por CA-08 (línea base), CA-10 (drift) y CA-17..CA-27
      (huérfanas): si la versión siguiente del kit retira `AGENTS.md` del
      manifiesto, una instalación subsiguiente lo borra del destino si no
      tiene drift. Si el destino tenía prosa local en su `AGENTS.md`, el
      install la sobreescribe con la del kit — es overwrite, no fusión.

- [ ] CA-54 — `scripts/mcp-pce.sh` figura en `installer/kit_manifest.yaml`
      como carga ordinaria. Tras `--install`, existe en el destino con los
      mismos bytes que la fuente, ejecutable (`chmod +x`). Si
      `scripts/mcp-pce.py` falta en el destino, `mcp-pce.sh` falla con código
      != 0 y mensaje claro a stderr al invocarse; **el install no aborta**
      por esa ausencia (es una dependencia runtime, no de carga): el hook
      `PreToolUse` que invoque el script debe tratarlo como fail-open con
      mensaje "pce-mcp no disponible", no como crash. La presencia o ausencia
      de `mcp-pce.py` es asunto del destino (vendored o provisto por la
      PCE); el kit solo carga el loader.

- [ ] CA-55 — **Cobertura doble.** Un test fija que, si `.mcp.json` figura
      en `installer/kit_manifest.yaml` y `opencode.jsonc` **no** figura (o
      viceversa), el manifest es **inconsistente** y el install aborta con
      código 2 antes de tocar el destino: las tres configuraciones
      (`.claude/settings.json` fusionada por el instalador, `.mcp.json` y
      `opencode.jsonc` cargadas del manifiesto) **deben viajar juntas**.
      Esta consistencia se verifica al inicio del run, antes de cualquier
      escritura. La asimetría entre Claude-only y Claude+Opencode no es
      válida: si el kit gobierna un ecosistema, gobierna los dos.

**Cierre**

- [ ] CA-29 — Todo test nuevo de esta unidad lleva `u0002_ca<NN>` en su nombre y
      cada CA de CA-01 a CA-28 y de CA-33 a CA-55 tiene al menos uno;
      `python3 -m pytest installer -q --collect-only -k u0002` lista tests para cada
      número.
- [ ] CA-30 — Ningún texto del instalador describe el contrato viejo. Se comprueba
      en dos partes, ambas obligatorias: (a)
      `grep -n "never read\|nunca leído\|never enters this\|bookkeeping only" installer/installer.py`
      no devuelve nada — las tres frases que hoy afirman que el registro no se lee
      (líneas 7, 58 y 118) más la que lo llama "bookkeeping only" (líneas 5-6); y
      (b) el docstring de módulo de `installer/installer.py` y el texto de `--force`
      y de los códigos de salida en `installer/cli.py` describen el contrato nuevo
      (`--force` autoriza solo el borrado de huérfanas con drift; el código 3 tiene
      como única causa la colisión de CA-40), verificado por un test que busca en
      esos textos la descripción vieja y falla si sobrevive.
- [ ] CA-31 — Los tests preexistentes de `installer/tests/` no pierden ni cambian
      aserciones, salvo las excepciones que este spec enumera. Se comprueba contra
      un punto fijo, no contra una referencia móvil:
      `git diff $(git merge-base HEAD master) -- installer/tests` (`master` avanza,
      y tras mergear la unidad un diff contra `master` quedaría vacío y el criterio
      pasaría de forma vacua). **Excepciones inevitables, enumeradas aquí y no
      delegadas a `plan.md`:** `test_ca13_conflict_is_reported_regardless_of_previous_install_record`,
      `test_ca14_any_conflict_aborts_the_whole_run` y
      `test_ca15_force_completes_the_whole_install` de
      `installer/tests/test_installation.py` aseveran hoy código 3 y aborto sin
      escritura ante una ruta divergente; el contrato nuevo los invierte (CA-10,
      CA-11, CA-14) y **deben** cambiar. Cualquier otra modificación de un test
      preexistente es un incumplimiento de este criterio.
- [ ] CA-32 — `python3 -m pytest .spec/scripts/tests installer -q` (el
      `comando_validacion` de la unidad) termina con código 0.

> Nota sobre `0001`: sus CA-03 (`grep` sin coincidencias de `version|digest|sha256|hash`
> en `installer/kit_manifest.yaml` y de `unlink|rmtree|remove` en
> `installer/installer.py`) describían el estado **al cierre de 0001** y dejarán de
> cumplirse por diseño al implementar esta unidad. No son un invariante permanente y
> 0001 está cerrada; se deja constancia para que nadie lea esa divergencia como
> regresión.

## Governance aplicable

Este repositorio **tiene superficie de gobernanza propia que el propio kit
produce como carga**, pero `pce-mcp` **no** está conectado al kit sobre sí
mismo: el kit es el **productor** del contrato de gobernanza que cada destino
consume, no su **consumidor**. Hechos comprobables al cierre del scope delta
(2026-09-29):

- **Existe** `AGENTS.md` en la raíz — es prosa de gobernanza que el kit
  **escribe como fuente** y que viaja al destino como carga (CA-53). El kit
  **lee** este archivo al redactar el contrato, pero **no lo evalúa** contra
  `pce-mcp`: el `pce-mcp` del destino es el que lo lee en el destino.
- **Existe** `.mcp.json` (Claude) y **existe** `opencode.jsonc` (Opencode)
  en la raíz — son configs MCP que el kit declara y que viajan al destino
  como carga con fusión con marcador (CA-51, CA-52). Tampoco se evalúan
  localmente: apuntan a `scripts/mcp-pce.sh`, que requiere `PCE_MCP_API_KEY`
  en `.env` del destino, no del kit.
- **No existe** `pce-mcp` configurado como dependencia del kit sobre sí
  mismo: `~/.config/opencode/opencode.jsonc` solo lista `codebase-memory-mcp`
  y `gitnexus` (no `pce-mcp`), y el kit no tiene un `.mcp.json` declarado
  como configuración operativa (su `.mcp.json` es **carga para destinos**,
  no config que el kit se aplique a sí mismo).

Por eso el centinela `governance_refs: [ninguna-aplicable]` sigue siendo
correcto: la superficie **existe como artefacto producido**, pero no se
**aplica al kit** (sería aplicar al producto sobre sí mismo). El caso
"superficie caída" de `sdd-gate` § 3 tampoco aplica: el lente L4 puede
confirmar, leyendo el filesystem del kit, que (a) AGENTS.md existe y es la
prosa que el destino consume, (b) los configs MCP existen pero no están
conectados localmente, y (c) `pce-mcp` no corre contra el kit. La
verificación es distinta a la del spec original (que decía "no existe
ninguno"): ahora dice "existen pero no aplican aquí".

| Tipo | Id | Cómo aplica / restringe |
|---|---|---|
| — | ninguna-aplicable | Superficie de gobernanza **producida** por el kit (AGENTS.md, `.mcp.json`, `opencode.jsonc`) pero `pce-mcp` **no se ejecuta** contra el repo del kit; la prosa normativa local no es evaluable aquí; ver justificación arriba |

**Salvedad que este spec deja registrada** (a diferencia de `0001`, esta unidad sí
decide fuentes de verdad): al fijar dónde vive la versión del kit y dónde viven los
digests y la línea base, `plan.md` estará tomando decisiones de fuente única. El
kit cita en su prosa normativa artefactos de gobernanza sobre exactamente eso
(`pri-gob-fuente-verdad-unica`, citado en `.spec/_plantillas/plan-maestro.md:56,117`,
`.spec/_plantillas/mandato.md:122` y `.spec/PARADAS-SUPERVISADO.md:33`;
`pri-gob-precedencia-superficies` también aparece en la prosa del kit). El scope
delta del 2026-09-29 agrega a esa lista decisiones que esta unidad toma sobre la
**forma del contrato de gobernanza** que el kit lleva a destinos: el discriminador
`_sdd_kit: true` en los configs JSON (CA-51, CA-52), la semántica de
overwrite-no-fusión de `AGENTS.md` (CA-53), y la decisión de exigir
cobertura doble Claude+Opencode como invariante del kit (CA-55). Aquí **no
son evaluables** porque, aunque la superficie existe (el kit la produce), `pce-mcp`
no se ejecuta contra el repo del kit, y este spec no los cita como aplicables ni
transcribe su mandato de memoria. Lo que queda dicho es contra qué habría que
contrastar las decisiones en un destino con `pce-mcp`: (a) que exista un único
lugar autoritativo por dato (versión; digest de la fuente; línea base del destino)
y ningún otro lo repita a mano — el spec lo baja a criterio verificable en
CA-01/CA-02 y en la fila "Origen del digest de la fuente" de las decisiones
pendientes; (b) que la forma del contrato de gobernanza que el kit entrega
(`AGENTS.md` overwrite + `_sdd_kit: true` discriminator + cobertura doble) sea
compatible con cómo `pce-mcp` espera leerlo en el destino; y (c) que la
precedencia entre cualquier decisión de esta unidad y una regla de gobernanza
aceptada la resuelva la gobernanza, no este documento. Esa contrastación **no
queda como buena intención**: `plan.md` debe llevarla como fila obligatoria de
riesgo residual, con el disparador explícito *"antes de cerrar esta unidad, o al
primer uso del kit en un destino que sí tenga `pce-mcp`"*, y la decisión que se
tome se anota en `bitacora.md`. Sin dueño ni disparador el spec se estaría dando
permiso a sí mismo por omisión, que es justo lo que el centinela no autoriza.

Nótese que la afirmación de fuente única que esta salvedad menciona **sí** está
bajada a criterio verificable, pero no por CA-01/CA-02 (que solo comprueban que la
versión se lee y se imprime): la cubre **CA-43**, que falla si la cadena de versión
aparece declarada en más de un archivo. Análogamente, las decisiones del scope
delta sobre el contrato de gobernanza quedan bajadas a criterios verificables
(CA-51..CA-55), pero los **principios** que deberían regularlos
(`pri-gob-precedencia-superficies`, en particular: ¿el destino puede tener su
propia `AGENTS.md` además de la del kit? ¿el destino puede tener entries de MCP
sin el discriminador y eso le gana al kit?) solo se contrastan cuando un
destino real con `pce-mcp` los aplique.

## Preguntas abiertas

Solo el humano puede resolverlas; en modo interactivo quedan listadas para el
checkpoint. Con recomendación del redactor entre paréntesis, no como decisión.

> **P-1 a P-6 quedaron decididas por el humano los días 2026-09-28 y 2026-09-29**,
> en los checkpoints que siguieron a la redacción y al panel del gate:
>
> - **P-1** — retirar automáticamente las huérfanas intactas (CA-20).
> - **P-2** — retener la huérfana con drift, avisar, sin cambiar el código de
>   salida (CA-21).
> - **P-3** — `--force` **sí** borra una huérfana con drift (CA-22), y ésa pasa a
>   ser su única función.
> - **P-4** — la línea base vive bajo el directorio git, por clon, **sin** arranque
>   en frío conservador (ver *Hechos de compatibilidad* y CA-48).
> - **P-5** — versión manual con red de seguridad: un test falla si la carga cambia
>   sin que cambie la versión (CA-50).
> - **P-6** — una huérfana **sí** hace que `verificar` termine con código 1
>   (CA-39), porque `verificar` en CI es la garantía dura contra el drift.
>
> En esos mismos checkpoints el humano fijó la **premisa de reempaquetamiento**
> —que reescribió la motivación de la capacidad 2—, resolvió la colisión de ruta
> nueva a favor de abortar (CA-40), eligió **fusión con marcador** para el cableado
> de hooks en `.claude/settings.json` (CA-45..CA-47) y sumó al alcance el protocolo
> antidrift y los `output-styles` (CA-44).

Queda una sola pregunta abierta, que no deja ningún criterio indecidible:

- **P-7 — Rutas huérfanas que el manifiesto pasó a declarar con otro destino
  (renombre).** Una ruta que se renombró en el kit aparece a la vez como huérfana
  (destino viejo) y como ruta nueva. El spec la trata como dos casos independientes
  (la vieja se retira según P-1/P-2; la nueva se escribe). ¿Basta, o se quiere
  arrastrar a la ruta nueva el drift que hubiera sobre la vieja? (Recomendación:
  basta; arrastrarlo es migración de contenido, fuera de alcance — y bajo la
  premisa de reempaquetamiento ese drift no debería existir.)
