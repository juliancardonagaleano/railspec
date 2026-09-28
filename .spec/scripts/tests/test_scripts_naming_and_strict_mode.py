"""Transversal tests over the nine 0114 executables (unit 0114, G4): CA-10 (naming
of the eight rituals-and-hooks, and Python strict mode by `ast`) and CA-22a (no
network in the two hooks, same mechanism as CA-21 of `0113`).

`preflight.py` is the **ninth** executable: CA-10's naming-suffix check runs over
the **eight** the spec names (six rituals + two hooks) and does not include it
(`plan.md` § Enfoque); its own strict-mode checks (i)-(iii) and its header are
still verified here, and its "uncontrolled exception" contract (check iv) is its own
`preflight-error` scheme, not the rituals' `internal-error` one — kept as a
separate assertion, not folded into `RitualInternalErrorTests`.
"""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS.parents[1]

# --- The eight names CA-10 checks for forbidden suffixes (six rituals + two hooks) --
NAMED_FILES = [
    "record_mandate_validation.py",
    "instance_lock.py",
    "report_git_sync.py",
    "append_changelog_line.py",
    "snapshot_evidence.sh",
    "mandate_anchor.py",
    "guard_written_state_shape.py",
    "guard_bash_spec_writes.py",
]

PY_RITUALS = [
    "record_mandate_validation.py",
    "instance_lock.py",
    "report_git_sync.py",
    "append_changelog_line.py",
    "mandate_anchor.py",
]
BASH_RITUAL = "snapshot_evidence.sh"
HOOKS = ["guard_written_state_shape.py", "guard_bash_spec_writes.py"]
PREFLIGHT = "preflight.py"

# `pol-dev-nombramiento-semantico` R5, any capitalization.
FORBIDDEN_SUFFIXES = ["Service", "Dto", "Helper", "Manager", "Util", "Common",
                      "Provider"]

# CA-22a: no network beyond the stdlib. Same allowlist `0113`'s CA-21 already used
# for its own hooks; `validate_mandate` is a **local** module import (not stdlib),
# added explicitly because `guard_written_state_shape.py` imports `read_unit_state`
# from it (plan.md § Reutilización) — still zero network.
STDLIB_NETWORK_FREE_ALLOWLIST = {
    "sys", "os", "json", "re", "shlex", "pathlib", "subprocess", "datetime",
    "time", "traceback", "argparse", "hashlib", "tempfile", "contextlib",
}
LOCAL_MODULE_ALLOWLIST = {"validate_mandate", "_common"}
FORBIDDEN_NETWORK_MODULES = {"socket", "urllib", "http", "ssl", "asyncio",
                             "requests", "httpx"}


def _read(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def _parse(name: str) -> ast.Module:
    return ast.parse(_read(name), filename=name)


def _stem_tokens(name: str) -> list[str]:
    stem = Path(name).stem
    return re.split(r"[_\-]", stem)


# ======================================================================================
# CA-10 — naming: no forbidden suffix as the main identifier, on the eight names
# ======================================================================================

class NamingTests(unittest.TestCase):

    def test_los_ocho_nombres_existen(self) -> None:
        for name in NAMED_FILES:
            with self.subTest(name=name):
                self.assertTrue((SCRIPTS / name).is_file(), name)

    def test_ninguno_usa_un_sufijo_prohibido_como_identificador_principal(self) -> None:
        forbidden_lower = {s.lower() for s in FORBIDDEN_SUFFIXES}
        for name in NAMED_FILES:
            with self.subTest(name=name):
                tokens = {t.lower() for t in _stem_tokens(name)}
                self.assertFalse(tokens & forbidden_lower,
                                 f"{name} usa un sufijo prohibido (R5)")
                stem_lower = Path(name).stem.lower()
                for suffix in forbidden_lower:
                    self.assertFalse(stem_lower.endswith(suffix),
                                     f"{name} termina en «{suffix}»")

    def test_preflight_no_es_parte_de_la_lista_de_ocho(self) -> None:
        """`preflight.py` es el noveno ejecutable; CA-10 aplica la suite de nombres
        a los ocho que nombra el spec, no a él (`plan.md` § Enfoque)."""
        self.assertNotIn(PREFLIGHT, NAMED_FILES)
        self.assertTrue((SCRIPTS / PREFLIGHT).is_file())


# ======================================================================================
# CA-10 (i)-(iii) — Python strict mode, by `ast`, over every Python executable
# (the five rituals, the two hooks, and `preflight.py`)
# ======================================================================================

ALL_PYTHON_EXECUTABLES = PY_RITUALS + HOOKS + [PREFLIGHT]


class BareExceptTests(unittest.TestCase):
    """(i) No bare `except:` and no `except Exception: pass` anywhere."""

    def test_ningun_except_desnudo_ni_except_exception_pass(self) -> None:
        for name in ALL_PYTHON_EXECUTABLES:
            with self.subTest(name=name):
                tree = _parse(name)
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ExceptHandler):
                        continue
                    self.assertIsNotNone(
                        node.type, f"{name}: except desnudo en la línea {node.lineno}")
                    is_bare_exception = (
                        isinstance(node.type, ast.Name)
                        and node.type.id == "Exception"
                        and len(node.body) == 1
                        and isinstance(node.body[0], ast.Pass))
                    self.assertFalse(
                        is_bare_exception,
                        f"{name}: `except Exception: pass` en la línea {node.lineno}")


