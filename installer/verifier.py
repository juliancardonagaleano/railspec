"""sdd-kit's verify operation (unit 0002 — versión y línea base).

Clasifica cada ruta de la carga declarada en el manifiesto contra la línea
base persistida en ``<git_dir>/sdd-kit-install-record.yaml``:

  - **desactualizada** (destino == línea base; solo el kit cambió): se
    reporta con marca propia, exit 0.
  - **drift** (destino != línea base; coincida o no con la fuente): se
    reporta con marca propia, exit 1.
  - **ausente** (línea base la registra, manifiesto la sigue declarando,
    destino ya no la tiene): se reporta con marca distinta de ``drift``.

Reporta además las rutas en la línea base que el manifiesto corriente ya
no declara (huérfanas). Sobre un destino sin divergencias y sin huérfanas
termina con código 0 (CA-12); con divergencias o huérfanas, código 1
(CA-39); con la fuente de versión faltante, código 2 (CA-05); con
registro de instalación presente pero ilegible, código 1 nombrándolo
(CA-49).

Imprime la versión del kit y la del destino (``kit_version`` del
registro); sobre un destino legado (sin registro o con ``kit_version``
ausente), imprime ``(sin versión registrada)`` (CA-06, CA-07).

Nunca borra nada (CA-26).
"""

from __future__ import annotations

import sys
from pathlib import Path

from git_target import GitTargetError, resolve_git_dir, resolve_git_root
from manifest import (
    DEFAULT_MANIFEST_PATH,
    REPO_ROOT,
    ManifestEntry,
    ManifestError,
    get_kit_version,
    load_and_validate,
    read_install_record,
    sha256_bytes,
)

EXIT_OK = 0
EXIT_DIVERGENT = 1
EXIT_OPERATIONAL_ERROR = 2

INSTALL_RECORD_NAME = "sdd-kit-install-record.yaml"


def iter_payload_files(
    entries: list[ManifestEntry], repo_root: Path = REPO_ROOT
) -> list[tuple[Path, str]]:
    """Expand each manifest entry into ``(source_file, dest_rel)`` pairs.

    A directory entry (e.g. ``.agents/skills``) expands to every file
    under it, recursively, with its destination path prefixed
    accordingly. A file entry yields itself unchanged. Shared by the
    verify and install operations so both classify content the same way.
    """
    pairs: list[tuple[Path, str]] = []
    for entry in entries:
        source_path = repo_root / entry.source
        if source_path.is_dir():
            dest_prefix = entry.dest.rstrip("/")
            for file_path in sorted(source_path.rglob("*")):
                if not file_path.is_file():
                    continue
                rel = file_path.relative_to(source_path).as_posix()
                pairs.append((file_path, f"{dest_prefix}/{rel}"))
        else:
            pairs.append((source_path, entry.dest))
    return pairs


def run_verify(target: Path, *, manifest_path: Path = DEFAULT_MANIFEST_PATH) -> int:
    """Entry point de ``verificar``. Imprime marcas por ruta en stdout."""
    try:
        kit_version = get_kit_version(manifest_path)
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL_ERROR

    try:
        entries = load_and_validate(manifest_path)
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL_ERROR

    try:
        git_root = resolve_git_root(target)
    except GitTargetError:
        git_root = target

    baseline: dict[str, str] = {}
    destino_kit_version: str | None = None
    registro_ileible = False
    record_path: Path | None = None
    try:
        git_dir = resolve_git_dir(git_root)
        record_path = git_dir / INSTALL_RECORD_NAME
    except GitTargetError:
        record_path = None

    registro_presente = record_path is not None and record_path.is_file()
    if registro_presente and record_path is not None:
        raw_record = read_install_record(record_path)
        if isinstance(raw_record, dict) and isinstance(raw_record.get("baseline"), dict):
            baseline = {
                str(k): str(v)
                for k, v in raw_record["baseline"].items()
                if isinstance(k, str) and isinstance(v, str)
            }
            kv = raw_record.get("kit_version")
            if isinstance(kv, str) and kv.strip():
                destino_kit_version = kv.strip()
        else:
            registro_ileible = True

    if registro_ileible:
        destino_kit_version_display = "(registro ilegible)"
    elif destino_kit_version is None:
        destino_kit_version_display = "(sin versión registrada)"
    else:
        destino_kit_version_display = destino_kit_version

    print(f"kit_version: {kit_version}")
    print(f"destino_kit_version: {destino_kit_version_display}")

    pares = iter_payload_files(entries)
    pares_por_dest = {dest: source for source, dest in pares}

    if registro_ileible:
        for source_file, dest_rel in pares:
            print(f"divergencia sin clasificar: {dest_rel}")
        print("ERROR: registro de instalación presente pero ilegible", file=sys.stderr)
        return EXIT_DIVERGENT

    has_divergence = False

    if not baseline:
        if registro_presente:
            print(
                f"AVISO: destino sin línea base — todas las rutas se marcan como 'ausente'",
                file=sys.stderr,
            )
        for source_file, dest_rel in pares:
            dest_path = git_root / dest_rel
            if not dest_path.is_file():
                print(f"ausente: {dest_rel}")
                has_divergence = True
                continue
            if sha256_bytes(dest_path.read_bytes()) != sha256_bytes(source_file.read_bytes()):
                print(f"divergencia sin clasificar: {dest_rel}")
                has_divergence = True
        if has_divergence:
            return EXIT_DIVERGENT
        return EXIT_OK

    for source_file, dest_rel in pares:
        baseline_digest = baseline.get(dest_rel)
        dest_path = git_root / dest_rel
        if baseline_digest is None:
            print(f"ausente: {dest_rel}")
            has_divergence = True
            continue
        if not dest_path.is_file():
            print(f"ausente: {dest_rel}")
            has_divergence = True
            continue
        actual_digest = sha256_bytes(dest_path.read_bytes())
        if actual_digest == baseline_digest:
            continue
        print(f"drift: {dest_rel}")
        has_divergence = True

    for rel in baseline:
        if rel in pares_por_dest:
            continue
        dest_path = git_root / rel
        if not dest_path.is_file():
            continue
        print(f"huérfana: {rel}")
        has_divergence = True

    if has_divergence:
        return EXIT_DIVERGENT
    return EXIT_OK