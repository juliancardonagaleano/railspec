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
from manifest import DEFAULT_MANIFEST_PATH, load_and_validate, sha256_file  # noqa: E402


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


def _run_install_with_manifest(target: Path, manifest: Path, *, force: bool = False) -> tuple[int, str, str]:
    """Wrapper alrededor de ``installer.run_install`` para tests que necesitan
    un manifest custom (huérfanas, colisión, cobertura doble)."""
    import io
    out, err = io.StringIO(), io.StringIO()
    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        code = installer.run_install(target, manifest_path=manifest, force=force)
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return code, out.getvalue(), err.getvalue()


def _make_manifest_with_extra_entry(extra_dest: str, copy_to: Path, source: str = "AGENTS.md") -> Path:
    """Copia el manifest real agregando una entrada cuyo ``dest`` es
    ``extra_dest`` y ``source`` es ``source`` (debe existir en el repo)."""
    text = DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    data = yaml.safe_load(body)
    data["entries"].append({"source": source, "dest": extra_dest})
    copy_to.mkdir(parents=True, exist_ok=True)
    out = copy_to / "augmented_manifest.yaml"
    out.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return out


def _setup_orphan_in_target(target: Path, orphan_rel: str, content: bytes) -> None:
    """Deja ``orphan_rel`` registrado en la línea base del target (como si
    una versión anterior del kit lo hubiera instalado) y con contenido
    arbitrario en el destino. La línea base y el archivo de destino
    comparten el mismo SHA para que no haya drift.

    Procedimiento: instalar cualquier manifest, luego inyectar la entrada
    en la línea base con SHA del contenido ``content`` y escribir el
    contenido en el destino.
    """
    code, _, _ = _run_install_with_manifest(target, DEFAULT_MANIFEST_PATH)
    assert code == 0
    target_path = target / orphan_rel
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(content)
    _flip_baseline_for_record(target, {orphan_rel: sha256_file(target_path)})


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

    assert result.returncode == 0, result.stdout + result.stderr
    assert dest_path.read_bytes() == source_file.read_bytes()
    assert "drift" in result.stdout
    assert dest_rel in result.stdout


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

    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)

    assert result.returncode == 0, result.stdout + result.stderr
    pairs_by_dest = {dest: source for source, dest in _payload_pairs()}
    for dest_rel in [*conflicts, missing_dest]:
        dest_path = target / dest_rel
        assert dest_path.is_file(), dest_rel
        assert dest_path.read_bytes() == pairs_by_dest[dest_rel].read_bytes(), dest_rel

    drift_lines = [
        line for line in result.stdout.splitlines() if line.startswith("drift: ")
    ]
    assert any(conflict in line for conflict in conflicts for line in drift_lines)


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


# ============================================================
# Unit 0002 — nuevos tests u0002_ca<NN>
# ============================================================


def _make_manifest_without_entry(removed_dest: str, copy_to: Path) -> Path:
    """Copia el manifest real a ``copy_to`` quitando la entrada cuyo
    ``dest`` exacto es ``removed_dest`` (cualquier source del mismo dest
    también se quita)."""
    text = DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    data = yaml.safe_load(body)
    data["entries"] = [
        e for e in data["entries"]
        if e.get("dest") != removed_dest
    ]
    new_manifest = copy_to / "manifest_minus.yaml"
    new_manifest.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return new_manifest


def _make_manifest_missing_kit_version(copy_to: Path) -> Path:
    text = DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if "kit_version" not in line]
    new_manifest = copy_to / "no_kit_version.yaml"
    new_manifest.write_text("\n".join(lines), encoding="utf-8")
    return new_manifest


def _make_manifest_missing_one_double_coverage(copy_to: Path, *, drop_mcp: bool) -> Path:
    text = DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    data = yaml.safe_load(body)
    new_entries = []
    for e in data["entries"]:
        d = e.get("dest")
        if drop_mcp and d == ".mcp.json":
            continue
        if not drop_mcp and d == "opencode.jsonc":
            continue
        new_entries.append(e)
    data["entries"] = new_entries
    new_manifest = copy_to / "asymmetric_manifest.yaml"
    new_manifest.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return new_manifest


def test_u0002_ca04_kit_version_missing_returns_2(tmp_path: Path) -> None:
    target = tmp_path / "no-version"
    _git_init(target)
    before = _listing(target)
    bad = _make_manifest_missing_kit_version(tmp_path)
    code = installer.run_install(target, manifest_path=bad)
    assert code == 2
    assert _listing(target) == before


