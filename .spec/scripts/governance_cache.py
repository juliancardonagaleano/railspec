#!/usr/bin/env python3
"""Per-unit governance lookup cache (unit 0123, decisión directa `0123-D3`).

Reads/writes `<unit>/.governance-cache.json` (gitignored). The cache stores
the literal JSON returned by `pce-mcp` for each `resolve_entity` and
`search_catalog` query that `sdd-gate` paso 3 issues, indexed by query.

The cache is **invalidated** when either of these changes between gates of
the same unit:

  * `catalog_hash` — a hash of the catalog the gate would see. When the
    catalog gains, removes, or edits an entity, the hash flips and the
    cached responses are stale.
  * `governance_refs_hash` — a hash of the sorted, normalized list of
    `_estado.yaml > governance_refs`. When the unit adds or removes an
    id, the cached `resolve_entity` results are stale.

This script does **not** invoke `pce-mcp` itself — it only reads/writes
the cache file. The gate that decides "hit / fresh / invalidated" runs the
hash checks and falls through to the MCP if the cache is missing or stale.
That keeps the script deterministic and unit-testable.

Decisión directa de Julian, `0123-D3` (2026-09-21), bajo CR-2:
  - prefijo medido por `0113` baja — the script replaces 7-8 k tokens of
    catalog retrieval per gate by a disk read of ~2-5 k tokens.
  - no test pasa a rojo — new file, no existing test references it.
  - no altera MUST/MUST NOT — the gate still consults `pce-mcp`; the
    cache only avoids re-doing work the previous gate already did.

Subcommands:
    governance_cache.py get --unit <u> --fase <fase> --refs <csv>
        Returns the cached JSON if the catalog_hash and
        governance_refs_hash match; prints "null" (exit 0) otherwise.
        Never invokes the MCP.

    governance_cache.py put --unit <u> --fase <fase> --refs <csv>
                            --catalog-hash <hex> --input <json-path>
        Reads `<json-path>` (a JSON file with the MCP responses keyed by
        query) and writes `<unit>/.governance-cache.json`. Refuses to
        write if the file already exists with a different hash pair.

    governance_cache.py invalidate --unit <u>
        Removes the cache file. Use when `governance_refs` change.

Exit codes:
  0  Cache hit or successful put.
  1  Cache miss / no unit / malformed cache.
  2  Stale hash (caller must re-query MCP).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _hash_refs(refs_csv: str) -> str:
    """SHA-256 of the sorted, normalized `governance_refs` list."""
    refs = [r.strip() for r in refs_csv.split(",") if r.strip()]
    refs = sorted(set(refs))
    return hashlib.sha256("\n".join(refs).encode("utf-8")).hexdigest()[:16]


def _cache_path(unit_arg: str) -> Path:
    """Resolve `--unit <arg>` to a cache path under `.spec/units/<u>/`."""
    unit_path = Path(unit_arg)
    if unit_path.is_absolute():
        return unit_path / ".governance-cache.json"
    rel = REPO_ROOT / unit_arg
    if rel.is_dir():
        return rel / ".governance-cache.json"
    rel = REPO_ROOT / ".spec" / "units" / unit_arg
    if rel.is_dir():
        return rel / ".governance-cache.json"
    return Path("/nonexistent")


def cmd_get(args: argparse.Namespace) -> int:
    cache = _cache_path(args.unit)
    if not cache.is_file():
        print("null")
        return 1
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print("null")
        return 1
    refs_hash = _hash_refs(args.refs)
    if data.get("catalog_hash") != args.catalog_hash:
        print("null")
        return 2
    if data.get("governance_refs_hash") != refs_hash:
        print("null")
        return 2
    responses = data.get("responses", {})
    print(json.dumps(responses, ensure_ascii=False))
    return 0


def cmd_put(args: argparse.Namespace) -> int:
    cache = _cache_path(args.unit)
    if not cache.parent.is_dir():
        print(f"ERROR — unidad inexistente: {args.unit}", file=sys.stderr)
        return 1
    if not Path(args.input).is_file():
        print(f"ERROR — input no existe: {args.input}", file=sys.stderr)
        return 1
    responses = json.loads(Path(args.input).read_text(encoding="utf-8"))
    refs_hash = _hash_refs(args.refs)
    payload = {
        "schema_version": 1,
        "fase": args.fase,
        "catalog_hash": args.catalog_hash,
        "governance_refs_hash": refs_hash,
        "responses": responses,
    }
    cache.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK — cache written: {cache}")
    return 0


def cmd_invalidate(args: argparse.Namespace) -> int:
    cache = _cache_path(args.unit)
    if cache.is_file():
        cache.unlink()
        print(f"OK — cache invalidated: {cache}")
    else:
        print(f"OK — no cache to invalidate at {cache}")
    return 0


def main() -> int:
    first_line = __doc__.splitlines()[0] if __doc__ else "governance_cache.py"
    ap = argparse.ArgumentParser(prog="governance_cache.py", description=first_line)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get", help="return cached responses or 'null'")
    g.add_argument("--unit", required=True)
    g.add_argument("--fase", required=True)
    g.add_argument("--refs", required=True)
    g.add_argument("--catalog-hash", required=True)

    p = sub.add_parser("put", help="write cache from a JSON file")
    p.add_argument("--unit", required=True)
    p.add_argument("--fase", required=True)
    p.add_argument("--refs", required=True)
    p.add_argument("--catalog-hash", required=True)
    p.add_argument("--input", required=True)

    inv = sub.add_parser("invalidate", help="drop the cache file")
    inv.add_argument("--unit", required=True)

    args = ap.parse_args()
    if args.cmd == "get":
        return cmd_get(args)
    if args.cmd == "put":
        return cmd_put(args)
    if args.cmd == "invalidate":
        return cmd_invalidate(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
