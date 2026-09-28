"""Tests for `scripts/kit_doctor.py`: diagnosis of the local agentic
toolchain (the MCP servers this repository depends on, plus the local graph
index) and its gated, single-subject remediation.

Every test drives the module's public API directly (`run_diagnostics`,
`check_*`, `remediate_*`, `main`, ...) with a fake runner and, where needed,
a fake confirmation callable. Nothing here ever spawns a real subprocess,
touches the network, or reads/writes `~/.claude.json` or `.mcp.json` for
real: the module never opens those paths, so the configuration-integrity
tests assert on hashes of the files this repo happens to have on disk (or,
where that is not hermetic enough, on the absence of any write call).
"""

from __future__ import annotations

import hashlib
import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import kit_doctor  # noqa: E402


# --- fake runner infrastructure ----------------------------------------------


def make_result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeRunner:
    """Injectable runner: maps an argv tuple to a canned result or an
    exception, and records every call it received.
    """

    def __init__(self, responses=None, raises_for=None):
        self.responses = {tuple(k): v for k, v in (responses or {}).items()}
        self.raises_for = {tuple(k) for k in (raises_for or [])}
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        key = tuple(argv)
        if key in self.raises_for:
            raise OSError("simulated failure: {}".format(argv))
        if key in self.responses:
            return self.responses[key]
        return make_result(returncode=1, stdout="", stderr="")


def connected_output():
    return "\n".join(
        [
            "pce-mcp: connected ✓",
            "gitnexus: connected ✓",
            "codebase-memory-mcp: connected ✓",
        ]
    )


def fresh_status_payload(index_commit="abc123", current_commit="abc123"):
    return (
        '{"status": "ok", "index": {"commit": "%s"}, '
        '"current": {"commit": "%s"}}' % (index_commit, current_commit)
    )


def base_responses(connection_output=None, install_states=None, freshness_json=None,
                    commits_behind=0):
    """A runner response map covering all 7 rows with everything healthy,
    overridable one field at a time by the caller.
    """
    connection_output = connection_output if connection_output is not None else connected_output()
    install_states = install_states or {}

    responses = {
        tuple(kit_doctor.build_connection_argv()): make_result(0, connection_output),
        tuple(kit_doctor.build_install_pce_mcp_argv()):
            make_result(install_states.get("pce-mcp", 0)),
        tuple(kit_doctor.build_install_gitnexus_argv()):
            make_result(install_states.get("gitnexus", 0)),
        tuple(kit_doctor.build_install_codebase_memory_mcp_argv()):
            make_result(install_states.get("codebase-memory-mcp", 0)),
        tuple(kit_doctor.build_freshness_status_argv()):
            make_result(0, freshness_json if freshness_json is not None else fresh_status_payload()),
        tuple(kit_doctor.build_commits_behind_argv("abc123", "def456")):
            make_result(0, str(commits_behind)),
    }
    return responses


def healthy_runner(**overrides):
    return FakeRunner(responses=base_responses(**overrides))


def run_main(argv, runner, confirm=None):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = kit_doctor.main(argv, run=runner, confirm=confirm or (lambda prompt: False))
    return code, buf.getvalue()


# --- exit code precedence --------------------------------------------------


def test_exit_code_0_all_healthy():
    runner = healthy_runner()
    code, output = run_main([], runner)
    assert code == 0
    assert "state=unknown" not in output


def test_exit_code_1_degraded_row_no_unknown():
    runner = healthy_runner(install_states={"gitnexus": 1})
    code, _ = run_main([], runner)
    assert code == 1


def test_exit_code_2_unknown_row_present():
    runner = FakeRunner(responses=base_responses(), raises_for=[kit_doctor.build_connection_argv()])
    code, output = run_main([], runner)
    assert code == 2
    assert "state=unknown" in output


def test_exit_code_3_multiple_actions_requested():
    runner = healthy_runner()
    code, _ = run_main(["--action", "gitnexus", "--action", "graph-index"], runner)
    assert code == 3


def test_exit_code_4_unremediable_subject_requested():
    runner = healthy_runner()
    code, _ = run_main(["--action", "pce-mcp"], runner, confirm=lambda prompt: True)
    assert code == 4


# --- per-row diagnosis -------------------------------------------------------


