#!/usr/bin/env python3
"""Bridge stdio (MCP) <-> HTTP (Streamable HTTP) para `pce-mcp` (PCE).

Sustituye al antiguo wrapper basado en `mcp-remote@0.8.3` que estaba
pinnado a una versión con flags no implementados y un framing no estándar.

Fuentes (en este orden de precedencia):
- Variable de entorno `PCE_MCP_API_KEY` (exportada por `.mcp.json` o shell).
- Variable de entorno `PCE_MCP_URL`; si falta, default `https://20.7.84.154/mcp`.
- `NODE_EXTRA_CA_CERTS` (mismo bundle que ya consumía el wrapper anterior).
- Si ninguna variable de API key existe, intenta leer `.env` en la raíz del repo
  con la misma forma que `mcp-pce.sh` usaba (clave `PCE_MCP_API_KEY=`).

Protocolo:
- stdio lado cliente: framing MCP estándar (`Content-Length: N\r\n\r\n<body>`).
- HTTP lado servidor: `POST` con `Content-Type: application/json`,
  `Accept: application/json, text/event-stream`, `X-API-Key: <key>`.
- Soporta respuesta JSON única o `text/event-stream` (`data: <json>\n\n`).

Caché semántica local (0125-cache-veredictos-de-gate-en-mcp-de-conocimiento):

El bridge actúa como choke point para todas las llamadas MCP del cliente.
Esta capa agrega una caché opcional de respuestas, organizada en tres
niveles:

- **L0 bypass** — métodos no idempotentes (típicamente notificaciones) se
  reenvían siempre sin tocar la caché.
- **L1 exact match** — hash SHA-256 de `(method, params_normalizados)`. La
  coincidencia exacta se sirve de inmediato sin reenviar al servidor.
- **L2 semantic match** — TF-IDF con vocabulario dinámico + similitud coseno
  ≥ `MCP_PCE_CACHE_SIM_THRESHOLD` (default 0.85). Permite reusar respuestas
  para queries con texto cercano (e.g., `pri-ia-humano-decide` ≈
  `pri-ia-humano-decide-v2`).
- **L3 live** — si L1 y L2 fallan, se reenvía al servidor y la respuesta
  se persiste en la caché con su timestamp.

Storage: SQLite en `~/.siste/mcp-pce-cache.sqlite3` (per-machine, gitignored).
TTL por defecto 1 hora, configurable vía `MCP_PCE_CACHE_TTL_SECS`. Pasada la
TTL, la entrada no se sirve (se reenvía al servidor y se renueva).

Identidad de la respuesta cacheada:
- `pragma`: 4-tuple `(method, params_digest, response_bytes, ts_unix)`
- `params_digest` = `sha256(method || json_canon(params))`

Envelope `_meta` que se añade a cada respuesta cacheada:
- `_meta.cached: true` — respuesta servida desde caché (L1 o L2)
- `_meta.cache_age_ms: <int>` — edad en milisegundos de la entrada servida
- `_meta.cache_layer: "L1" | "L2"` — qué capa produjo el hit
- (Ausente en respuestas fresh del servidor)

Limitaciones (deliberadas, mantener simple):
- Sin session persistence entre invocaciones: cada POST es independiente.
- Sin reintentos automáticos: el cliente decide.
- Sin notificaciones server->client (sampling, roots, etc.) — si llegan se
  devuelven vacías y se loguean a stderr; el upstream no las emite hoy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = "https://20.7.84.154/mcp"
DEFAULT_CACHE_PATH = Path.home() / ".siste" / "mcp-pce-cache.sqlite3"
DEFAULT_TTL_SECS = 3600
DEFAULT_SIM_THRESHOLD = 0.85

# Métodos que típicamente son notificaciones o lecturas no-idempotentes.
# No los cacheamos: cada llamada reenvía al servidor.
_NON_IDEMPOTENT_METHODS = frozenset({
    "notifications/initialized",
    "notifications/cancelled",
    "notifications/progress",
    "logging/setLevel",
    "notifications/message",
})

# El handshake se negocia contra el servidor vivo en cada arranque: servir un
# `initialize` cacheado devolvería capacidades y `protocolVersion` de una sesión
# anterior, que el cliente asumiría vigentes.
_HANDSHAKE_METHODS = frozenset({"initialize"})

# Unión de lo que nunca toca la caché (ni lectura ni escritura).
_UNCACHEABLE_METHODS = _NON_IDEMPOTENT_METHODS | _HANDSHAKE_METHODS


def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        out[k] = v
    return out


def read_message(stream) -> tuple[bytes | None, str]:
    """Lee un mensaje MCP detectando el framing.

    Devuelve `(body, framing)` donde `framing` es `"legacy"` (Content-Length)
    o `"ndjson"` (un mensaje por línea). Devuelve `(None, "")` en EOF limpio.
    Lanza `ValueError` si el framing legacy está mal formado.

    El cliente elige su framing en el `initialize`:
    - Claude Code (MCP stdio clásico) usa `Content-Length: N\\r\\n\\r\\n<body>`.
    - opencode >= 1.18 y clientes que negocian `protocolVersion` 2025+
      usan NDJSON (un JSON por línea terminado en `\\n`).
    """
    first = stream.readline()
    if not first:
        return None, ""
    first_clean = first.rstrip(b"\r\n")

    if first_clean.lower().startswith(b"content-length:"):
        n = int(first_clean.split(b":", 1)[1].strip())
        # Descarta headers restantes hasta la línea vacía separadora.
        while True:
            line = stream.readline()
            if not line or line in (b"\r\n", b"\n"):
                break
        body = b""
        while len(body) < n:
            chunk = stream.read(n - len(body))
            if not chunk:
                raise ValueError("EOF antes de leer body completo")
            body += chunk
        return body, "legacy"

    # NDJSON: el mensaje entero está en esta línea (sin embedded newlines,
    # que el spec MCP prohíbe explícitamente).
    return first_clean, "ndjson"


def write_message(stream, body: bytes, framing: str) -> None:
    if framing == "legacy":
        stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode())
        stream.write(body)
    else:
        stream.write(body + b"\n")
    stream.flush()


def http_post(url: str, body: bytes, headers: dict[str, str], cafile: str | None) -> bytes:
    """POST síncrono; devuelve el body de la respuesta completo.

    Si la respuesta es `text/event-stream`, concatena todos los `data: <json>`
    en un único JSON array y devuelve ese array (cada elemento como un mensaje
    JSON-RPC independiente, preservando el orden).
    """
    ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={**headers, "Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"},
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
            status = r.status
            ctype = r.headers.get("Content-Type", "")
            raw = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        raise RuntimeError(f"HTTP {e.code}: {raw[:200]!r}") from None

    if status >= 400:
        raise RuntimeError(f"HTTP {status}: {raw[:200]!r}")
    if "text/event-stream" in ctype:
        events: list[bytes] = []
        for chunk in raw.split(b"\n\n"):
            for line in chunk.splitlines():
                if line.startswith(b"data:"):
                    payload = line[5:].lstrip()
                    if payload:
                        events.append(payload)
        return b"[" + b",".join(events) + b"]" if events else b"[]"
    return raw


def log(msg: str) -> None:
    sys.stderr.write(f"[mcp-pce] {msg}\n")
    sys.stderr.flush()


# --- Capa de caché semántica (0125) ---

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    """Tokeniza para TF-IDF: minúsculas, solo alfanumérico + underscore.

    Suficientemente granular para queries MCP (típicamente 5-30 tokens):
    `pri-ia-humano-decide` → `pri ia humano decide`; `search_catalog` →
    `search catalog`.
    """
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _canonical_params(params: Any) -> bytes:
    """Serialización canónica de `params` para hashing y vocabulario."""
    return json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _params_digest(method: str, params: Any) -> str:
    raw = method.encode() + b"\x00" + _canonical_params(params)
    return hashlib.sha256(raw).hexdigest()


def _response_id(response_bytes: bytes) -> str:
    """Identidad del body de respuesta (sin envelope `_meta`)."""
    return hashlib.sha256(response_bytes).hexdigest()


def _vocabulary_from_texts(texts: list[str]) -> dict[str, int]:
    vocab: dict[str, int] = {}
    for t in texts:
        for tok in _tokenize(t):
            if tok not in vocab:
                vocab[tok] = len(vocab)
    return vocab


def _tfidf_vector(text: str, vocab: dict[str, int], idf: dict[str, float]) -> dict[int, float]:
    """Vector TF-IDF disperso (dict[dim, peso]). Cero donde no hay token."""
    tokens = _tokenize(text)
    if not tokens:
        return {}
    tf = Counter(tokens)
    vec: dict[int, float] = {}
    for tok, count in tf.items():
        dim = vocab.get(tok)
        if dim is None:
            continue
        vec[dim] = (1.0 + math.log(count)) * idf.get(tok, 0.0)
    return vec


def _cosine(a: dict[int, float], b: dict[int, float]) -> float:
    if not a or not b:
        return 0.0
    dot = 0.0
    for k, va in a.items():
        vb = b.get(k)
        if vb is not None:
            dot += va * vb
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class SemanticCache:
    """Caché SQLite con dos niveles: L1 (exact) y L2 (TF-IDF coseno)."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        method TEXT NOT NULL,
        params_digest TEXT NOT NULL,
        response BLOB NOT NULL,
        response_digest TEXT NOT NULL,
        ts REAL NOT NULL,
        query_text TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_digest
        ON entries(method, params_digest);
    CREATE INDEX IF NOT EXISTS idx_response
        ON entries(response_digest);
    """

    def __init__(self, path: Path, ttl_secs: int, sim_threshold: float):
        self.path = path
        self.ttl_secs = ttl_secs
        self.sim_threshold = sim_threshold
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def invalidate_all(self) -> int:
        cur = self._conn.execute("DELETE FROM entries")
        self._conn.commit()
        return cur.rowcount

    def get_exact(self, method: str, params_digest: str, now: float) -> tuple[bytes | None, int | None]:
        row = self._conn.execute(
            "SELECT response, ts FROM entries "
            "WHERE method = ? AND params_digest = ? "
            "ORDER BY ts DESC LIMIT 1",
            (method, params_digest),
        ).fetchone()
        if row is None:
            return None, None
        response, ts = row
        age_ms = int((now - ts) * 1000)
        if age_ms / 1000 > self.ttl_secs:
            return None, age_ms
        return response, age_ms

    def get_semantic(
        self, method: str, query_text: str, now: float, max_candidates: int = 64,
    ) -> tuple[bytes | None, int | None, float | None]:
        """L2: candidatos recientes del mismo método, ranking por similitud TF-IDF."""
        candidates = self._conn.execute(
            "SELECT response, ts, query_text FROM entries "
            "WHERE method = ? AND ts > ? "
            "ORDER BY ts DESC LIMIT ?",
            (method, now - self.ttl_secs, max_candidates),
        ).fetchall()
        if not candidates:
            return None, None, None
        # Vocabulario + IDF sobre candidatos + query.
        all_texts = [c[2] for c in candidates] + [query_text]
        vocab = _vocabulary_from_texts(all_texts)
        if not vocab:
            return None, None, None
        N = len(candidates) + 1
        df = Counter()
        for t in all_texts:
            for tok in set(_tokenize(t)):
                df[tok] += 1
        idf = {tok: math.log((N + 1) / (df_t + 0.5)) + 1.0 for tok, df_t in df.items()}
        q_vec = _tfidf_vector(query_text, vocab, idf)
        best: tuple[float, bytes | None, float | None] = (0.0, None, None)
        for response, ts, qt in candidates:
            v = _tfidf_vector(qt, vocab, idf)
            sim = _cosine(q_vec, v)
            if sim > best[0]:
                best = (sim, response, ts)
        sim, response, ts = best
        if (
            sim >= self.sim_threshold
            and response is not None
            and ts is not None
        ):
            return response, int((now - ts) * 1000), sim
        return None, None, sim

    def put(self, method: str, params_digest: str, response: bytes,
            query_text: str, now: float) -> None:
        """Persiste la respuesta. Deduplica por `(method, params_digest, response_digest)`."""
        response_digest = _response_id(response)
        # Si la misma respuesta ya está bajo ese método/digest, no duplica.
        existing = self._conn.execute(
            "SELECT 1 FROM entries "
            "WHERE method = ? AND params_digest = ? AND response_digest = ? "
            "LIMIT 1",
            (method, params_digest, response_digest),
        ).fetchone()
        if existing:
            return
        self._conn.execute(
            "INSERT INTO entries(method, params_digest, response, response_digest, ts, query_text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (method, params_digest, response, response_digest, now, query_text),
        )
        self._conn.commit()


def _annotate_cached(message: Any, layer: str, age_ms: int, request_id: Any) -> Any:
    """Reetiqueta un mensaje cacheado con el `id` vigente y lo marca en `result`.

    El body persistido conserva el `id` de la petición que lo originó, de otra
    sesión. Servirlo tal cual le entrega al cliente una respuesta que no casa con
    ninguna petición en vuelo (`Received a response for an unknown message ID`) y
    la que sí espera nunca llega, así que expira por timeout. Toda respuesta
    (`result` o `error`) se reetiqueta con el `id` del request que la pide ahora;
    una notificación embebida en un array SSE no lleva `id` y queda intacta.

    `_meta` es una extensión válida del *payload* MCP (`result._meta`), no del
    envelope JSON-RPC: en la raíz es una clave desconocida y el cliente corta la
    conexión con `unrecognized_keys: ["_meta"]`. Un mensaje cuyo `result` no sea
    un objeto se deja sin anotar: no hay dónde hacerlo sin alterar el payload.
    """
    if not isinstance(message, dict):
        return message
    if "result" in message or "error" in message:
        message["id"] = request_id
    result = message.get("result")
    if isinstance(result, dict):
        meta = result.setdefault("_meta", {})
        meta["cached"] = True
        meta["cache_layer"] = layer
        meta["cache_age_ms"] = age_ms
    return message


def _envelope_cached(response_bytes: bytes, layer: str, age_ms: int, request_id: Any) -> bytes:
    """Reetiqueta la respuesta cacheada con `request_id` y la anota en `result._meta`.

    Un array (SSE agregado) se anota elemento por elemento, preservando su
    longitud: cada elemento es un mensaje JSON-RPC independiente y agregar uno
    extra le entregaría al cliente un mensaje sin `jsonrpc` ni `id`. Si el body
    no es JSON parseable (caso raro), se devuelve sin modificar — la caché es
    opt-in y nunca debe romper el contrato.
    """
    try:
        parsed = json.loads(response_bytes)
    except json.JSONDecodeError:
        return response_bytes
    if isinstance(parsed, list):
        parsed = [_annotate_cached(item, layer, age_ms, request_id) for item in parsed]
    else:
        parsed = _annotate_cached(parsed, layer, age_ms, request_id)
    return json.dumps(parsed, separators=(",", ":"), ensure_ascii=False).encode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bridge stdio↔HTTP para PCE con caché semántica local opcional",
        add_help=True,
    )
    parser.add_argument(
        "--invalidate", action="store_true",
        help="Vacía la caché antes de iniciar el bucle de stdio.",
    )
    parser.add_argument(
        "--cache-path", type=Path, default=None,
        help=f"Ruta del archivo SQLite de caché (default: {DEFAULT_CACHE_PATH}).",
    )
    parser.add_argument(
        "--cache-ttl-secs", type=int, default=None,
        help="TTL de las entradas en segundos (default: 3600, env: MCP_PCE_CACHE_TTL_SECS).",
    )
    parser.add_argument(
        "--cache-sim-threshold", type=float, default=None,
        help="Umbral de similitud coseno TF-IDF para L2 (default: 0.85, env: MCP_PCE_CACHE_SIM_THRESHOLD).",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Desactiva la caché semántica para esta corrida (reenvía siempre al servidor).",
    )
    parser.add_argument(
        "--purge", action="store_true",
        help=(
            "Vacía la caché y termina inmediatamente, sin entrar al bucle de stdio "
            "ni requerir PCE_MCP_API_KEY. Uso: purga puntual al enterarse de un "
            "cambio de gobernanza en pce-mcp, sin reiniciar la sesión MCP."
        ),
    )
    args = parser.parse_args(argv)

    env = os.environ.copy()
    env_from_file = load_env_file(ROOT / ".env")
    for k in ("PCE_MCP_API_KEY", "PCE_MCP_URL"):
        if not env.get(k):
            env[k] = env_from_file.get(k, "")

    cache_path = args.cache_path or Path(
        env.get("MCP_PCE_CACHE_PATH", str(DEFAULT_CACHE_PATH))
    )
    ttl_secs = (
        args.cache_ttl_secs
        if args.cache_ttl_secs is not None
        else int(env.get("MCP_PCE_CACHE_TTL_SECS", str(DEFAULT_TTL_SECS)))
    )
    sim_threshold = (
        args.cache_sim_threshold
        if args.cache_sim_threshold is not None
        else float(env.get("MCP_PCE_CACHE_SIM_THRESHOLD", str(DEFAULT_SIM_THRESHOLD)))
    )

    if args.purge:
        purge_cache = SemanticCache(cache_path, ttl_secs, sim_threshold)
        n = purge_cache.invalidate_all()
        purge_cache.close()
        log(f"cache purgada: {n} entradas eliminadas en {cache_path}")
        return 0

    key = env.get("PCE_MCP_API_KEY", "").strip()
    url = env.get("PCE_MCP_URL", "").strip() or DEFAULT_URL
    cafile_raw = (
        env.get("NODE_EXTRA_CA_CERTS", "").strip()
        or env.get("SSL_CERT_FILE", "").strip()
        or env.get("REQUESTS_CA_BUNDLE", "").strip()
    )
    cafile = os.path.expanduser(os.path.expandvars(cafile_raw)) if cafile_raw else None

    if not key:
        log("PCE_MCP_API_KEY vacía; saliendo con error")
        return 2

    cache: SemanticCache | None = None
    if not args.no_cache:
        cache = SemanticCache(cache_path, ttl_secs, sim_threshold)
        log(
            f"cache=on path={cache_path} ttl={ttl_secs}s sim>={sim_threshold} "
            f"({_cache_stats(cache)})"
        )
        if args.invalidate:
            n = cache.invalidate_all()
            log(f"cache invalidated: {n} entradas eliminadas")
    else:
        log("cache=off (--no-cache)")

    log(f"upstream={url} ca={'set' if cafile else 'system'}")
    is_jwt = key.startswith("eyJ")
    headers = {"Authorization": f"Bearer {key}"} if is_jwt else {"X-API-Key": key}
    if is_jwt:
        log("auth: Bearer JWT (detected)")

    sin = sys.stdin.buffer
    sout = sys.stdout.buffer

    try:
        while True:
            body, framing = read_message(sin)
            if body is None:
                log("stdin EOF")
                return 0
            now = time.time()
            cache_hit: tuple[bytes, str, int] | None = None
            try:
                req = json.loads(body)
                method = req.get("method", "") if isinstance(req, dict) else ""
                params = req.get("params", {}) if isinstance(req, dict) else {}
                is_notification = isinstance(req, dict) and "id" not in req
                request_id = req.get("id") if isinstance(req, dict) else None
            except json.JSONDecodeError:
                method, params, is_notification = "", {}, False
                request_id = None

            if cache is not None and method and method not in _UNCACHEABLE_METHODS:
                digest = _params_digest(method, params)
                query_text = method + " " + _canonical_params(params).decode("utf-8", "replace")
                cached, age = cache.get_exact(method, digest, now)
                if cached is not None and age is not None:
                    log(f"L1 hit method={method} age_ms={age}")
                    cache_hit = (cached, "L1", age)
                else:
                    cached, age, sim = cache.get_semantic(method, query_text, now)
                    if cached is not None and age is not None:
                        log(f"L2 hit method={method} age_ms={age} sim={sim:.3f}")
                        cache_hit = (cached, "L2", age)

            if cache_hit is not None:
                response_bytes = _envelope_cached(*cache_hit, request_id)
                stripped = response_bytes.lstrip()
                if stripped.startswith(b"["):
                    try:
                        arr = json.loads(response_bytes)
                    except json.JSONDecodeError:
                        write_message(sout, response_bytes, framing)
                    else:
                        if not isinstance(arr, list):
                            write_message(sout, response_bytes, framing)
                        else:
                            for item in arr:
                                write_message(
                                    sout,
                                    json.dumps(item).encode(),
                                    framing,
                                )
                else:
                    write_message(sout, response_bytes, framing)
                continue

            try:
                resp = http_post(url, body, headers, cafile)
            except Exception as e:
                log(f"upstream error: {e}")
                try:
                    req = json.loads(body)
                    err = {
                        "jsonrpc": "2.0",
                        "id": req.get("id"),
                        "error": {"code": -32603, "message": f"upstream: {e}"},
                    }
                    write_message(sout, json.dumps(err).encode(), framing)
                except Exception:
                    pass
                continue

            # Una notificación no lleva `id` y por tanto no admite respuesta: el
            # servidor la acusa con `202` y `content-length: 0`. Reenviar ese body
            # vacío al cliente le entrega un mensaje sin JSON (`Content-Length: 0`
            # en framing legacy, una línea vacía en NDJSON), que desalinea el
            # stream y deja colgado el siguiente request del handshake.
            if is_notification or not resp.strip():
                continue

            # Persistir en caché para llamadas futuras.
            if cache is not None and method and method not in _UNCACHEABLE_METHODS:
                digest = _params_digest(method, params)
                query_text = method + " " + _canonical_params(params).decode("utf-8", "replace")
                try:
                    cache.put(method, digest, resp, query_text, now)
                except sqlite3.DatabaseError as e:
                    log(f"cache.put failed: {e}")

            # Si la respuesta es un array SSE, emitimos cada elemento
            # como un mensaje JSON-RPC independiente por stdio.
            stripped = resp.lstrip()
            if stripped.startswith(b"["):
                try:
                    arr = json.loads(resp)
                except json.JSONDecodeError:
                    write_message(sout, resp, framing)
                else:
                    if not isinstance(arr, list):
                        write_message(sout, resp, framing)
                    else:
                        for item in arr:
                            write_message(sout, json.dumps(item).encode(), framing)
            else:
                write_message(sout, resp, framing)
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        if cache is not None:
            cache.close()


def _cache_stats(cache: SemanticCache) -> str:
    """Resumen rápido del estado de la caché para el log de arranque."""
    try:
        row = cache._conn.execute(
            "SELECT COUNT(*), COALESCE(MIN(ts), 0), COALESCE(MAX(ts), 0) "
            "FROM entries"
        ).fetchone()
        if row is None:
            return "0 entradas"
        n, lo, hi = row
        return f"{n} entradas, ts {lo:.0f}..{hi:.0f}"
    except sqlite3.DatabaseError:
        return "consulta fallida"


if __name__ == "__main__":
    sys.exit(main())