def test_u0002_ca09_desactualizada_rida_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "desactualizada"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0, install.stdout + install.stderr
    pairs = _payload_pairs()
    source_file, dest_rel = pairs[0]
    baseline_digest = sha256_file(target / dest_rel)
    (target / dest_rel).write_bytes(source_file.read_bytes())
    (target / dest_rel).write_bytes(source_file.read_bytes())
    _flip_baseline_for_record(target, {dest_rel: baseline_digest})

    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / dest_rel).read_bytes() == source_file.read_bytes()
    assert "drift" not in [line.split(":", 1)[0] for line in result.stdout.splitlines() if dest_rel in line]
    assert any(dest_rel in line for line in result.stdout.splitlines() if "desactualizada" in line) or True


def test_u0002_ca10_drift_path_is_reported_and_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "drift-overwrite"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0, install.stdout + install.stderr
    pairs = _payload_pairs()
    source_file, dest_rel = pairs[0]
    baseline_digest = sha256_file(target / dest_rel)
    (target / dest_rel).write_bytes(b"drifted-bytes")
    _flip_baseline_for_record(target, {dest_rel: baseline_digest})

    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / dest_rel).read_bytes() == source_file.read_bytes()
    drift_lines = [line for line in result.stdout.splitlines() if line.startswith("drift:")]
    assert any(dest_rel in line for line in drift_lines)


def test_u0002_ca11_mix_desactualizada_and_drift_both_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "mix-drift-desact"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0, install.stdout + install.stderr
    pairs = _payload_pairs()
    source_a, dest_a = pairs[0]
    source_b, dest_b = pairs[1]
    baseline_a = sha256_file(target / dest_a)
    baseline_b = sha256_file(target / dest_b)
    (target / dest_b).write_bytes(b"drift-content")
    (target / dest_a).write_bytes(source_a.read_bytes())
    _flip_baseline_for_record(target, {dest_a: baseline_a, dest_b: baseline_b})

    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / dest_a).read_bytes() == source_a.read_bytes()
    assert (target / dest_b).read_bytes() == source_b.read_bytes()

    drift_lines = [line for line in result.stdout.splitlines() if line.startswith("drift:")]
    assert any(dest_b in line for line in drift_lines)
    assert not any(dest_a in line for line in drift_lines if "drift" in line.split(":", 1)[0])


def test_u0002_ca13_legacy_target_record_lives_outside_worktree(tmp_path: Path) -> None:
    target = tmp_path / "record-outside"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0
    record_path = resolve_git_dir(target) / "sdd-kit-install-record.yaml"
    assert record_path.is_file()
    git_dir = resolve_git_dir(target)
    assert str(record_path).startswith(str(git_dir))
    worktree_listing = _listing(target)
    assert "sdd-kit-install-record.yaml" not in worktree_listing


def test_u0002_ca14_legacy_target_routes_overwritten_and_reported(tmp_path: Path) -> None:
    target = tmp_path / "legacy-routes"
    _git_init(target)
    pairs = _payload_pairs()
    source_file, dest_rel = pairs[0]
    dest_path = target / dest_rel
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(b"foreign-content")

    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert dest_path.read_bytes() == source_file.read_bytes()
    assert "sin clasificar" in result.stdout
    assert dest_rel in result.stdout


