"""Diagnose the local agentic toolchain (the MCP servers this repository
depends on, plus the local graph index) and, only on explicit confirmation,
remediate one degraded subject at a time.

Originally `scripts/kit_doctor.py`; moved verbatim to `installer/doctor.py`
(unit 0006) so that `installer/cli.py doctor` and the legacy shim at
`scripts/kit_doctor.py` both reach the same ``main`` function — see CA-02
and CA-13.

Two code paths, never calling each other:

- Diagnosis (read-only): a `check_*` function per row, each calling the
  injected runner and translating its result into a `Row`. Nothing here
  ever writes anything.
- Remediation (write, gated): a `remediate_*` function per remediable
  subject, reachable only from `main()`, and only after an explicit
  confirmation callable has returned `True`.

Both the subprocess runner and the confirmation callable are injected so
this module is fully testable without touching a real MCP server, a real
package manager, or a real terminal. Any failure raised by the runner
(missing binary, timeout, anything else) is caught per call and turned into
`state=unknown` — this module never lets an uncontrolled exception escape a
diagnostic call.
"""

import argparse
import json
import subprocess
import sys

DEFAULT_TIMEOUT_SECONDS = 30
FRESHNESS_THRESHOLD = 10

CONNECTION_SUBJECTS = ("pce-mcp", "gitnexus", "codebase-memory-mcp")

# The command cited in a report row is the contract this tool reports
# against, not necessarily the literal argv used to reach that answer (the
# graph-index freshness row is the clearest case: the contract names the
# `gitnexus_list_repos` tool, while this script reaches the same answer
# through the local CLI — see check_graph_freshness).
COMMAND_CITATIONS = {
    ("pce-mcp", "connection"): "claude mcp list",
    ("gitnexus", "connection"): "claude mcp list",
    ("codebase-memory-mcp", "connection"): "claude mcp list",
    ("pce-mcp", "install"): "test -f scripts/mcp-pce.sh",
    ("gitnexus", "install"): "npm ls -g --depth=0 gitnexus",
    ("codebase-memory-mcp", "install"): "command -v codebase-memory-mcp",
    ("graph-index", "freshness"): "gitnexus_list_repos",
}

GITNEXUS_INSTALL_COMMAND = "npm install -g gitnexus"
REINDEX_COMMAND = "node .gitnexus/run.cjs analyze --index-only"
CODEBASE_MEMORY_MCP_INSTALLER_URL = (
    "https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/install.sh"
)
CODEBASE_MEMORY_MCP_INSTALL_COMMAND = (
    "curl -fsSL " + CODEBASE_MEMORY_MCP_INSTALLER_URL + " | bash -s -- --skip-config"
)


class Row:
    """One line of the observable report contract."""

    __slots__ = ("subject", "check", "state", "command", "commits_behind", "threshold")

    def __init__(self, subject, check, state, command, commits_behind=None, threshold=None):
        self.subject = subject
        self.check = check
        self.state = state
        self.command = command
        self.commits_behind = commits_behind
        self.threshold = threshold