def test_connection_pce_mcp_responding():
    runner = FakeRunner(responses={tuple(kit_doctor.build_connection_argv()): make_result(0, connected_output())})
    rows = kit_doctor.check_connections(runner.__call__)
    row = next(r for r in rows if r.subject == "pce-mcp")
    assert row.check == "connection"
    assert row.state == "responding"
    assert row.command == "claude mcp list"


def test_connection_gitnexus_not_responding():
    output = "pce-mcp: connected ✓\ngitnexus: disconnected ✗\ncodebase-memory-mcp: connected ✓"
    runner = FakeRunner(responses={tuple(kit_doctor.build_connection_argv()): make_result(0, output)})
    rows = kit_doctor.check_connections(runner.__call__)
    row = next(r for r in rows if r.subject == "gitnexus")
    assert row.state == "not_responding"


def test_connection_codebase_memory_mcp_not_declared():
    output = "pce-mcp: connected ✓\ngitnexus: connected ✓"
    runner = FakeRunner(responses={tuple(kit_doctor.build_connection_argv()): make_result(0, output)})
    rows = kit_doctor.check_connections(runner.__call__)
    row = next(r for r in rows if r.subject == "codebase-memory-mcp")
    assert row.state == "not_declared"


def test_connection_pce_mcp_unknown_on_runner_failure():
    runner = FakeRunner(raises_for=[kit_doctor.build_connection_argv()])
    rows = kit_doctor.check_connections(runner.__call__)
    for row in rows:
        assert row.state == "unknown"


def test_install_pce_mcp_present():
    runner = FakeRunner(responses={tuple(kit_doctor.build_install_pce_mcp_argv()): make_result(0)})
    row = kit_doctor.check_install_pce_mcp(runner.__call__)
    assert row.subject == "pce-mcp"
    assert row.check == "install"
    assert row.state == "present"
    assert row.command == "test -f scripts/mcp-pce.sh"


def test_install_gitnexus_absent():
    runner = FakeRunner(responses={tuple(kit_doctor.build_install_gitnexus_argv()): make_result(1)})
    row = kit_doctor.check_install_gitnexus(runner.__call__)
    assert row.state == "absent"
    assert row.command == "npm ls -g --depth=0 gitnexus"


def test_install_codebase_memory_mcp_unknown_on_ambiguous_returncode():
    runner = FakeRunner(responses={tuple(kit_doctor.build_install_codebase_memory_mcp_argv()): make_result(127)})
    row = kit_doctor.check_install_codebase_memory_mcp(runner.__call__)
    assert row.state == "unknown"
    assert row.command == "command -v codebase-memory-mcp"


def test_graph_freshness_fresh_when_commits_match():
    runner = FakeRunner(responses={
        tuple(kit_doctor.build_freshness_status_argv()): make_result(0, fresh_status_payload("abc123", "abc123")),
    })
    row = kit_doctor.check_graph_freshness(runner.__call__, "responding")
    assert row.subject == "graph-index"
    assert row.check == "freshness"
    assert row.state == "fresh"
    assert row.commits_behind == 0
    assert row.threshold == kit_doctor.FRESHNESS_THRESHOLD
    assert row.command == "gitnexus_list_repos"


def test_graph_freshness_stale_when_commits_behind_at_threshold():
    payload = fresh_status_payload("abc123", "def456")
    runner = FakeRunner(responses={
        tuple(kit_doctor.build_freshness_status_argv()): make_result(0, payload),
        tuple(kit_doctor.build_commits_behind_argv("abc123", "def456")): make_result(0, "10"),
    })
    row = kit_doctor.check_graph_freshness(runner.__call__, "responding")
    assert row.state == "stale"
    assert row.commits_behind == 10


def test_graph_freshness_missing_when_no_index_commit():
    payload = '{"status": "ok", "index": {"commit": null}, "current": {"commit": "abc123"}}'
    runner = FakeRunner(responses={
        tuple(kit_doctor.build_freshness_status_argv()): make_result(0, payload),
    })
    row = kit_doctor.check_graph_freshness(runner.__call__, "responding")
    assert row.state == "missing"


def test_command_citations_match_spec_table():
    assert kit_doctor.COMMAND_CITATIONS[("pce-mcp", "connection")] == "claude mcp list"
    assert kit_doctor.COMMAND_CITATIONS[("pce-mcp", "install")] == "test -f scripts/mcp-pce.sh"
    assert kit_doctor.COMMAND_CITATIONS[("gitnexus", "install")] == "npm ls -g --depth=0 gitnexus"
    assert kit_doctor.COMMAND_CITATIONS[("codebase-memory-mcp", "install")] == "command -v codebase-memory-mcp"
    assert kit_doctor.COMMAND_CITATIONS[("graph-index", "freshness")] == "gitnexus_list_repos"


