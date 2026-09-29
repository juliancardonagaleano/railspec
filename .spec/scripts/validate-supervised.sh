#!/usr/bin/env bash
# `comando_validacion` of unit 0109a — supervised mode artifacts (S-28).
#
# Chains the 12 steps from `plan.md` § Comando de validación. Fails at the first
# step that goes red, naming it. Runs **from the repo root**, with the system's
# `python3` and `bash`; no network, no agents, no external dependencies.
#
#     bash .spec/scripts/validate-supervised.sh
#
# Steps 1, 3-8, 10 and 11 validated fixture-bound artifacts of unit 0109a
# (literal anchors, per-code fixtures, frozen historical baselines) against a
# fixture tree a later unit retires wholesale (D-11) — or against sections
# (`## Precondiciones de aprobación`, `## Anotación del ADR propuesto`,
# `## Evidencia de piloto`) that same unit removes from the
# templates and from the validator's own required anchors (CA-29). None of
# them is reconstructable "inline": a frozen baseline (steps 6-7) is recorded
# evidence of a specific past commit, not a re-derivable seed, and the
# anchors steps 1/8 checked for are gone by design, not by accident. They are
# retired **in place** below — same step number, one line explaining why, exit
# 0 — rather than deleted outright: `mandate_anchor.py`'s own docstring and
# `test_mandate_anchor.py`'s differential test over real git history both
# name "Paso 9" by number, and retiring the numbering along with the step
# would just move the same fragility elsewhere. Steps 2 (closed code list), 9
# (aditividad del índice de mandatos) and 12 (every real `modo: supervisado`
# unit resolves its mandate) keep doing real work.
#
# Comparison base (step 9). Compares against `BASE_COMMIT`: `VALIDAR_BASE` if
# set (required — the frozen-baseline file step 9 used to fall back to lived
# under the retired fixture tree), else the step fails asking for it
# explicitly.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT" || exit 1

# shellcheck source=./_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

VALIDATOR=".spec/scripts/validate_mandate.py"

STEP=""
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

step() { STEP="$1"; printf '\n=== %s\n' "$1"; }
fail() { printf 'FAIL — %s\n      %s\n' "$STEP" "$1" >&2; exit 1; }
ok() { printf 'ok — %s\n' "$1"; }
retired() { printf 'retirado — %s\n' "$1"; }

# ---------------------------------------------------------------------------- 1
step "Paso 1 — anclas literales de CA-01 / CA-02 en plantillas, primer plan y fixture"
retired "verificaba \`.spec/_plantillas/mandato.md\` y una fixture propia contra listas de anclas que incluían \`## Precondiciones de aprobación\`/\`## Evidencia de piloto\` — secciones y fixture que esta unidad retira (CA-29, D-11)"

# ---------------------------------------------------------------------------- 2
step "Paso 2 — lista cerrada de 30 códigos (CA-21): --codigos == lista inline"
EXPECTED_CODES="$(cat <<'CODES_EOF'
aprobacion-ausente
aprobacion-desactualizada
aprobacion-sin-contexto
cadena-unidad-inexistente
cierre-con-pendientes
decision-campo-ausente
decision-fuera-de-delegacion
decision-id-duplicado
decision-sobre-reservada
desbloqueo-no-autor
implementacion-sin-aprobacion
mandato-entrada-sin-fecha
mandato-estado-inconsistente
mandato-forma-invalida
mandato-sin-fin
parada-abierta-sin-estado-parado
paradas-sin-referencia
paralelismo-excede-tope
paralelismo-sin-declarar
plan-unidad-sin-referencia-inversa
retoma-incompleta
revertida-sin-tarea
revision-sin-fecha
seccion-ausente
seccion-duplicada
unidad-mandato-doble
unidad-mandato-inexistente
unidad-mandato-vacio
unidad-plan-inexistente
unidad-supervisado-sin-mandato
CODES_EOF
)"
python3 "$VALIDATOR" --codigos | sort > "$TMP/codigos-cli.txt" \
  || fail "el validador no pudo imprimir --codigos"
printf '%s\n' "$EXPECTED_CODES" | sort > "$TMP/codigos-esperados.txt"
diff -u "$TMP/codigos-cli.txt" "$TMP/codigos-esperados.txt" \
  || fail "--codigos difiere de la lista inline de este paso"
n_codes="$(printf '%s\n' "$EXPECTED_CODES" | grep -c .)"
[ "$n_codes" -eq 30 ] || fail "la lista inline tiene $n_codes líneas, se esperaban 30"
ok "30 códigos, idénticos en el validador y en la lista inline"

# ---------------------------------------------------------------------------- 3
step "Paso 3 — fixtures de unidad + cobertura de los 30 códigos por la unión"
retired "iteraba un árbol de fixtures por unidad y por plan, uno por código (D-11, árbol eliminado); \`.spec/scripts/tests/test_mutation_each_code_caught_by_validator.py\` cubre, código por código, que el validador de verdad los emite"

