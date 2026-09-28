"""Installation and verification tests that exercise sdd-kit's writing
operation against disposable git targets it creates itself — never against
this repository. Every test below that touches the install operation
creates its own temporary target with `git init` under pytest's `tmp_path`
and lets pytest tear it down; none passes this repository's own root as
`--target` to an install. The verify operation is the sole exception: two
tests below exercise it against this repository (implicit and explicit
target) and against a throwaway copy of it — safe because a separate test
already establishes that verify never writes anything.

Does not recreate test_manifest.py (direct assertions on the loaded
manifest), test_cli.py (help output) or test_isolation.py (grep over the
implementation) — it complements them."""

from __future__ import annotations

import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SDD_KIT_DIR = REPO_ROOT / "installer"
CLI_PATH = SDD_KIT_DIR / "cli.py"

sys.path.insert(0, str(SDD_KIT_DIR))

import installer  # noqa: E402
import verifier  # noqa: E402
from git_target import resolve_git_dir, resolve_git_root  # noqa: E402
from manifest import DEFAULT_MANIFEST_PATH, load_and_validate  # noqa: E402


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def _listing(root: Path) -> dict[str, bytes]:
    """Recursive file listing (relative path -> content), excluding .git/."""
    listing: dict[str, bytes] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel == ".git" or rel.startswith(".git/"):
            continue
        listing[rel] = path.read_bytes()
    return listing


def _payload_pairs() -> list[tuple[Path, str]]:
    entries = load_and_validate()
    return verifier.iter_payload_files(entries)


def _flip_one_byte(data: bytes) -> bytes:
    if not data:
        return b"x"
    return bytes([data[0] ^ 0xFF]) + data[1:]


# ------------------------------------------------------- verify, no sibling


def test_ca03_verify_works_against_a_standalone_copy(tmp_path: Path) -> None:
    """Copies this repository's tree into an exclusive parent directory —
    no sibling checkout present — and invokes verify there with no
    ``--target``: sdd-kit must work without any sibling directory."""
    isolated_parent = tmp_path / "isolated-parent"
    copy_root = isolated_parent / "repo-copy"

    def _copy() -> None:
        shutil.copytree(
            REPO_ROOT,
            copy_root,
            # symlinks=True: copy symlinks as symlinks instead of following
            # them — this repo has one under .claude/worktrees/ pointing at
            # a sibling checkout outside this tree, and dereferencing it
            # would copy that whole other repository.
            symlinks=True,
            ignore=shutil.ignore_patterns(".git", "node_modules", ".angular", "__pycache__"),
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_copy)
        try:
            future.result(timeout=30)
        except FutureTimeoutError:
            pytest.fail("copiar el árbol del repositorio tardó más de 30s")

    # Invoke the copy's own cli.py, not the fixed module-level CLI_PATH:
    # REPO_ROOT is derived from __file__ inside manifest.py, so running the
    # copy's script (not the real repo's) is what actually makes this test
    # exercise the standalone tree instead of always passing against the
    # real repository regardless of what _copy() produced.
    copy_cli = copy_root / "installer" / "cli.py"
    result = subprocess.run(
        [sys.executable, str(copy_cli)],
        cwd=copy_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# -------------------------------------------------- implicit vs. explicit


def test_ca09_implicit_and_explicit_target_produce_identical_stdout() -> None:
    implicit = _run_cli([], cwd=REPO_ROOT)
    explicit = _run_cli(["--target", str(REPO_ROOT)], cwd=REPO_ROOT)
    assert implicit.returncode == explicit.returncode
    assert implicit.stdout == explicit.stdout


# ------------------------------------------------- fresh install + mirrors


def test_ca10_fresh_target_receives_exactly_the_payload_and_mirrors(tmp_path: Path) -> None:
    target = tmp_path / "fresh-target"
    _git_init(target)

    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr

    pairs = _payload_pairs()
    manifest_dests = {dest for _, dest in pairs}

    listing = _listing(target)
    assert manifest_dests <= set(listing.keys())
    for source_file, dest_rel in pairs:
        assert listing[dest_rel] == source_file.read_bytes(), dest_rel

    mirror_prefixes = (".claude/skills/", ".claude/agents/", ".claude/commands/")
    for rel in listing:
        if rel in manifest_dests:
            continue
        assert rel.startswith(mirror_prefixes), rel


def test_ca11_mirrors_verify_clean_after_install(tmp_path: Path) -> None:
    target = tmp_path / "fresh-target-mirrors"
    _git_init(target)

    install_result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install_result.returncode == 0, install_result.stdout + install_result.stderr

    for script_rel in installer.MIRROR_MATERIALIZERS:
        check = subprocess.run(
            [sys.executable, str(target / script_rel), "--check", "--repo-root", str(target)],
            capture_output=True,
            text=True,
            cwd=target,
        )
        assert check.returncode == 0, f"{script_rel}: {check.stdout}\n{check.stderr}"


# ------------------------------------------------------- non-git target


def test_ca12_non_git_target_is_rejected_with_no_effects(tmp_path: Path) -> None:
    target = tmp_path / "not-a-git-tree"
    target.mkdir()
    (target / "placeholder.txt").write_text("hola\n", encoding="utf-8")
    before = _listing(target)

    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)

    assert result.returncode == 2
    assert _listing(target) == before