def default_runner(argv):
    """Real subprocess runner: the default `run` used outside of tests."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=DEFAULT_TIMEOUT_SECONDS)


def safe_run(run, argv):
    """Call the injected runner and never let it raise.

    Any exception (missing binary, timeout, anything else the runner can
    raise) is swallowed here and reported as `None`, which every caller in
    this module treats as `state=unknown` for that row.
    """
    try:
        return run(argv)
    except Exception:
        return None


def read_confirmation(prompt):
    """Default confirmation callable: only a real, interactive TTY answer
    counts. A non-interactive stdin (pipe, redirect, or no stdin at all)
    never counts as an explicit confirmation.
    """
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(prompt)
    except (EOFError, OSError):
        return False
    return answer.strip().lower() in ("y", "yes", "si", "sí")


# --- argv builders (diagnosis) ---------------------------------------------


def build_connection_argv():
    return ["claude", "mcp", "list"]


def build_install_pce_mcp_argv():
    return ["test", "-f", "scripts/mcp-pce.sh"]


def build_install_gitnexus_argv():
    return ["npm", "ls", "-g", "--depth=0", "gitnexus"]


def build_install_codebase_memory_mcp_argv():
    # "command" is a shell builtin, not a standalone executable: it needs a
    # shell to run it, hence the bash wrapper. The row still cites the
    # conceptual command from COMMAND_CITATIONS.
    return ["bash", "-lc", "command -v codebase-memory-mcp"]


def build_freshness_status_argv():
    return ["node", ".gitnexus/run.cjs", "status", "--json"]


def build_commits_behind_argv(from_commit, to_commit):
    return ["git", "rev-list", "--count", "{}..{}".format(from_commit, to_commit)]


# --- diagnosis (read-only) --------------------------------------------------


# Both plain and heavy variants of check/cross marks show up across client
# versions (e.g. U+2713/U+2714 for a check, U+2717/U+2718 for a cross).
CONNECTION_OK_SYMBOLS = ("✓", "✔")
CONNECTION_FAIL_SYMBOLS = ("✗", "✘")


def parse_connection_state(output, subject):
    for line in output.splitlines():
        if subject not in line:
            continue
        lowered = line.lower()
        has_fail_symbol = any(symbol in line for symbol in CONNECTION_FAIL_SYMBOLS)
        has_ok_symbol = any(symbol in line for symbol in CONNECTION_OK_SYMBOLS)
        if has_fail_symbol or "disconnected" in lowered or "fail" in lowered:
            return "not_responding"
        if has_ok_symbol or "connected" in lowered:
            return "responding"
        return "unknown"
    return "not_declared"


def check_connections(run):
    result = safe_run(run, build_connection_argv())
    rows = []
    for subject in CONNECTION_SUBJECTS:
        citation = COMMAND_CITATIONS[(subject, "connection")]
        if result is None or result.returncode != 0:
            state = "unknown"
        else:
            state = parse_connection_state(result.stdout or "", subject)
        rows.append(Row(subject, "connection", state, citation))
    return rows


def _install_state_from_result(result):
    if result is None:
        return "unknown"
    if result.returncode == 0:
        return "present"
    if result.returncode == 1:
        return "absent"
    return "unknown"


def check_install_pce_mcp(run):
    result = safe_run(run, build_install_pce_mcp_argv())
    citation = COMMAND_CITATIONS[("pce-mcp", "install")]
    return Row("pce-mcp", "install", _install_state_from_result(result), citation)


def check_install_gitnexus(run):
    result = safe_run(run, build_install_gitnexus_argv())
    citation = COMMAND_CITATIONS[("gitnexus", "install")]
    return Row("gitnexus", "install", _install_state_from_result(result), citation)


def check_install_codebase_memory_mcp(run):
    result = safe_run(run, build_install_codebase_memory_mcp_argv())
    citation = COMMAND_CITATIONS[("codebase-memory-mcp", "install")]
    return Row("codebase-memory-mcp", "install", _install_state_from_result(result), citation)


def _unknown_freshness_row():
    citation = COMMAND_CITATIONS[("graph-index", "freshness")]
    return Row("graph-index", "freshness", "unknown", citation, commits_behind=0, threshold=FRESHNESS_THRESHOLD)


def check_graph_freshness(run, gitnexus_connection_state):
    """Freshness depends on the gitnexus connection: if that MCP server is
    not responding, freshness is reported unknown and reindexing is never
    offered — a not_responding server means the answer cannot be trusted,
    not that the index itself changed state.
    """
    citation = COMMAND_CITATIONS[("graph-index", "freshness")]
    if gitnexus_connection_state != "responding":
        return _unknown_freshness_row()

    status_result = safe_run(run, build_freshness_status_argv())
    if status_result is None or status_result.returncode != 0:
        return _unknown_freshness_row()

    try:
        payload = json.loads(status_result.stdout)
    except (ValueError, TypeError):
        return _unknown_freshness_row()

    if not isinstance(payload, dict):
        return _unknown_freshness_row()

    if payload.get("status") == "missing":
        return Row("graph-index", "freshness", "missing", citation, commits_behind=0, threshold=FRESHNESS_THRESHOLD)

    try:
        index_commit = payload["index"]["commit"]
        current_commit = payload["current"]["commit"]
    except (KeyError, TypeError):
        return _unknown_freshness_row()

    if not index_commit:
        return Row("graph-index", "freshness", "missing", citation, commits_behind=0, threshold=FRESHNESS_THRESHOLD)

    if index_commit == current_commit:
        commits_behind = 0
    else:
        count_result = safe_run(run, build_commits_behind_argv(index_commit, current_commit))
        if count_result is None or count_result.returncode != 0:
            return _unknown_freshness_row()
        try:
            commits_behind = int((count_result.stdout or "").strip())
        except ValueError:
            return _unknown_freshness_row()

    state = "stale" if commits_behind >= FRESHNESS_THRESHOLD else "fresh"
    return Row("graph-index", "freshness", state, citation, commits_behind=commits_behind, threshold=FRESHNESS_THRESHOLD)


def run_diagnostics(run):
    """Run the full, 7-row diagnostic pass. Read-only: never calls a
    remediate_* function.
    """
    connection_rows = check_connections(run)
    by_subject = {row.subject: row for row in connection_rows}

    rows = [
        by_subject["pce-mcp"],
        by_subject["gitnexus"],
        by_subject["codebase-memory-mcp"],
        check_install_pce_mcp(run),
        check_install_gitnexus(run),
        check_install_codebase_memory_mcp(run),
    ]
    rows.append(check_graph_freshness(run, by_subject["gitnexus"].state))
    return rows


# --- remediation (write, gated behind confirmation) -------------------------


class RemediationOutcome:
    """Result of one remediation attempt: whether it succeeded, plus the
    argv and stderr an operator would need to reproduce a failure by hand.
    """

    __slots__ = ("succeeded", "argv", "stderr")

    def __init__(self, succeeded, argv, stderr=""):
        self.succeeded = succeeded
        self.argv = argv
        self.stderr = stderr


def _remediation_outcome(run, argv):
    result = safe_run(run, argv)
    if result is None:
        return RemediationOutcome(False, argv, stderr="")
    return RemediationOutcome(result.returncode == 0, argv, stderr=result.stderr or "")


def remediate_gitnexus(run):
    return _remediation_outcome(run, GITNEXUS_INSTALL_COMMAND.split())


def remediate_codebase_memory_mcp(run):
    return _remediation_outcome(run, ["bash", "-c", CODEBASE_MEMORY_MCP_INSTALL_COMMAND])


def remediate_graph_index(run):
    return _remediation_outcome(run, REINDEX_COMMAND.split())


REMEDIATORS = {
    "gitnexus": remediate_gitnexus,
    "codebase-memory-mcp": remediate_codebase_memory_mcp,
    "graph-index": remediate_graph_index,
}


def refresh_row(rows, subject, run):
    """After a successful remediation, re-run only the diagnostic check that
    remediation is meant to fix, so the final report reflects reality
    instead of the pre-remediation snapshot.
    """
    if subject == "gitnexus":
        updated = check_install_gitnexus(run)
    elif subject == "codebase-memory-mcp":
        updated = check_install_codebase_memory_mcp(run)
    elif subject == "graph-index":
        gitnexus_state = next(row.state for row in rows if row.subject == "gitnexus" and row.check == "connection")
        updated = check_graph_freshness(run, gitnexus_state)
    else:
        return rows
    return [
        updated if (row.subject == updated.subject and row.check == updated.check) else row
        for row in rows
    ]


# --- reporting ---------------------------------------------------------------


def compute_exit_code(rows):
    states = [row.state for row in rows]
    if any(state == "unknown" for state in states):
        return 2
    if any(state in ("not_responding", "absent", "stale") for state in states):
        return 1
    return 0


def format_row(row):
    parts = [
        "subject={}".format(row.subject),
        "check={}".format(row.check),
        "state={}".format(row.state),
    ]
    if row.commits_behind is not None:
        parts.append("commits_behind={}".format(row.commits_behind))
    if row.threshold is not None:
        parts.append("threshold={}".format(row.threshold))
    parts.append("command={}".format(row.command))
    return " ".join(parts)


def build_reconnect_hints(rows):
    hints = []
    for row in rows:
        if row.check == "connection" and row.state == "not_responding":
            hints.append(
                "hint: {} is not responding right now; reconnect it from "
                "within the session via /mcp (this tool has no way to do "
                "that for you)".format(row.subject)
            )
    return hints


def print_report(rows):
    for row in rows:
        print(format_row(row))
    for hint in build_reconnect_hints(rows):
        print(hint)


# --- CLI -----------------------------------------------------------------


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="kit_doctor",
        description=(
            "Diagnose the local agentic toolchain (MCP servers and the "
            "graph index) and, only when explicitly confirmed, remediate a "
            "single degraded subject."
        ),
    )
    parser.add_argument(
        "--action",
        choices=sorted(list(REMEDIATORS) + ["pce-mcp"]),
        action="append",
        default=None,
        help=(
            "Request a write action for exactly one subject. Requires an "
            "explicit TTY confirmation in the same run; pce-mcp has no "
            "remediation available."
        ),
    )
    return parser.parse_args(argv)


def main(argv=None, run=None, confirm=None):
    run = run or default_runner
    confirm = confirm or read_confirmation

    args = parse_args(argv)
    rows = run_diagnostics(run)
    requested = args.action or []

    if len(requested) > 1:
        print_report(rows)
        return 3

    if not requested:
        print_report(rows)
        return compute_exit_code(rows)

    subject = requested[0]
    remediator = REMEDIATORS.get(subject)
    if remediator is None:
        print_report(rows)
        return 4

    if not confirm("Confirm write action for {}? [y/N]: ".format(subject)):
        print_report(rows)
        return 3

    outcome = remediator(run)
    if not outcome.succeeded:
        print_report(rows)
        print(
            "remediation failed: command={} stderr={}".format(
                " ".join(outcome.argv), outcome.stderr.strip()
            )
        )
        return 3

    rows = refresh_row(rows, subject, run)
    print_report(rows)
    return compute_exit_code(rows)