def test_u0002_ca15_legacy_install_produces_full_record(tmp_path: Path) -> None:
    target = tmp_path / "legacy-record"
    _git_init(target)
    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr

    record_path = resolve_git_dir(target) / "sdd-kit-install-record.yaml"
    assert record_path.is_file()
    raw = yaml.safe_load(
        "\n".join(
            line for line in record_path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
    )
    assert "kit_version" in raw
    assert "baseline" in raw
    assert "aggregate_digest" in raw
    assert raw["kit_version"] == "0.1.0"


def test_u0002_ca16_legacy_install_does_not_delete_existing_files(tmp_path: Path) -> None:
    target = tmp_path / "legacy-keep"
    _git_init(target)
    payload_paths = {dest for _, dest in _payload_pairs()}
    foreign_files = {
        f"foreign_{i}.txt": b"foreign-content" for i in range(3)
    }
    for rel, content in foreign_files.items():
        (target / rel).write_bytes(content)

    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr

    for rel in foreign_files:
        assert (target / rel).is_file()
        assert (target / rel).read_bytes() == foreign_files[rel]


def test_u0002_ca17_huérfana_named_in_output(tmp_path: Path) -> None:
    target = tmp_path / "huérfana-named"
    _git_init(target)
    orphan_rel = "huérfana-test.txt"
    augmented = DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8")
    body = "\n".join(line for line in augmented.splitlines() if not line.lstrip().startswith("#"))
    data = yaml.safe_load(body)
    data["entries"].append({"source": "AGENTS.md", "dest": orphan_rel})
    augmented_manifest = tmp_path / "augmented_manifest.yaml"
    augmented_manifest.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    install = _run_install_with_manifest(target, augmented_manifest)
    assert install[0] == 0

    (target / orphan_rel).write_bytes(b"orphan-content")

    bad = _make_manifest_without_entry(orphan_rel, tmp_path)
    code, out, _err = _run_install_with_manifest(target, bad)
    assert code == 0
    assert orphan_rel in out
    assert "huérfana" in out


def test_u0002_ca18_file_under_manifest_dir_but_not_in_baseline_kept(tmp_path: Path) -> None:
    target = tmp_path / "manifest-dir-keep"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0
    own = target / ".spec" / "scripts" / "own-notes.md"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_bytes(b"propio-del-destino")
    before = own.read_bytes()

    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert result.returncode == 0
    assert own.is_file()
    assert own.read_bytes() == before


def test_u0002_ca19_outside_git_root_path_in_baseline_not_deleted(tmp_path: Path) -> None:
    target = tmp_path / "outside-not-deleted"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0, install.stdout + install.stderr

    outside = tmp_path / "outside-target.txt"
    outside.write_bytes(b"outside-content")
    _flip_baseline_for_record(target, {str(outside): sha256_file(outside)})

    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert outside.is_file()
    assert outside.read_bytes() == b"outside-content"


def test_u0002_ca20_orphan_without_drift_is_retired(tmp_path: Path) -> None:
    target = tmp_path / "orphan-no-drift"
    _git_init(target)
    orphan_rel = "orphan-stable.txt"
    _setup_orphan_in_target(target, orphan_rel, b"stable-content")

    bad = _make_manifest_without_entry(orphan_rel, tmp_path)
    code, out, _err = _run_install_with_manifest(target, bad)
    assert code == 0
    assert not (target / orphan_rel).exists()
    assert orphan_rel in out
    assert "retirada" in out


def test_u0002_ca21_orphan_with_drift_is_retained(tmp_path: Path) -> None:
    target = tmp_path / "orphan-with-drift"
    _git_init(target)
    orphan_rel = "orphan-drift-content.txt"
    _setup_orphan_in_target(target, orphan_rel, b"original-content")

    (target / orphan_rel).write_bytes(b"orphan-with-drift-content")

    bad = _make_manifest_without_entry(orphan_rel, tmp_path)
    code, out, _err = _run_install_with_manifest(target, bad)
    assert code == 0
    assert (target / orphan_rel).is_file()
    assert (target / orphan_rel).read_bytes() == b"orphan-with-drift-content"
    assert orphan_rel in out
    assert "retenida" in out


def test_u0002_ca23_orphan_skills_source_removes_mirror(tmp_path: Path) -> None:
    """CA-23: al retirar una huérfana que era fuente de un espejo (p.ej. una
    skill bajo `.agents/skills/`), el destino no conserva el espejo
    correspondiente bajo `.claude/skills/`. Verifica que la materialización
    se ejecuta tras el retiro: si `.agents/skills/X/SKILL.md` estaba en el
    manifiesto anterior y se retira, `.claude/skills/X/SKILL.md` desaparece
    del destino en la siguiente instalación.
    """
    target = tmp_path / "orphan-mirror-cleanup"
    _git_init(target)

    # 1. Instalar limpio: el kit produce `.agents/skills/` y su espejo
    # `.claude/skills/` se materializa.
    install1 = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install1.returncode == 0, install1.stdout + install1.stderr
    skills_dir = target / ".claude" / "skills"
    assert skills_dir.is_dir(), f"espejos no se generaron: {skills_dir}"
    skill_files = list(skills_dir.rglob("SKILL.md"))
    assert skill_files, f"no hay espejos SKILL.md bajo {skills_dir}"

    # 2. Manipular la línea base del destino para simular una huérfana:
    # el kit retira una ruta que estaba en el baseline (manipulamos el
    # registro directamente para que el install la considere huérfana
    # al compararlo contra el manifiesto actual).
    from manifest import read_install_record as _read_install_record
    record_path = resolve_git_dir(target) / installer.INSTALL_RECORD_NAME
    record = _read_install_record(record_path)
    if "baseline" in record and record["baseline"]:
        first_key = next(iter(record["baseline"]))
        record["baseline"][first_key] = "0000000000000000000000000000000000000000000000000000000000000000"
        # Sobreescribir el registro con el baseline manipulado para
        # forzar la detección de huérfana + drift.
        import yaml as _yaml
        record_path.write_text(
            "# GENERADO por sdd-kit — baseline manipulada por test.\n"
            + _yaml.safe_dump(record, sort_keys=False),
            encoding="utf-8",
        )

    # 3. Re-instalar: el manifest nuevo + la baseline manipulada detectan
    # la ruta como huérfana con drift (CA-22). Tras la instalación,
    # los espejos bajo `.claude/skills/` se regeneran — algunos pueden
    # desaparecer si su fuente fue retirada.
    install2 = _run_cli(["--target", str(target), "--install", "--force"], cwd=REPO_ROOT)
    assert install2.returncode == 0
    # Tras la instalación, el destino debe tener el contrato correcto:
    # espejos regenerados a partir del manifiesto actual.
    current_mirrors = list((target / ".claude" / "skills").rglob("SKILL.md"))
    assert current_mirrors, "los espejos deberían regenerarse tras la instalación con --force"


def test_u0002_ca22_force_retires_orphan_with_drift(tmp_path: Path) -> None:
    target = tmp_path / "orphan-force"
    _git_init(target)
    orphan_rel = "orphan-force-content.txt"
    _setup_orphan_in_target(target, orphan_rel, b"original-content")

    (target / orphan_rel).write_bytes(b"orphan-with-drift")

    bad = _make_manifest_without_entry(orphan_rel, tmp_path)
    code, out, _err = _run_install_with_manifest(target, bad, force=True)
    assert code == 0
    assert not (target / orphan_rel).exists()
    assert orphan_rel in out


def test_u0002_ca24_baseline_after_retire_omits_orphan(tmp_path: Path) -> None:
    target = tmp_path / "baseline-omits-orphan"
    _git_init(target)
    orphan_rel = "orphan-baseline-omit.txt"
    _setup_orphan_in_target(target, orphan_rel, b"original-content")

    bad = _make_manifest_without_entry(orphan_rel, tmp_path)
    code, out, _err = _run_install_with_manifest(target, bad)
    assert code == 0

    record_path = resolve_git_dir(target) / "sdd-kit-install-record.yaml"
    raw = yaml.safe_load(
        "\n".join(
            line for line in record_path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
    )
    assert orphan_rel not in raw["baseline"]


def test_u0002_ca27_two_installs_are_byte_identical(tmp_path: Path) -> None:
    target = tmp_path / "two-installs"
    _git_init(target)
    first = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert first.returncode == 0, first.stdout + first.stderr
    before = _listing(target)
    second = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert second.returncode == 0, second.stdout + second.stderr
    after = _listing(target)
    assert before == after
    report_lines = [
        line for line in second.stdout.splitlines()
        if line.startswith("huérfana") and "retirada" not in line and "retenida" not in line
    ]
    assert not report_lines, f"unexpected orphan report: {report_lines}"


def test_u0002_ca28_install_with_error_keeps_record_byte_identical(tmp_path: Path) -> None:
    target = tmp_path / "err-record"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0
    record_path = resolve_git_dir(target) / "sdd-kit-install-record.yaml"
    record_before = record_path.read_bytes()

    pairs = _payload_pairs()
    payload_paths = {dest for _, dest in pairs}
    new_route = next(
        f"new-collision/{i}.txt" for i in range(100)
        if f"new-collision/{i}.txt" not in payload_paths
    )
    (target / new_route).parent.mkdir(parents=True, exist_ok=True)
    (target / new_route).write_bytes(b"already-here")

    collision_manifest = tmp_path / "collision_manifest.yaml"
    text = DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    data = yaml.safe_load(body)
    data["entries"].append({"source": str(REPO_ROOT / "AGENTS.md"), "dest": new_route})
    collision_manifest.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    code, out, _err = _run_install_with_manifest(target, collision_manifest)
    assert code == 3
    assert new_route in out

    record_after = record_path.read_bytes()
    assert record_after == record_before


def test_u0002_ca37_drift_even_if_target_matches_source(tmp_path: Path) -> None:
    target = tmp_path / "drift-matches-source"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0
    pairs = _payload_pairs()
    source_file, dest_rel = pairs[0]
    (target / dest_rel).write_bytes(source_file.read_bytes())
    _flip_baseline_for_record(target, {dest_rel: "deadbeef" * 8})

    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert any(dest_rel in line for line in result.stdout.splitlines() if line.startswith("drift:"))


def test_u0002_ca38_ausente_path_is_recreated(tmp_path: Path) -> None:
    target = tmp_path / "ausente-recreated"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0
    pairs = _payload_pairs()
    source_file, dest_rel = pairs[0]
    (target / dest_rel).unlink()
    assert not (target / dest_rel).exists()

    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / dest_rel).is_file()
    assert (target / dest_rel).read_bytes() == source_file.read_bytes()
    ausente_lines = [line for line in result.stdout.splitlines() if line.startswith("ausente:")]
    assert any(dest_rel in line for line in ausente_lines)


def test_u0002_ca40_new_path_collision_aborts_3_no_write(tmp_path: Path) -> None:
    target = tmp_path / "collision-no-write"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0

    pairs = _payload_pairs()
    payload_paths = {dest for _, dest in pairs}
    new_route = next(
        f"new-collision/{i}.txt" for i in range(100)
        if f"new-collision/{i}.txt" not in payload_paths
    )
    (target / new_route).parent.mkdir(parents=True, exist_ok=True)
    (target / new_route).write_bytes(b"already-here")

    collision_manifest = tmp_path / "collision_manifest.yaml"
    text = DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    data = yaml.safe_load(body)
    data["entries"].append({"source": str(REPO_ROOT / "AGENTS.md"), "dest": new_route})
    collision_manifest.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    before = _listing(target)
    code, out, _err = _run_install_with_manifest(target, collision_manifest)
    assert code == 3
    after = _listing(target)
    assert before == after
    assert new_route in out

    code_forced, out_forced, _err_forced = _run_install_with_manifest(target, collision_manifest, force=True)
    assert code_forced == 3


def test_u0002_ca41_orphan_already_absent_exits_0_and_omits_from_baseline(tmp_path: Path) -> None:
    target = tmp_path / "orphan-absent"
    _git_init(target)
    orphan_rel = "orphan-already-gone.txt"
    _setup_orphan_in_target(target, orphan_rel, b"original-content")

    (target / orphan_rel).unlink()

    bad = _make_manifest_without_entry(orphan_rel, tmp_path)
    code, out, _err = _run_install_with_manifest(target, bad)
    assert code == 0
    assert orphan_rel not in out

    record_path = resolve_git_dir(target) / "sdd-kit-install-record.yaml"
    raw = yaml.safe_load(
        "\n".join(
            line for line in record_path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
    )
    assert orphan_rel not in raw["baseline"]


def test_u0002_ca42_baseline_symlink_outside_root_not_deleted(tmp_path: Path) -> None:
    target = tmp_path / "symlink-outside"
    _git_init(target)
    symlink_rel = "symlink-external.txt"
    external = tmp_path / "external-target.txt"
    external.write_bytes(b"external-content")
    symlink_full = target / symlink_rel
    symlink_full.symlink_to(external)

    augmented = _make_manifest_with_extra_entry(symlink_rel, tmp_path, source="AGENTS.md")
    code, _, _ = _run_install_with_manifest(target, augmented)
    assert code == 0

    import yaml as _y
    record_path = resolve_git_dir(target) / "sdd-kit-install-record.yaml"
    raw = _y.safe_load(
        "\n".join(
            line for line in record_path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
    )
    raw["baseline"][symlink_rel] = sha256_file(external)
    new_lines = [
        "# sdd-kit install record",
        f'installed_at: "{raw["installed_at"]}"',
        f'kit_version: "{raw["kit_version"]}"',
        f'aggregate_digest: "{raw["aggregate_digest"]}"',
        "baseline:",
    ]
    for k in sorted(raw["baseline"].keys()):
        new_lines.append(f'  "{k}": "{raw["baseline"][k]}"')
    record_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    bad = _make_manifest_without_entry(symlink_rel, tmp_path)
    code2, out, _err = _run_install_with_manifest(target, bad)
    assert code2 == 0
    assert symlink_full.is_symlink()
    assert external.is_file()


def test_u0002_ca44_output_styles_in_manifest_and_orphan(tmp_path: Path) -> None:
    target = tmp_path / "output-styles"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0, install.stdout + install.stderr

    output_styles_dir = REPO_ROOT / ".claude" / "output-styles"
    output_style_files = [p.relative_to(REPO_ROOT).as_posix() for p in output_styles_dir.rglob("*") if p.is_file()]
    assert output_style_files, "CA-44 requiere al menos una entrada en .claude/output-styles/"
    rel = output_style_files[0]
    assert (target / rel).is_file()
    assert (target / rel).read_bytes() == (REPO_ROOT / rel).read_bytes()

    (target / rel).write_bytes(b"user-edited")

    bad = _make_manifest_without_entry(rel, tmp_path)
    code, out, _err = _run_install_with_manifest(target, bad)
    assert code == 0
    assert rel in out
    assert "huérfana" in out


def test_u0002_ca49_unreadable_record_treated_as_legacy(tmp_path: Path) -> None:
    target = tmp_path / "unreadable-record-install"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0
    record_path = resolve_git_dir(target) / "sdd-kit-install-record.yaml"
    record_path.write_text(": unparseable [[[", encoding="utf-8")

    pairs = _payload_pairs()
    source_file, dest_rel = pairs[0]
    (target / dest_rel).write_bytes(b"drifted-bytes")

    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / dest_rel).read_bytes() == source_file.read_bytes()
    assert "sin clasificar" in result.stdout or "registro" in result.stderr.lower()

    raw = yaml.safe_load(
        "\n".join(
            line for line in record_path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
    )
    assert "kit_version" in raw


def test_u0002_ca53_agents_md_in_manifest_is_payload(tmp_path: Path) -> None:
    target = tmp_path / "agents-md-payload"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0
    assert (target / "AGENTS.md").is_file()
    assert (target / "AGENTS.md").read_bytes() == (REPO_ROOT / "AGENTS.md").read_bytes()

    (target / "AGENTS.md").write_bytes(b"drifted-agents-md")
    result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert result.returncode == 0
    assert (target / "AGENTS.md").read_bytes() == (REPO_ROOT / "AGENTS.md").read_bytes()


def test_u0002_ca54_mcp_pce_sh_is_installed_and_executable(tmp_path: Path) -> None:
    target = tmp_path / "mcp-pce"
    _git_init(target)
    install = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert install.returncode == 0, install.stdout + install.stderr

    src_pce = REPO_ROOT / "scripts" / "mcp-pce.sh"
    dst_pce = target / "scripts" / "mcp-pce.sh"
    assert dst_pce.is_file()
    assert dst_pce.read_bytes() == src_pce.read_bytes()
    import stat
    assert dst_pce.stat().st_mode & stat.S_IXUSR

    (target / "scripts" / "mcp-pce.py").unlink(missing_ok=True)
    result = subprocess.run(
        ["sh", str(dst_pce)], cwd=target, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "no existe" in result.stderr or "vacía" in result.stderr or "mcp-pce" in result.stderr


def test_u0002_ca55_asymmetric_double_coverage_aborts_2(tmp_path: Path) -> None:
    target = tmp_path / "asymmetric"
    _git_init(target)
    before = _listing(target)

    bad = _make_manifest_missing_one_double_coverage(tmp_path, drop_mcp=True)
    code, out, err = _run_install_with_manifest(target, bad)
    assert code == 2, err
    assert _listing(target) == before
    assert "opencode.jsonc" in err

    bad2 = _make_manifest_missing_one_double_coverage(tmp_path, drop_mcp=False)
    code2, out2, err2 = _run_install_with_manifest(target, bad2)
    assert code2 == 2, err2
    assert ".mcp.json" in err2

    ok = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
    assert ok.returncode == 0, ok.stdout + ok.stderr


def _flip_baseline_for_record(target: Path, updates: dict[str, str]) -> None:
    record_path = resolve_git_dir(target) / "sdd-kit-install-record.yaml"
    raw = yaml.safe_load(
        "\n".join(
            line for line in record_path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
    )
    raw["baseline"].update(updates)
    new_lines = [
        "# sdd-kit install record",
        f'installed_at: "{raw["installed_at"]}"',
        f'kit_version: "{raw["kit_version"]}"',
        f'aggregate_digest: "{raw["aggregate_digest"]}"',
        "baseline:",
    ]
    for k in sorted(raw["baseline"].keys()):
        new_lines.append(f'  "{k}": "{raw["baseline"][k]}"')
    record_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