def test_run_diagnostics_produces_seven_rows_with_unique_subject_check_pairs():
    runner = healthy_runner()
    rows = kit_doctor.run_diagnostics(runner.__call__)
    assert len(rows) == 7
    pairs = {(row.subject, row.check) for row in rows}
    assert len(pairs) == 7
    subjects = {row.subject for row in rows}
    assert subjects == {"pce-mcp", "gitnexus", "codebase-memory-mcp", "graph-index"}


def test_report_field_counts_match_spec_grep_contract():
    runner = healthy_runner()
    rows = kit_doctor.run_diagnostics(runner.__call__)
    report = "\n".join(kit_doctor.format_row(row) for row in rows)
    assert report.count("subject=") == 7
    assert sum(1 for line in report.splitlines() if "check=connection state=" in line) == 3
    assert sum(1 for line in report.splitlines() if "check=install" in line) == 3
    assert sum(1 for line in report.splitlines() if "check=freshness" in line) == 1
    for subject in ("pce-mcp", "gitnexus", "codebase-memory-mcp", "graph-index"):
        assert "subject={} ".format(subject) in report + " "


# --- remediation --------------------------------------------------------------


def test_remediate_gitnexus_uses_npm_install_dash_g():
    runner = FakeRunner(responses={
        ("npm", "install", "-g", "gitnexus"): make_result(0),
    })
    outcome = kit_doctor.remediate_gitnexus(runner.__call__)
    assert outcome.succeeded is True
    assert runner.calls == [["npm", "install", "-g", "gitnexus"]]


def test_remediate_gitnexus_reports_failure_on_nonzero_returncode():
    runner = FakeRunner(responses={
        ("npm", "install", "-g", "gitnexus"): make_result(1, "", "network error"),
    })
    outcome = kit_doctor.remediate_gitnexus(runner.__call__)
    assert outcome.succeeded is False
    assert outcome.stderr == "network error"


def test_remediate_gitnexus_permission_error_reports_stderr_verbatim():
    runner = FakeRunner(responses={
        ("npm", "install", "-g", "gitnexus"): make_result(243, "", "EACCES: permission denied"),
    })
    code, output = run_main(
        ["--action", "gitnexus"], runner, confirm=lambda prompt: True
    )
    assert code == 3
    assert "EACCES: permission denied" in output
    assert "npm install -g gitnexus" in output


def test_remediate_codebase_memory_mcp_argv_has_skip_config():
    runner = FakeRunner()
    kit_doctor.remediate_codebase_memory_mcp(runner.__call__)
    assert len(runner.calls) == 1
    argv = runner.calls[0]
    joined = " ".join(argv)
    assert "--skip-config" in joined
    assert "--clients" not in joined


def test_remediate_pce_mcp_always_returns_exit_code_4_and_never_runs():
    before = [file_hash(p) for p in CONFIG_PATHS]
    runner = healthy_runner()
    code, _ = run_main(["--action", "pce-mcp"], runner, confirm=lambda prompt: True)
    after = [file_hash(p) for p in CONFIG_PATHS]
    assert code == 4
    assert before == after
    # Only the 6 diagnostic-runner argvs (the 7th row, freshness, is skipped
    # unless the gitnexus commits-behind check fires) were ever invoked --
    # no write-shaped argv (npm/bash install, reindex) was requested for
    # a subject that has no remediator.
    write_shaped_calls = [
        c for c in runner.calls
        if list(c) == ["npm", "install", "-g", "gitnexus"]
        or list(c) == ["bash", "-c", kit_doctor.CODEBASE_MEMORY_MCP_INSTALL_COMMAND]
        or list(c) == kit_doctor.REINDEX_COMMAND.split()
    ]
    assert write_shaped_calls == []


def test_remediate_graph_index_uses_reindex_command():
    responses = base_responses()
    responses[("node", ".gitnexus/run.cjs", "analyze", "--index-only")] = make_result(0)
    runner = FakeRunner(responses=responses)
    outcome = kit_doctor.remediate_graph_index(runner.__call__)
    assert outcome.succeeded is True
    assert ["node", ".gitnexus/run.cjs", "analyze", "--index-only"] in runner.calls


# --- configuration integrity ---------------------------------------------------


def file_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


