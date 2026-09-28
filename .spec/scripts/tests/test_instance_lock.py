"""`instance_lock.py` — CA-11a, CA-11b, CA-11c (unit 0114, G1).

The three subcommands against a real mandate copied into a temporary directory:
`inspect` reads and never writes, `acquire` leaves the three bullets of § 1.1, and
`release` empties the section and refuses to touch a lock that belongs to another
session.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS.parents[1]
SCRIPT = SCRIPTS / "instance_lock.py"

sys.path.insert(0, str(SCRIPTS))

import instance_lock  # noqa: E402
from _common import bullets, sections  # noqa: E402

SESSION = "https://claude.ai/code/session_0114-fixture"
LAUNCHER = "Julian Cardona Galeano"
START = "2026-09-19T08:00Z"

#: Inline seeds (no fixture tree): a free mandate — `## Instancia en curso`
#: with no `- sesion:` bullet — and a busy one, whose three bullets match the
#: literals `test_inspect_de_un_lock_ocupado_sale_3_y_cita_sesion_e_inicio`
#: asserts. Both carry a second section (`## Revisión posterior`) so
#: `test_el_resto_del_mandato_no_cambia` has something outside the lock
#: section to compare before/after.
FREE_MANDATE_TEXT = (
    "# Mandato supervisado — fixture\n\n"
    "## Objetivo y criterio de salida\n\n"
    "Fixture de prueba de instance_lock.py.\n\n"
    "## Instancia en curso\n\n"
    "Sin instancia.\n\n"
    "## Revisión posterior\n\n"
    "Vacía.\n"
)

BUSY_MANDATE_TEXT = (
    "# Mandato supervisado — fixture\n\n"
    "## Objetivo y criterio de salida\n\n"
    "Fixture de prueba de instance_lock.py.\n\n"
    "## Instancia en curso\n\n"
    "- sesion: https://claude.ai/code/session_00-fixture-otra-instancia\n"
    "- lanzador: Julian Cardona Galeano\n"
    "- inicio: 2026-09-17T13:55Z\n"
    "- pid: 999999\n\n"
    "## Revisión posterior\n\n"
    "Vacía.\n"
)


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, check=False)


class InstanceLockTests(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="0114-lock-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def copy(self, source: str, name: str = "mandato.md") -> Path:
        target = self.tmp / name
        target.write_text(source, encoding="utf-8")
        return target

    # --- inspect ---------------------------------------------------------------------

    def test_inspect_de_un_lock_libre_sale_0_y_dice_libre(self) -> None:
        mandate = self.copy(FREE_MANDATE_TEXT)
        before = mandate.read_bytes()
        completed = run(["inspect", str(mandate)])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "libre")
        self.assertEqual(mandate.read_bytes(), before, "`inspect` solo lee")

    def test_inspect_de_un_lock_ocupado_sale_3_y_cita_sesion_e_inicio(self) -> None:
        mandate = self.copy(BUSY_MANDATE_TEXT)
        completed = run(["inspect", str(mandate)])
        self.assertEqual(completed.returncode, 3)
        self.assertIn("ocupado", completed.stdout)
        self.assertIn("session_00-fixture-otra-instancia", completed.stdout)
        self.assertIn("inicio=2026-09-17T13:55Z", completed.stdout)

    def test_mandato_inexistente_sale_1(self) -> None:
        for subcommand, extra in (("inspect", []),
                                  ("acquire", ["--session", SESSION,
                                               "--launcher", LAUNCHER]),
                                  ("release", ["--session", SESSION])):
            with self.subTest(subcommand=subcommand):
                completed = run([subcommand, str(self.tmp / "no-existe.md"), *extra])
                self.assertEqual(completed.returncode, 1, completed.stderr)

    # --- acquire ---------------------------------------------------------------------

    def test_dry_run_imprime_byte_a_byte_lo_que_la_ejecucion_real_escribe(self) -> None:
        """CA-11a: the dry-run text is the text the real run leaves in the file."""
        mandate = self.copy(FREE_MANDATE_TEXT)
        before = mandate.read_bytes()
        args = ["acquire", str(mandate), "--session", SESSION,
                "--launcher", LAUNCHER, "--start", START]

        dry = run([*args, "--dry-run"])
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertEqual(mandate.read_bytes(), before, "el dry-run no escribe")

        real = run(args)
        self.assertEqual(real.returncode, 0, real.stderr)
        body = sections(mandate.read_text(encoding="utf-8"))["## Instancia en curso"]
        written = "\n".join(line for line in body.splitlines() if line.strip())
        self.assertEqual(dry.stdout.strip("\n"), written)

    def test_acquire_deja_los_tres_bullets_de_1_1(self) -> None:
        """CA-11b."""
        mandate = self.copy(FREE_MANDATE_TEXT)
        completed = run(["acquire", str(mandate), "--session", SESSION,
                         "--launcher", LAUNCHER, "--start", START])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        body = sections(mandate.read_text(encoding="utf-8"))["## Instancia en curso"]
        fields = bullets(body)
        self.assertEqual({key: fields.get(key) for key in
                          ("sesion", "lanzador", "inicio")},
                         {"sesion": SESSION, "lanzador": LAUNCHER, "inicio": START})
        self.assertEqual(run(["inspect", str(mandate)]).returncode, 3)

    def test_acquire_agrega_el_bullet_pid_del_proceso_padre(self) -> None:
        """4to bullet — evidencia de dueño del lock (`os.getppid()` capturado al
        escribir el bloque). `run()` lanza `acquire` como subproceso de este test,
        así que su padre es el proceso que corre este test."""
        mandate = self.copy(FREE_MANDATE_TEXT)
        completed = run(["acquire", str(mandate), "--session", SESSION,
                         "--launcher", LAUNCHER, "--start", START])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        body = sections(mandate.read_text(encoding="utf-8"))["## Instancia en curso"]
        fields = bullets(body)
        self.assertIn("pid", fields)
        self.assertTrue(fields["pid"].strip().isdigit(), fields["pid"])
        self.assertEqual(int(fields["pid"]), os.getpid())

    def test_acquire_sobre_un_lock_ocupado_sale_3_sin_tocar_nada(self) -> None:
        """CA-11c."""
        mandate = self.copy(BUSY_MANDATE_TEXT)
        before = mandate.read_bytes()
        completed = run(["acquire", str(mandate), "--session", SESSION,
                         "--launcher", LAUNCHER, "--start", START])
        self.assertEqual(completed.returncode, 3)
        self.assertEqual(mandate.read_bytes(), before)

    def test_el_resto_del_mandato_no_cambia(self) -> None:
        mandate = self.copy(FREE_MANDATE_TEXT)
        before = sections(mandate.read_text(encoding="utf-8"))
        run(["acquire", str(mandate), "--session", SESSION,
             "--launcher", LAUNCHER, "--start", START])
        after = sections(mandate.read_text(encoding="utf-8"))
        self.assertEqual(set(before), set(after), "ninguna sección aparece ni se va")
        for anchor in before:
            if anchor == "## Instancia en curso":
                continue
            self.assertEqual(before[anchor], after[anchor], anchor)

    # --- release ---------------------------------------------------------------------

    def test_release_vacia_la_seccion_con_la_forma_de_la_plantilla(self) -> None:
        """CA-11b. «Vacía» tiene el sentido que le da `instance_lock.EMPTY_BODY`:
        la sección no cuelga ningún bullet `- sesion:` — la misma forma que la
        plantilla `.spec/_plantillas/mandato.md` deja para «## Instancia en
        curso» vacía; comparado aquí contra la constante del propio código
        (`instance_lock.py` es la fuente de verdad de esa forma), no leyendo la
        plantilla en vivo."""
        mandate = self.copy(BUSY_MANDATE_TEXT)
        completed = run(["release", str(mandate),
                         "--session",
                         "https://claude.ai/code/session_00-fixture-otra-instancia"])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        body = sections(mandate.read_text(encoding="utf-8"))["## Instancia en curso"]
        self.assertEqual(bullets(body), {})
        self.assertIn(instance_lock.EMPTY_BODY, body)
        self.assertEqual(run(["inspect", str(mandate)]).returncode, 0)

    def test_release_dry_run_no_escribe_e_imprime_lo_mismo(self) -> None:
        """CA-11a para `release`."""
        mandate = self.copy(BUSY_MANDATE_TEXT)
        before = mandate.read_bytes()
        args = ["release", str(mandate), "--session",
                "https://claude.ai/code/session_00-fixture-otra-instancia"]
        dry = run([*args, "--dry-run"])
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertEqual(mandate.read_bytes(), before)
        run(args)
        body = sections(mandate.read_text(encoding="utf-8"))["## Instancia en curso"]
        written = "\n".join(line for line in body.splitlines() if line.strip())
        self.assertEqual(dry.stdout.strip("\n"), written)

    def test_release_de_un_lock_ya_vacio_sale_4(self) -> None:
        """CA-11c."""
        mandate = self.copy(FREE_MANDATE_TEXT)
        before = mandate.read_bytes()
        completed = run(["release", str(mandate), "--session", SESSION])
        self.assertEqual(completed.returncode, 4)
        self.assertEqual(mandate.read_bytes(), before)

    def test_release_de_un_lock_de_otra_sesion_sale_5_sin_tocar_nada(self) -> None:
        """CA-11c: un lock huérfano de otra sesión lo libera el autor del mandato,
        no este script (§ 1.1 de `sdd-supervisado`)."""
        mandate = self.copy(BUSY_MANDATE_TEXT)
        before = mandate.read_bytes()
        completed = run(["release", str(mandate), "--session", SESSION])
        self.assertEqual(completed.returncode, 5)
        self.assertEqual(mandate.read_bytes(), before)
        self.assertEqual(run(["inspect", str(mandate)]).returncode, 3)

    # --- header contract -------------------------------------------------------------

    def test_la_cabecera_documenta_los_tres_subcomandos_y_sus_codigos(self) -> None:
        """CA-10: header documents usage, `--dry-run` and every exit code, and says
        which subcommand is the read-only one."""
        header = instance_lock.__doc__ or ""
        for token in ("inspect", "acquire", "release", "--dry-run", "read-only"):
            self.assertIn(token, header)
        for code in (0, 1, 2, 3, 4, 5):
            self.assertRegex(header, rf"(?m)^\s+{code}\s")


if __name__ == "__main__":
    unittest.main()