def test_ca12_actual_root_is_accepted_regardless_of_path_case(tmp_path: Path) -> None:
    """A worktree root passed with different letter-casing than the one on
    disk (case-insensitive filesystems, the default on macOS/Windows) is
    still the same directory and must be accepted, not rejected as a
    subfolder of itself — resolve_git_root compares by identity
    (``os.path.samefile``), never by string equality of the resolved path."""
    target = tmp_path / "CaseSensitiveName"
    target.mkdir()
    _git_init(target)
    differently_cased = tmp_path / "casesensitivename"

    resolved = resolve_git_root(differently_cased)

    assert resolved.samefile(target)


# --------------------------------------- conflict regardless of history


def test_ca13_conflict_is_reported_regardless_of_previous_install_record(tmp_path: Path) -> None:
    target = tmp_path / "single-conflict"
    _git_init(target)

    source_file, dest_rel = _payload_pairs()[0]
    dest_path = target / dest_rel
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(_flip_one_byte(source_file.read_bytes()))

    assert not (resolve_git_dir(target) / installer.INSTALL_RECORD_NAME).exists()

    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)

    assert dest_rel in result.stdout.splitlines()


# -------------------------------------------------------- abort all-or-nothing


def _prepare_conflicts_and_one_missing(target: Path) -> tuple[list[str], str]:
    """Full clean install, then tamper 2 payload files (conflict) and delete
    a 3rd (missing) — every other payload path is left byte-identical.
    Returns (the 2 conflicting dest paths, the 1 missing dest path)."""
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0, install.stdout + install.stderr

    pairs = _payload_pairs()
    conflict_a, conflict_b, missing_entry = pairs[0], pairs[1], pairs[2]

    for source_file, dest_rel in (conflict_a, conflict_b):
        (target / dest_rel).write_bytes(_flip_one_byte(source_file.read_bytes()))

    missing_dest = missing_entry[1]
    (target / missing_dest).unlink()

    return [conflict_a[1], conflict_b[1]], missing_dest


def _bare_path_lines(text: str, candidates: set[str]) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() in candidates]


def test_ca14_any_conflict_aborts_the_whole_run(tmp_path: Path) -> None:
    target = tmp_path / "abort-all-or-nothing"
    conflicts, missing_dest = _prepare_conflicts_and_one_missing(target)

    before = _listing(target)
    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    after = _listing(target)

    assert result.returncode == 3
    assert after == before
    assert not (target / missing_dest).exists()

    all_dest_paths = {dest for _, dest in _payload_pairs()}
    combined = result.stdout + result.stderr
    matched_lines = _bare_path_lines(combined, all_dest_paths)
    assert len(matched_lines) == 2
    assert set(matched_lines) == set(conflicts)


