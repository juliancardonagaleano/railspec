"""sdd-kit's install operation (unit 0002 — versión y línea base).

Escribe la carga del kit en el destino, regenera los espejos ``.claude/``,
instala el gancho ``pre-push`` y, al cierre exitoso, persiste en
``<git_dir>/sdd-kit-install-record.yaml`` la línea base (mapa ruta →
 SHA-256 sobre los bytes escritos), la ``kit_version`` y el ``aggregate_digest``
 SHA-256 de la carga completa.

Clasificación contra la línea base (nunca contra la fuente; CA-09..CA-11):

    - **desactualizada**: el contenido del destino coincide con su entrada
      en la línea base; solo el kit cambió. Se sobrescribe con la fuente
      silenciosamente.
    - **drift**: el contenido del destino difiere de su entrada en la
      línea base (coincida o no con la fuente). Se sobrescribe con la
      fuente y se imprime una línea con la palabra literal ``drift``.
    - **ausente**: la línea base registra la ruta, el manifiesto la sigue
      declarando, el destino ya no la tiene. Se re-escribe con la fuente y
      se imprime con marca distinta de ``drift``.

Rutas huérfanas (P-1..P-3, CA-17..CA-23):

    - sin drift → se retira y se reporta;
    - con drift sin ``--force`` → se retiene y se reporta (exit 0);
    - con drift con ``--force`` → se retira y se reporta (CA-22);
    - ya ausentes en el destino → no se nombran como error y se omiten de
      la línea base resultante (CA-41);
    - rutas que resuelven fuera de la raíz git, symlinks a fuera, o
      directorios en vez de archivos → nunca se borran (CA-19, CA-42).

Colisión de ruta nueva (CA-40):

    - ruta en el manifiesto que la línea base **no** registra y el destino
    ya tiene un archivo ahí → exit 3, todo-o-nada, ``--force`` no la
    autoriza.

Códigos de salida:

    0 OK (carga sobrescrita; huérfanas tratadas; línea base actualizada)
    2 error operativo (manifiesto/versión/cobertura doble/registro ilegible
      al cierre, etc.). El destino queda byte-idéntico a su estado previo.
    3 colisión de ruta nueva (CA-40).
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from installer.git_target import GitTargetError, resolve_git_dir, resolve_git_root  # noqa: E402
from installer.manifest import (  # noqa: E402
    DEFAULT_MANIFEST_PATH,
    ManifestError,
    get_kit_version,
    load_and_validate,
    read_install_record,
    sha256_bytes,
    sha256_file,
)
from installer.materializers.agents import main as agents_main  # noqa: E402
from installer.materializers.commands import main as commands_main  # noqa: E402
from installer.materializers.skills import main as skills_main  # noqa: E402
from installer.verifier import iter_payload_files  # noqa: E402

EXIT_OK = 0
EXIT_OPERATIONAL_ERROR = 2
EXIT_NEW_PATH_COLLISION = 3

# Nombre del archivo de registro, bajo el git dir del destino. Vive fuera
# del worktree y fuera de la carga (CA-13).
INSTALL_RECORD_NAME = "sdd-kit-install-record.yaml"

# Espejos ``.claude/skills|agents|commands`` regenerados en el destino
# después de escribir la carga. Tupla ``(which, callable)`` — U-0006 invoca
# cada materializador en proceso (sin lanzar intérprete nuevo).
MIRROR_MATERIALIZERS = (
    ("agents", agents_main),
    ("commands", commands_main),
    ("skills", skills_main),
)

PRE_PUSH_HOOK_INSTALLER = "scripts/install_pre_push_hook.sh"

# Marca para la cobertura doble Claude + Opencode (CA-55). Si el manifiesto
# declara una sin la otra, abortar código 2 antes de tocar nada.
CONFIG_DOUBLE_COVERAGE = (".mcp.json", "opencode.jsonc")


def _check_double_coverage(manifest_path: Path) -> str | None:
    """Devuelve ``None`` si la cobertura doble es consistente; un mensaje
    de error si falta una de las dos (CA-55)."""
    import yaml
    text = manifest_path.read_text(encoding="utf-8")
    body_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    try:
        data = yaml.safe_load("\n".join(body_lines))
    except yaml.YAMLError:
        return "manifiesto con YAML inválido al validar cobertura doble"
    if not isinstance(data, dict):
        return None
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        return None
    dests = {entry.get("dest") for entry in raw_entries if isinstance(entry, dict)}
    has_mcp = ".mcp.json" in dests
    has_opencode = "opencode.jsonc" in dests
    if has_mcp and not has_opencode:
        return "manifiesto declara `.mcp.json` pero no `opencode.jsonc` (CA-55)"
    if has_opencode and not has_mcp:
        return "manifiesto declara `opencode.jsonc` pero no `.mcp.json` (CA-55)"
    return None


def _pair_dest_index(entries) -> dict[str, tuple[Path, str]]:
    """Índice ruta-destino → (source, dest) para todas las entries. Las
    entries de directorio expanden a sus archivos."""
    pairs = iter_payload_files(entries)
    return {dest: (source, dest) for source, dest in pairs}


def _normalize_dest(rel: str) -> Path:
    """Normaliza una ruta relativa POSIX; rechaza absolutas o con ``..``."""
    if rel.startswith("/"):
        raise ValueError(f"ruta absoluta prohibida: {rel}")
    parts = rel.split("/")
    if any(part == ".." for part in parts):
        raise ValueError(f"ruta con '..' prohibida: {rel}")
    return Path(*parts)


def _is_safe_to_delete(target_root: Path, target_path: Path) -> bool:
    """True si ``target_path`` está dentro de ``target_root``, no es symlink
    fuera, no es un directorio, y existe (CA-19, CA-42)."""
    try:
        resolved = target_path.resolve()
        target_resolved = target_root.resolve()
    except OSError:
        return False
    try:
        resolved.relative_to(target_resolved)
    except ValueError:
        return False
    if resolved.is_symlink():
        try:
            link_target = resolved.resolve(strict=True)
        except OSError:
            return False
        try:
            link_target.relative_to(target_resolved)
        except ValueError:
            return False
    if target_path.is_dir():
        return False
    return target_path.exists()


def _vaciar_directorios_vacios(path: Path, target_root: Path, manifest_roots: set[str]) -> None:
    """Sube desde el padre del archivo retirado y elimina directorios
    vacíos hasta una raíz declarada por el manifiesto o hasta
    ``target_root`` (CA-18/CA-23). Patrón reutilizado de
    ``scripts/materialize_claude_skills.py``."""
    current = path.parent
    target_resolved = target_root.resolve()
    while current != target_resolved:
        try:
            current_resolved = current.resolve()
        except OSError:
            return
        if current_resolved == target_resolved:
            return
        try:
            rel_to_target = current_resolved.relative_to(target_resolved)
        except ValueError:
            return
        if rel_to_target.as_posix() in manifest_roots:
            return
        try:
            if not any(current.iterdir()):
                current.rmdir()
            else:
                return
        except OSError:
            return
        current = current.parent


def _manifest_root_dirs(git_root: Path) -> set[str]:
    """Devuelve los roots de directorio que el manifiesto declara
    (sin el sufijo slash), normalizados como paths POSIX relativos a
    ``git_root``. Se usan como tope al subir directorios vacíos."""
    import yaml
    text = DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8")
    body_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    data = yaml.safe_load("\n".join(body_lines))
    raw_entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(raw_entries, list):
        return set()
    roots: set[str] = set()
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        dest = entry.get("dest")
        if not isinstance(dest, str):
            continue
        if dest.endswith("/") or "/" in dest:
            parts = dest.split("/")
            first = parts[0]
            if first:
                roots.add(first)
        else:
            parts = dest.split("/")
            if len(parts) > 1:
                roots.add(parts[0])
    return roots


def _run_materializer(which: str, fn, target: Path) -> int:
    """Invoca un materializador en proceso (U-0006). Pasa ``--repo-root
    <target>`` y propaga el returncode; sin lanzar intérprete nuevo."""
    return fn(["--repo-root", str(target)])


def _install_pre_push_hook(target: Path) -> int:
    hook_installer = target / PRE_PUSH_HOOK_INSTALLER
    result = subprocess.run(
        ["bash", str(hook_installer)],
        cwd=target,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def _aggregate_digest_for_payload(pairs: list[tuple[Path, str]]) -> str:
    """Digest agregado de la carga (CA-50). Concatena los bytes escritos
    en el destino ordenados por ruta y los hashea. El orden es estable
    porque ``iter_payload_files`` ordena por ``sorted(rglob)``."""
    h = hashlib.sha256()
    for source_file, dest_rel in pairs:
        target = source_file
        try:
            data = target.read_bytes()
        except OSError:
            continue
        h.update(dest_rel.encode("utf-8"))
        h.update(b"\x00")
        h.update(data)
        h.update(b"\x00")
    return h.hexdigest()


def _write_install_record(
    git_root: Path,
    kit_version: str,
    baseline: dict[str, str],
    aggregate_digest: str,
) -> None:
    """Persiste la línea base en ``<git_dir>/sdd-kit-install-record.yaml``
    con ``installed_at``, ``kit_version``, ``baseline:`` y ``aggregate_digest``.
    Hand-written (no ``yaml.safe_dump``) por consistencia con los
    materializadores."""
    record_path = resolve_git_dir(git_root) / INSTALL_RECORD_NAME
    record_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "# sdd-kit install record — generado por installer/installer.py.",
        "# NO editar a mano: cualquier modificación se sobrescribe en el",
        "# próximo --install. La línea base (baseline:) es el único",
        "# árbitro de drift para el hook pre-push (CA-33).",
        f'installed_at: "{timestamp}"',
        f'kit_version: "{kit_version}"',
        f'aggregate_digest: "{aggregate_digest}"',
        "baseline:",
    ]
    for rel in sorted(baseline.keys()):
        lines.append(f'  "{rel}": "{baseline[rel]}"')
    record_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_install(
    target: Path,
    *,
    force: bool = False,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
) -> int:
    """Entry point de ``--install`` del CLI. Verbosidad: imprime marcas de
    categoría por ruta en stdout y errores operativos en stderr."""
    # (1) Validar manifiesto y versión al inicio (CA-04, CA-55): si algo
    # falla, abortar código 2 sin tocar destino.
    coverage_error = _check_double_coverage(manifest_path)
    if coverage_error:
        print(f"ERROR: {coverage_error}", file=sys.stderr)
        return EXIT_OPERATIONAL_ERROR

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
    except GitTargetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL_ERROR

    git_dir = resolve_git_dir(git_root)
    record_path = git_dir / INSTALL_RECORD_NAME

    raw_record = read_install_record(record_path)
    registro_legible_antes = bool(raw_record and isinstance(raw_record.get("baseline"), dict))
    baseline_anterior: dict[str, str] = {}
    if isinstance(raw_record, dict) and isinstance(raw_record.get("baseline"), dict):
        baseline_anterior = {
            str(k): str(v)
            for k, v in raw_record["baseline"].items()
            if isinstance(k, str) and isinstance(v, str)
        }
    if raw_record and not isinstance(raw_record.get("baseline"), dict):
        print(
            "AVISO: registro de instalación previo está presente pero su "
            "`baseline` no es un mapeo válido; se descarta como legado.",
            file=sys.stderr,
        )

    pares = iter_payload_files(entries)
    pares_por_dest = {dest: source for source, dest in pares}

    # Detección de colisión de ruta nueva (CA-40): la línea base **registra**
    # que esta ruta ya estaba instalada (alguna versión anterior del kit la
    # cubrió), el manifiesto corriente ya no la declara, y el destino ya
    # tiene un archivo ahí. Si no hay línea base (destino legado o clon
    # nuevo), no es colisión: la ruta entra como cualquier otra de la carga.
    colision: str | None = None
    if baseline_anterior:
        for source_file, dest_rel in pares:
            if dest_rel not in baseline_anterior and (git_root / dest_rel).exists():
                colision = dest_rel
                break

    if colision is not None:
        print(
            f"ERROR: ruta nueva colisiona con archivo del destino (no se puede sobrescribir): {colision}",
            file=sys.stderr,
        )
        print(colision)
        return EXIT_NEW_PATH_COLLISION

    # Detección de huérfanas (CA-17..CA-23, CA-41, CA-42).
    huérfanas: list[tuple[str, str]] = []
    for rel, baseline_digest in baseline_anterior.items():
        if rel in pares_por_dest:
            continue
        try:
            target_path = (git_root / rel).resolve()
        except (OSError, ValueError):
            continue
        if not _is_safe_to_delete(git_root, git_root / rel):
            continue
        actual = git_root / rel
        if not actual.is_file():
            continue
        actual_bytes = actual.read_bytes()
        drift = sha256_bytes(actual_bytes) != baseline_digest
        huérfanas.append((rel, "drift" if drift else ""))

    # Clasificación y escritura de cada par declarado en el manifiesto.
    drift_paths: list[str] = []
    ausente_paths: list[str] = []
    sin_clasificar_paths: list[str] = []
    desactualizada_count = 0

    # Snapshot de bytes para cada destino antes de escribir (para CA-13).
    before_bytes: dict[str, bytes | None] = {}
    for source_file, dest_rel in pares:
        dest_path = git_root / dest_rel
        try:
            before_bytes[dest_rel] = dest_path.read_bytes() if dest_path.is_file() else None
        except OSError:
            before_bytes[dest_rel] = None

    for source_file, dest_rel in pares:
        dest_path = git_root / dest_rel
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        data = source_file.read_bytes()
        baseline_digest = baseline_anterior.get(dest_rel)
        current_bytes = before_bytes.get(dest_rel)
        if baseline_digest is None:
            if current_bytes is None:
                status = "ausente"
                ausente_paths.append(dest_rel)
            elif sha256_bytes(current_bytes) != sha256_bytes(data):
                status = "sin clasificar"
                sin_clasificar_paths.append(dest_rel)
            else:
                status = "ok"
        else:
            if current_bytes is None:
                status = "ausente"
                ausente_paths.append(dest_rel)
            elif sha256_bytes(current_bytes) == baseline_digest:
                status = "desactualizada"
                desactualizada_count += 1
            else:
                status = "drift"
                drift_paths.append(dest_rel)
        dest_path.write_bytes(data)
        try:
            source_mode = source_file.stat().st_mode
            dest_path.chmod(source_mode)
        except OSError:
            pass

    # Reporte de clasificación.
    for rel in drift_paths:
        print(f"drift: {rel}")
    for rel in ausente_paths:
        print(f"ausente: {rel}")
    for rel in sin_clasificar_paths:
        print(f"divergencia sin clasificar: {rel}")

    # Tratamiento de huérfanas.
    manifest_roots = _manifest_root_dirs(git_root)
    for rel, kind in huérfanas:
        target_file = git_root / rel
        if kind == "drift":
            if force:
                print(f"huérfana retirada (--force): {rel}")
                try:
                    target_file.unlink()
                except OSError as exc:
                    print(
                        f"AVISO: no se pudo retirar huérfana {rel}: {exc}",
                        file=sys.stderr,
                    )
                else:
                    _vaciar_directorios_vacios(target_file, git_root, manifest_roots)
            else:
                print(f"huérfana retenida por drift: {rel}")
        else:
            print(f"huérfana retirada: {rel}")
            try:
                target_file.unlink()
            except OSError as exc:
                print(
                    f"AVISO: no se pudo retirar huérfana {rel}: {exc}",
                    file=sys.stderr,
                )
            else:
                _vaciar_directorios_vacios(target_file, git_root, manifest_roots)

    # Espejos.
    for which, fn in MIRROR_MATERIALIZERS:
        code = _run_materializer(which, fn, git_root)
        if code != 0:
            print(
                f"ERROR: materializador {which} falló con código {code} sobre {git_root}.",
                file=sys.stderr,
            )
            return EXIT_OPERATIONAL_ERROR

    hook_code = _install_pre_push_hook(git_root)
    if hook_code != 0:
        print(
            f"ERROR: instalación del gancho de pre-push falló con código {hook_code}.",
            file=sys.stderr,
        )
        return EXIT_OPERATIONAL_ERROR

    # Cableado de configuración (CA-45..CA-47, CA-51, CA-52).
    try:
        from installer.config_cableado import cablear  # noqa: E402
        cablear(git_root, git_root)
    except Exception as exc:
        print(
            f"ERROR: cableado de configuración del entorno falló: {exc}",
            file=sys.stderr,
        )
        return EXIT_OPERATIONAL_ERROR

    # Cálculo de la nueva línea base y digest agregado.
    nueva_baseline: dict[str, str] = {}
    for source_file, dest_rel in pares:
        target = git_root / dest_rel
        if not target.is_file():
            continue
        nueva_baseline[dest_rel] = sha256_file(target)
    aggregate_digest = _aggregate_digest_for_payload(pares)

    if registro_legible_antes and raw_record:
        print(
            "AVISO: registro de instalación previo con formato heredado "
            "descartado — se reemplaza por uno válido al cierre."
        )

    _write_install_record(git_root, kit_version, nueva_baseline, aggregate_digest)

    print(
        f"OK: instalación completa en {git_root} "
        f"({len(pares)} archivo(s); drift={len(drift_paths)}; "
        f"ausentes={len(ausente_paths)}; desactualizadas={desactualizada_count}; "
        f"huérfanas={len(huérfanas)})"
    )
    return EXIT_OK