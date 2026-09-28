#!/usr/bin/env python3
"""Stdio MCP stub for the preflight suite (unit 0114, CA-02).

It is the "local test endpoint" that CA-02 asks for: the four BLOCK causes of the
MCP row are induced with the **same** configuration mechanism the pilot already
uses for `ausente` (an inline `{"mcpServers": {"pce-mcp": {"command": "false"}}}`
config), so the preflight sees a real stdio server and not a patched function.

Usage
-----
    stub_knowledge_server.py --mode ok|unauthorized|budget|hang|exit-401

Modes
-----
* `ok`           — answers `initialize` and `tools/call resolve_entity` with the
                   entity. Emits a `notifications/message` frame **before every**
                   response, which is what proves the client keeps reading until the
                   frame carrying `result`/`error` for the id it asked for.
* `unauthorized` — answers `initialize`; `tools/call` comes back `isError` with a
                   `401 Unauthorized` text.
* `budget`       — answers `initialize`; `tools/call` comes back `isError` with the
                   knowledge-router's budget-exhausted text.
* `hang`         — never answers anything (the `timeout` cause).
* `exit-401`     — dies before answering `initialize`, leaving `401 Unauthorized` on
                   stderr (the cause `401` seen as a process that never starts).

Framing is MCP stdio: one JSON-RPC object per line, on stdin and on stdout.
Exit codes: `0` normal end of stream, `1` in `exit-401`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

EXIT_OK = 0
EXIT_UNAUTHORIZED = 1

#: Payload of a successful `resolve_entity`. The antidrift fields are here on
#: purpose: the preflight must reach PASS without reading either of them.
ENTITY_EXTRA = {"sourceDigest": "stub-source", "pointerDigest": "stub-pointer"}


def write_frame(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def notification() -> None:
    """A frame with `method` and no `id`: the client must skip it, not decode it
    as the answer."""
    write_frame({
        "jsonrpc": "2.0",
        "method": "notifications/message",
        "params": {"level": "info", "data": "stub: trabajando"},
    })


def tool_result(text: str, *, is_error: bool) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def entity_text(arguments: dict) -> str:
    entity_id = ""
    for key in ("entity_id", "entityId", "id"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            entity_id = value
            break
    return json.dumps({"id": entity_id, "type": "principio", **ENTITY_EXTRA},
                      ensure_ascii=False)


def answer(mode: str, request: dict) -> dict | None:
    method = request.get("method", "")
    request_id = request.get("id")
    if request_id is None:
        return None                       # a notification from the client
    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stub-knowledge", "version": "0"},
            },
        }
    if method == "tools/call":
        arguments = request.get("params", {}).get("arguments", {}) or {}
        if mode == "unauthorized":
            result = tool_result("401 Unauthorized: credencial rechazada por la PCE",
                                 is_error=True)
        elif mode == "budget":
            result = tool_result("budget exhausted: presupuesto agotado para pce-mcp",
                                 is_error=True)
        else:
            result = tool_result(entity_text(arguments), is_error=False)
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    return {
        "jsonrpc": "2.0", "id": request_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def serve(mode: str) -> int:
    if mode == "exit-401":
        sys.stderr.write("401 Unauthorized\n")
        sys.stderr.flush()
        return EXIT_UNAUTHORIZED
    if mode == "hang":
        while True:                       # the parent kills it when its budget ends
            time.sleep(3600)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = answer(mode, request)
        if response is None:
            continue
        if mode == "ok":
            notification()
        write_frame(response)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stub_knowledge_server.py")
    parser.add_argument("--mode", required=True,
                        choices=["ok", "unauthorized", "budget", "hang", "exit-401"])
    args = parser.parse_args(argv)
    return serve(args.mode)


if __name__ == "__main__":
    sys.exit(main())