# ---------------------------------------------------------- force completes


def test_ca15_force_completes_the_whole_install(tmp_path: Path) -> None:
    target = tmp_path / "force-completes-install"
    conflicts, missing_dest = _prepare_conflicts_and_one_missing(target)

    result = _run_cli(["--target", str(target), "--install", "--force"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr

    pairs_by_dest = {dest: source for source, dest in _payload_pairs()}
    for dest_rel in [*conflicts, missing_dest]:
        dest_path = target / dest_rel
        assert dest_path.is_file(), dest_rel
        assert dest_path.read_bytes() == pairs_by_dest[dest_rel].read_bytes(), dest_rel


# ---------------------------------------------------------- truncated manifest


def test_ca16_truncated_manifest_stops_with_no_effects(tmp_path: Path) -> None:
    target = tmp_path / "truncated-manifest"
    _git_init(target)
    before = _listing(target)

    # Cut mid-key of the last entry's `source:` — a byte offset that always
    # lands inside an entry, regardless of how the manifest's own entries
    # are laid out, unlike an arbitrary halfway split (YAML tolerates a
    # cut that happens to land on an entry boundary; this one never does:
    # the final list item becomes a bare scalar, not a mapping).
    original_text = DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8")
    marker = "- source:"
    cut_at = original_text.rindex(marker) + len("- sour")
    truncated_path = tmp_path / "truncated_manifest.yaml"
    truncated_path.write_text(original_text[:cut_at], encoding="utf-8")

    code = installer.run_install(target, manifest_path=truncated_path)

    assert code == 2
    assert _listing(target) == before


# ---------------------------------------------------------- verify never writes


def test_ca17_verify_never_writes_and_distinguishes_both_results(tmp_path: Path) -> None:
    target = tmp_path / "verify-no-write"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0, install.stdout + install.stderr

    before_clean = _listing(target)
    clean = _run_cli(["--target", str(target)], cwd=REPO_ROOT)
    after_clean = _listing(target)
    assert clean.returncode == 0
    assert after_clean == before_clean

    source_file, first_dest = _payload_pairs()[0]
    dest_path = target / first_dest
    dest_path.write_bytes(_flip_one_byte(source_file.read_bytes()))
    before_dirty = _listing(target)

    dirty = _run_cli(["--target", str(target)], cwd=REPO_ROOT)
    after_dirty = _listing(target)

    assert dirty.returncode == 1
    assert after_dirty == before_dirty


# ---------------------------------------------------------- idempotent hook


def test_ca27_pre_push_hook_is_idempotent_across_two_installs(tmp_path: Path) -> None:
    target = tmp_path / "idempotent-hook"
    _git_init(target)

    first = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert first.returncode == 0, first.stdout + first.stderr

    git_dir_result = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--absolute-git-dir"],
        capture_output=True,
        text=True,
        check=True,
    )
    hook_path = Path(git_dir_result.stdout.strip()) / "hooks" / "pre-push"
    assert hook_path.is_file()
    first_bytes = hook_path.read_bytes()
    assert b".spec/scripts/pre-push-gate.sh" in first_bytes

    second = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert second.returncode == 0, second.stdout + second.stderr
    assert hook_path.read_bytes() == first_bytes


# ---------------------------------------------------------- missing source


def test_ca28_missing_source_stops_with_no_effects(tmp_path: Path) -> None:
    target = tmp_path / "missing-source"
    _git_init(target)
    before = _listing(target)

    text = DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    data = yaml.safe_load(body)
    data["entries"].append(
        {"source": "installer/does-not-exist.txt", "dest": "does-not-exist.txt"}
    )
    broken_manifest = tmp_path / "broken_manifest.yaml"
    broken_manifest.write_text(yaml.safe_dump(data), encoding="utf-8")

    code = installer.run_install(target, manifest_path=broken_manifest)

    assert code == 2
    assert _listing(target) == before