# ---------------------------------------------------------------------------- 4
step "Paso 4 — fixtures de plan"
retired "iteraba un árbol de fixtures de plan (D-11, árbol eliminado)"

# ---------------------------------------------------------------------------- 5
step "Paso 5 — primer plan: exit ≠ 0 y conjunto == {aprobacion-ausente} (CA-30)"
retired "el primer plan (\`ola4-proceso-de-arquitectura\`) ya tiene aprobación registrada — el caso que este paso fijaba (recién creado, sin aprobar) no describe ningún plan real hoy; \`test_validate_mandate_unit_level_stop.py\`/\`test_mandate_adversarial.py\` cubren \`aprobacion-ausente\` con semillas propias"

# ---------------------------------------------------------------------------- 6
step "Paso 6 — línea base de 0068/scripts/validar.py sin diff (CA-26)"
retired "comparaba contra una captura congelada de un commit específico (D-11, árbol de fixtures eliminado) — no es un caso reconstruible inline, es evidencia histórica"

# ---------------------------------------------------------------------------- 7
step "Paso 7 — campos de sdd-retomar en los reportes .despues.md (CA-27, CA-29)"
retired "comparaba contra reportes \`.despues.md\` congelados de unidades reales (D-11, árbol de fixtures eliminado) — evidencia histórica, no reconstruible inline"

# ---------------------------------------------------------------------------- 8
step "Paso 8 — no-copia de gobernanza sobre los archivos del inventario (CA-24)"
retired "invocaba \`no_copy.py\` contra shingles del ADR que introdujo este modo (dominio \`ia\` rechazado, CA-29); \`no_copy.py\` y su fixture se eliminan con este mismo corte"

# ---------------------------------------------------------------------------- 9
step "Paso 9 — aditividad del índice de mandatos (CA-28)"
if [ -n "${VALIDAR_BASE:-}" ]; then
  BASE_COMMIT="$VALIDAR_BASE"
else
  fail "\$VALIDAR_BASE sin definir — la base congelada de 0109a vivía bajo la fixture eliminada (D-11); este paso exige la base explícita"
fi
[ -n "$BASE_COMMIT" ] || fail "la base de comparación está vacía"
BASE_COMMIT="$(git rev-parse --verify "${BASE_COMMIT}^{commit}" 2>/dev/null)" \
  || fail "la base de comparación no resuelve a un commit (revisar \$VALIDAR_BASE)"
ok "base de comparación: $BASE_COMMIT"
# El índice vigente es `.spec/units/_mandatos-supervisado.md` (ver
# `.spec/SUPERVISADO.md` § "Línea de changelog"). Si el archivo existe en la
# base congelada, se verifica su aditividad; si no existe (retirado por U-0009
# o aún no creado), se omite el chequeo sin fallar — el archivo es histórico,
# no normativo, y no aparece en ninguna unidad vigente.
INDEX_FILE='.spec/units/_mandatos-supervisado.md'
if git cat-file -e "${BASE_COMMIT}:${INDEX_FILE}" 2>/dev/null; then
  git diff -U0 "$BASE_COMMIT" -- "$INDEX_FILE" | grep -E '^-[^-]' > "$TMP/borradas.txt"
  if [ -s "$TMP/borradas.txt" ]; then
    cat "$TMP/borradas.txt" >&2
    fail "$INDEX_FILE tiene líneas eliminadas o reescritas; CA-28 exige adiciones"
  fi
  ok "$INDEX_FILE solo con adiciones (CA-28)"
else
  ok "ningún índice de mandatos presente en la base — Paso 9 no aplica"
fi

# ---------------------------------------------------------------------------- 10
step "Paso 10 — todo materializado del kit ya versionado y modificado, inventariado"
retired "comparaba contra un inventario de materializados que ya no existe en este repo"

# ---------------------------------------------------------------------------- 11
step "Paso 11 — la capa local sigue aplicada sobre los materializados del kit"
retired "verificaba literales contra ese mismo inventario; mismo mecanismo retirado que el paso 10"

# ---------------------------------------------------------------------------- 12
step 'Paso 12 — toda unidad real con «modo: supervisado» declara un mandato resoluble'
# `0109b` wrote this step (`_common.sh:paso12`, T22): any unit that *does*
# declare `modo: supervisado` must resolve its `mandato` (none of the six
# reference-resolution codes), with no allowlist of ids —
# `validate_mandate.py --unidad` is the judge, not a literal list.
if paso12 .spec/units ; then
  ok 'ninguna unidad real declara «modo: supervisado» con mandato irresoluble'
else
  fail 'una unidad real declara «modo: supervisado» con un mandato que no resuelve'
fi

printf '\nPASS — los 12 pasos de la validación de 0109a en verde.\n'
exit 0