class SubprocessRunCheckedTests(unittest.TestCase):
    """(ii) Every `subprocess.run` call carries `check=True`, or the `.returncode`
    of what it returns is inspected explicitly somewhere in the same function."""

    @staticmethod
    def _is_subprocess_run(node: ast.AST) -> bool:
        return (isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute)
               and node.func.attr == "run"
               and isinstance(node.func.value, ast.Name)
               and node.func.value.id == "subprocess")

    @staticmethod
    def _has_check_true(call: ast.Call) -> bool:
        for kw in call.keywords:
            if kw.arg == "check" and isinstance(kw.value, ast.Constant) \
                  and kw.value.value is True:
                return True
        return False

    def _functions_of(self, tree: ast.Module) -> list[ast.AST]:
        return [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def test_cada_llamada_lleva_check_o_su_returncode_se_inspecciona(self) -> None:
        for name in ALL_PYTHON_EXECUTABLES:
            with self.subTest(name=name):
                tree = _parse(name)
                for func in self._functions_of(tree):
                    # First pass: every `subprocess.run(...)` that is the direct
                    # value of a `x = subprocess.run(...)` assignment to a plain
                    # name — recorded by the *identity* of the call node, so a
                    # second, node-level walk can tell "assigned" from "bare"
                    # without needing parent pointers (`ast` does not carry them).
                    assigned_call_ids: dict[int, str | None] = {}
                    returned_call_ids: set[int] = set()
                    for node in ast.walk(func):
                        if isinstance(node, ast.Assign) and \
                              self._is_subprocess_run(node.value):
                            target = node.targets[0]
                            varname = target.id if isinstance(target, ast.Name) \
                                else None
                            assigned_call_ids[id(node.value)] = varname
                        elif isinstance(node, ast.Return) and node.value is not None \
                              and self._is_subprocess_run(node.value):
                            # A thin wrapper (`run_python`, `git`) that hands the
                            # `CompletedProcess` straight to its caller — the
                            # caller is where `.returncode` gets inspected, out of
                            # this function's own scope by construction.
                            returned_call_ids.add(id(node.value))

                    for node in ast.walk(func):
                        if not self._is_subprocess_run(node):
                            continue
                        if self._has_check_true(node) or id(node) in returned_call_ids:
                            continue
                        varname = assigned_call_ids.get(id(node))
                        if id(node) not in assigned_call_ids:
                            self.fail(
                                f"{name}:{node.lineno} subprocess.run sin "
                                "check=True y sin variable para inspeccionar "
                                "returncode (llamada no asignada a un nombre)")
                        self.assertIsNotNone(
                            varname,
                            f"{name}:{node.lineno} subprocess.run sin check=True, "
                            "asignado a un destino que no es un nombre simple")
                        inspected = any(
                            isinstance(n, ast.Attribute) and n.attr == "returncode"
                            and isinstance(n.value, ast.Name)
                            and n.value.id == varname
                            for n in ast.walk(func))
                        self.assertTrue(
                            inspected,
                            f"{name}:{node.lineno} subprocess.run asignado a "
                            f"'{varname}' pero su returncode no se inspecciona")


class SysExitDocumentedTests(unittest.TestCase):
    """(iii) Every `sys.exit()` receives one of the codes documented in the
    module's own header (or chains `main()`, whose own `sys.exit` calls are
    checked the same way)."""

    @staticmethod
    def _documented_codes(header: str) -> set[int]:
        """Two header styles coexist: the rituals/preflight list one code per
        line (`^\\s+<n>\\s`, same convention `test_instance_lock.py` etc. already
        check); the two hooks always exit `0` and say so in prose (`` `EXIT_OK`
        (0), always``). Both are "documented", so both patterns count."""
        codes = {int(m.group(1)) for m in re.finditer(r"(?m)^\s+(\d+)\s", header)}
        codes |= {int(m.group(1)) for m in re.finditer(r"\((\d+)\)", header)}
        return codes

    def test_cada_sys_exit_recibe_un_codigo_documentado(self) -> None:
        for name in ALL_PYTHON_EXECUTABLES:
            with self.subTest(name=name):
                tree = _parse(name)
                header = ast.get_docstring(tree) or ""
                documented = self._documented_codes(header)
                self.assertTrue(documented, f"{name}: cabecera sin códigos listados")
                for node in ast.walk(tree):
                    if not (isinstance(node, ast.Call)
                           and isinstance(node.func, ast.Attribute)
                           and node.func.attr == "exit"
                           and isinstance(node.func.value, ast.Name)
                           and node.func.value.id == "sys"):
                        continue
                    if not node.args:
                        continue  # `sys.exit()` bare — no code to check
                    arg = node.args[0]
                    if isinstance(arg, ast.Call):
                        continue  # `sys.exit(main())` — main()'s own exits are checked
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                        self.assertIn(
                            arg.value, documented,
                            f"{name}:{node.lineno} sys.exit({arg.value}) no está "
                            "en la cabecera")
                    # A `Name` (a variable holding a previously-validated exit code,
                    # e.g. `error.code`) is out of ast's reach without dataflow —
                    # accepted here; `EXIT_*` constants are covered by the
                    # `RitualInternalErrorTests`/header tests of each script's own
                    # test file, which run every code path.


# ======================================================================================
# CA-10 (iv) — uncontrolled exception ends with a documented internal-failure code,
# never a bare traceback. Preflight and the five rituals only (its own scheme is
# `preflight-error`; the rituals' is `internal-error: <motivo>`); the two hooks have
# a *different*, already-tested contract (CA-22c: fail-open, `guard-failures.log`).
# ======================================================================================

class RitualInternalErrorTests(unittest.TestCase):

    def test_cada_ritual_documenta_y_usa_internal_error(self) -> None:
        for name in PY_RITUALS:
            with self.subTest(name=name):
                text = _read(name)
                self.assertIn("internal-error", text,
                             f"{name}: no documenta/usa el literal internal-error")
                self.assertIn("except Exception", text)

    def test_preflight_documenta_y_usa_preflight_error(self) -> None:
        text = _read(PREFLIGHT)
        self.assertIn("preflight-error", text)
        self.assertIn("except Exception", text)


# ======================================================================================
# CA-22a — the two hooks make no network call: same mechanism as CA-21 of `0113`
# ======================================================================================

class NoNetworkTests(unittest.TestCase):

    def test_los_dos_hooks_no_importan_nada_de_red(self) -> None:
        stdlib_names = getattr(sys, "stdlib_module_names", frozenset())
        for name in HOOKS:
            with self.subTest(name=name):
                tree = _parse(name)
                imported: set[str] = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imported.add(alias.name.split(".")[0])
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imported.add(node.module.split(".")[0])
                imported.discard("__future__")

                forbidden = imported & FORBIDDEN_NETWORK_MODULES
                self.assertFalse(forbidden,
                                 f"{name} importa módulos de red: {forbidden}")

                self.assertNotIn("usage", imported,
                                 f"{name} importa del paquete usage/, prohibido "
                                 "para los hooks")

                # Every remaining import is either the local-module allowlist
                # (`validate_mandate`, `_common` — CA-22a explicitly permits these,
                # they carry no network of their own) or plain standard library.
                unknown = imported - LOCAL_MODULE_ALLOWLIST - stdlib_names
                self.assertFalse(
                    unknown,
                    f"{name} importa algo fuera de la biblioteca estándar y de "
                    f"la allowlist local: {unknown}")
                # And, redundantly but explicitly (CA-22a's own closed list): every
                # non-local import is inside the documented allowlist too.
                non_local = imported - LOCAL_MODULE_ALLOWLIST
                self.assertTrue(
                    non_local <= STDLIB_NETWORK_FREE_ALLOWLIST,
                    f"{name} importa algo fuera del allowlist documentado: "
                    f"{non_local - STDLIB_NETWORK_FREE_ALLOWLIST}")

    def test_ninguno_importa_nada_bajo_usage(self) -> None:
        for name in HOOKS:
            with self.subTest(name=name):
                text = _read(name)
                self.assertNotRegex(text, r"(?m)^\s*(import|from)\s+usage\b")


# ======================================================================================
# Bash strict mode for the one bash ritual (`set -euo pipefail`)
# ======================================================================================

class BashStrictModeTests(unittest.TestCase):

    def test_snapshot_evidence_declara_set_euo_pipefail(self) -> None:
        text = _read(BASH_RITUAL)
        self.assertRegex(text, r"(?m)^set -euo pipefail\s*$")


if __name__ == "__main__":
    unittest.main()
