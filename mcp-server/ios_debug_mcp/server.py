"""MCP stdio transport and lifecycle (JSON-RPC 2.0, newline-delimited), stdlib only.

Implements: initialize / notifications/initialized, ping, tools/list, tools/call, and the
client `roots` capability (roots/list + notifications/roots/list_changed) so the readable
project directories follow the workspace the client has open.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback
from typing import Any, Dict, Optional

from . import __version__
from .safety import PathPolicy, PolicyError, truncate
from .tools import BY_NAME, TOOLS, ArgumentError, SkillsNotFound, call, common

SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
ROOTS_REQUEST_PREFIX = "ios-debug-mcp/roots/"

INSTRUCTIONS = """Read-only iOS/Xcode diagnostics. Nothing here builds, signs, installs, erases or edits files.
Typical flow: xcode_environment -> find_xcode_projects -> the domain tool (extract_build_errors,
check_info_plist, compare_entitlements, swiftui_lint, swift_concurrency_check, check_firebase_config,
lint_firebase_rules, mapkit_check, summarize_crash_report, simulator_logs). Paths must be inside the
project roots or Xcode's DerivedData/DiagnosticReports/provisioning folders. Findings are heuristics:
confirm against the code before editing."""

log = logging.getLogger("ios-debug-mcp")


class Server:
    def __init__(self, policy: Optional[PathPolicy] = None, out=None, max_output: Optional[int] = None):
        self.policy = policy or PathPolicy()
        self.out = out or sys.stdout
        self.max_output = max_output or int(os.environ.get("IOS_DEBUG_MCP_MAX_OUTPUT", "60000"))
        self.client_caps: Dict[str, Any] = {}
        self.protocol_version = SUPPORTED_VERSIONS[0]
        self._request_seq = 0

    # ------------------------------------------------------------------ transport
    def send(self, message: Dict[str, Any]) -> None:
        # ensure_ascii: a lone surrogate from a tool result must not crash the write.
        self.out.write(json.dumps(message, default=str) + "\n")
        self.out.flush()

    def serve(self, stream=None) -> None:
        stream = stream or sys.stdin
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except (ValueError, RecursionError):  # RecursionError: absurdly nested input
                self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
                continue
            batch = message if isinstance(message, list) else [message]
            replies = [r for r in (self._safe_handle(m) for m in batch) if r is not None]
            if isinstance(message, list) and replies:
                self.send(replies)  # type: ignore[arg-type]
            else:
                for r in replies:
                    self.send(r)

    # ------------------------------------------------------------------ dispatch
    def _safe_handle(self, msg: Any) -> Optional[Dict[str, Any]]:
        try:
            return self.handle(msg)
        except Exception:  # never let one bad message kill the session
            log.error("unhandled error:\n%s", traceback.format_exc())
            mid = msg.get("id") if isinstance(msg, dict) else None
            return self._error(mid, -32603, "internal error") if mid is not None else None

    def handle(self, msg: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return self._error(None, -32600, "invalid request")
        if "method" not in msg:
            self._on_response(msg)
            return None
        method, params, mid = msg["method"], msg.get("params") or {}, msg.get("id")
        is_notification = "id" not in msg
        if not isinstance(method, str) or not isinstance(params, dict):
            return None if is_notification else self._error(mid, -32602, "method must be a string and params an object")
        try:
            if method == "initialize":
                result = self._initialize(params)
            elif method == "notifications/initialized":
                self._request_roots()
                return None
            elif method == "notifications/roots/list_changed":
                self._request_roots()
                return None
            elif method.startswith("notifications/"):
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [t.describe() for t in TOOLS]}
            elif method == "tools/call":
                result = self._call_tool(params)
            elif method == "resources/list":
                result = {"resources": []}
            elif method == "resources/templates/list":
                result = {"resourceTemplates": []}
            elif method == "prompts/list":
                result = {"prompts": []}
            else:
                return None if is_notification else self._error(mid, -32601, f"method not found: {method}")
        except _RpcError as exc:
            return None if is_notification else self._error(mid, exc.code, str(exc))
        return None if is_notification else {"jsonrpc": "2.0", "id": mid, "result": result}

    @staticmethod
    def _error(mid: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    def _initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        requested = params.get("protocolVersion")
        self.protocol_version = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        self.client_caps = params.get("capabilities") or {}
        client = (params.get("clientInfo") or {}).get("name", "unknown client")
        log.info("initialize from %s (protocol %s)", client, self.protocol_version)
        return {
            "protocolVersion": self.protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "ios-debug-mcp", "title": "iOS / Xcode Debug Tools", "version": __version__},
            "instructions": INSTRUCTIONS,
        }

    def _request_roots(self) -> None:
        if "roots" not in self.client_caps or self.policy.env_roots:
            return
        self._request_seq += 1
        self.send({"jsonrpc": "2.0", "id": f"{ROOTS_REQUEST_PREFIX}{self._request_seq}", "method": "roots/list"})

    def _on_response(self, msg: Dict[str, Any]) -> None:
        if str(msg.get("id", "")).startswith(ROOTS_REQUEST_PREFIX):
            roots = (msg.get("result") or {}).get("roots") or []
            self.policy.set_client_roots(r.get("uri", "") for r in roots if isinstance(r, dict))
            log.info("client roots: %s", self.policy.project_roots)

    def _call_tool(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        if name not in BY_NAME:
            raise _RpcError(-32602, f"unknown tool: {name}")
        try:
            payload = call(name, params.get("arguments"), self.policy)
            text = json.dumps(payload, indent=1, ensure_ascii=False, default=str)
            is_error = False
        except (ArgumentError, PolicyError, SkillsNotFound) as exc:
            text, is_error = f"{name}: {exc}", True
        except Exception as exc:  # skill scripts raise UsageError for bad input / missing Xcode
            if type(exc).__name__ == "UsageError":
                text = f"{name}: {exc}"
            else:
                log.error("tool %s crashed:\n%s", name, traceback.format_exc())
                text = f"{name}: internal error: {type(exc).__name__}: {exc}"
            is_error = True
        try:
            text = common().redact_text(text)
        except SkillsNotFound as exc:
            # Without the helper there is no redaction, so never return tool output; our own
            # error messages carry no file content.
            text, is_error = (text if is_error else f"{name}: {exc}"), True
        text = truncate(text, self.max_output)
        return {"content": [{"type": "text", "text": text}], "isError": is_error}


class _RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Read-only MCP server for iOS/Xcode diagnostics (stdio).")
    p.add_argument("--version", action="store_true", help="print version and exit")
    p.add_argument("--list-tools", action="store_true", help="print tool names and exit")
    p.add_argument("--call", metavar="TOOL", help="run one tool without an MCP client (debugging)")
    p.add_argument("--args", default="{}", help="JSON arguments for --call")
    args = p.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=os.environ.get("IOS_DEBUG_MCP_LOG", "WARNING").upper(),
                        format="[ios-debug-mcp] %(levelname)s %(message)s")
    if args.version:
        print(__version__)
        return 0
    if args.list_tools:
        for t in TOOLS:
            print(f"{t.name:<30} {t.title}")
        return 0
    server = Server()
    if args.call:
        try:
            arguments = json.loads(args.args)
        except ValueError as exc:
            print(f"--args is not valid JSON: {exc}", file=sys.stderr)
            return 2
        reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": args.call, "arguments": arguments}})
        if "error" in reply:
            print(reply["error"]["message"], file=sys.stderr)
            return 2
        print(reply["result"]["content"][0]["text"])
        return 1 if reply["result"]["isError"] else 0
    try:
        server.serve()
    except KeyboardInterrupt:
        pass
    return 0