CONFIG_PATHS = [
    Path.home() / ".claude.json",
    Path(__file__).resolve().parents[3] / ".mcp.json",
]


def test_diagnosis_only_run_leaves_config_files_byte_identical():
    before = [file_hash(p) for p in CONFIG_PATHS]
    runner = healthy_runner()
    run_main([], runner)
    after = [file_hash(p) for p in CONFIG_PATHS]
    assert before == after


def test_remediate_gitnexus_confirmed_leaves_config_files_byte_identical():
    before = [file_hash(p) for p in CONFIG_PATHS]
    responses = base_responses()
    responses[("npm", "install", "-g", "gitnexus")] = make_result(0)
    responses[tuple(kit_doctor.build_install_gitnexus_argv())] = make_result(0)
    runner = FakeRunner(responses=responses)
    run_main(["--action", "gitnexus"], runner, confirm=lambda prompt: True)
    after = [file_hash(p) for p in CONFIG_PATHS]
    assert before == after


def test_remediate_codebase_memory_mcp_confirmed_leaves_config_files_byte_identical():
    before = [file_hash(p) for p in CONFIG_PATHS]
    responses = base_responses()
    runner = FakeRunner(responses=responses)
    run_main(["--action", "codebase-memory-mcp"], runner, confirm=lambda prompt: True)
    after = [file_hash(p) for p in CONFIG_PATHS]
    assert before == after


def test_requested_action_without_confirmation_leaves_config_files_byte_identical():
    before = [file_hash(p) for p in CONFIG_PATHS]
    runner = healthy_runner()
    code, _ = run_main(["--action", "gitnexus"], runner, confirm=lambda prompt: False)
    after = [file_hash(p) for p in CONFIG_PATHS]
    assert code == 3
    assert before == after


@pytest.mark.parametrize("subject", ["codebase-memory-mcp", "graph-index"])
def test_requested_action_without_confirmation_exits_3_for_every_remediable_subject(subject):
    before = [file_hash(p) for p in CONFIG_PATHS]
    runner = healthy_runner()
    code, _ = run_main(["--action", subject], runner, confirm=lambda prompt: False)
    after = [file_hash(p) for p in CONFIG_PATHS]
    assert code == 3
    assert before == after


def test_module_source_never_opens_config_paths_for_writing():
    source = Path(kit_doctor.__file__).read_text()
    assert ".claude.json" not in source
    assert '".mcp.json"' not in source
    assert "open(" not in source


# --- reconnect hints and freshness-under-degraded-connection -----------------


def test_reconnect_hint_names_mcp_slash_command_when_not_responding():
    output = "pce-mcp: connected ✓\ngitnexus: disconnected ✗\ncodebase-memory-mcp: connected ✓"
    runner = FakeRunner(responses=base_responses(connection_output=output))
    code, report = run_main([], runner)
    assert "/mcp" in report


def test_freshness_unknown_when_gitnexus_not_responding_and_reindex_not_offered():
    output = "pce-mcp: connected ✓\ngitnexus: disconnected ✗\ncodebase-memory-mcp: connected ✓"
    runner = FakeRunner(responses=base_responses(connection_output=output))
    rows = kit_doctor.run_diagnostics(runner.__call__)
    freshness_row = next(r for r in rows if r.subject == "graph-index")
    assert freshness_row.state == "unknown"
    assert tuple(kit_doctor.build_freshness_status_argv()) not in [tuple(c) for c in runner.calls]
    reindex_calls = [c for c in runner.calls if list(c) == kit_doctor.REINDEX_COMMAND.split()]
    assert reindex_calls == []


# --- remediation-surface verification -----------------------------------------


def test_source_never_names_mcp_restart():
    source = Path(kit_doctor.__file__).read_text()
    assert "mcp restart" not in source


def test_source_has_no_brew_install_or_upgrade():
    source = Path(kit_doctor.__file__).read_text()
    assert "brew install" not in source
    assert "brew upgrade" not in source


def test_source_has_skip_config_for_codebase_memory_mcp():
    source = Path(kit_doctor.__file__).read_text()
    assert "--skip-config" in source


def test_source_has_npm_install_dash_g_gitnexus():
    source = Path(kit_doctor.__file__).read_text()
    assert "npm install -g gitnexus" in source


def test_source_cites_codebase_memory_mcp_repository():
    source = Path(kit_doctor.__file__).read_text()
    assert "DeusData/codebase-memory-mcp" in source
