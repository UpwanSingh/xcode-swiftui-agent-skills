"""End-to-end tests: spawn the MCP server over stdio and speak JSON-RPC to it."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import APP, ROOT  # noqa: E402

SERVER = os.path.join(ROOT, "mcp-server", "server.py")


class McpSession:
    def __init__(self, env_roots=None, cwd=APP, extra_env=None):
        env = dict(os.environ)
        env.pop("IOS_DEBUG_MCP_ROOTS", None)
        if env_roots is not None:
            env["IOS_DEBUG_MCP_ROOTS"] = os.pathsep.join(env_roots)
        env.update(extra_env or {})
        self.proc = subprocess.Popen([sys.executable, SERVER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, cwd=cwd, env=env)
        self.next_id = 0

    def send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def recv(self):
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError("server closed stdout: " + self.proc.stderr.read())
        return json.loads(line)

    def request(self, method, params=None):
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}})
        while True:
            msg = self.recv()
            if msg.get("id") == self.next_id and "method" not in msg:
                return msg

    def call(self, name, arguments=None):
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}})["result"]
        return result, result["content"][0]["text"]

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        self.proc.stdout.close()
        self.proc.stderr.close()


class McpServerTests(unittest.TestCase):
    def setUp(self):
        self.s = McpSession(env_roots=[APP])
        self.addCleanup(self.s.close)
        init = self.s.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                             "clientInfo": {"name": "test", "version": "0"}})
        self.init = init["result"]
        self.s.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def test_initialize(self):
        self.assertEqual(self.init["protocolVersion"], "2025-06-18")
        self.assertIn("tools", self.init["capabilities"])
        self.assertEqual(self.init["serverInfo"]["name"], "ios-debug-mcp")

    def test_unknown_protocol_version_gets_latest(self):
        s = McpSession(env_roots=[APP])
        try:
            r = s.request("initialize", {"protocolVersion": "1999-01-01", "capabilities": {}})
            self.assertEqual(r["result"]["protocolVersion"], "2025-11-25")
        finally:
            s.close()

    def test_tools_list_is_read_only(self):
        tools = self.s.request("tools/list")["result"]["tools"]
        self.assertGreaterEqual(len(tools), 20)
        for t in tools:
            self.assertTrue(t["annotations"]["readOnlyHint"], t["name"])
            self.assertFalse(t["annotations"]["destructiveHint"], t["name"])
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertFalse(t["inputSchema"]["additionalProperties"])

    def test_swiftui_lint_tool(self):
        result, text = self.s.call("swiftui_lint", {"paths": ["App"]})
        self.assertFalse(result["isError"], text)
        self.assertIn("SUI004", [f["rule"] for f in json.loads(text)["findings"]])

    def test_extract_build_errors_from_text(self):
        result, text = self.s.call("extract_build_errors", {"text": "error: No profiles for 'com.x' were found\n"})
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(text)["errors"][0]["id"], "PROV001")

    def test_firebase_config_masks_keys(self):
        result, text = self.s.call("check_firebase_config", {})
        self.assertFalse(result["isError"], text)
        self.assertNotIn("FAKE-TEST-KEY-0000000000000000000", text)
        self.assertIn("FB004", text)

    def test_read_plist_redacts(self):
        result, text = self.s.call("read_plist", {"path": "App/GoogleService-Info.plist"})
        self.assertFalse(result["isError"], text)
        self.assertIn("REDACTED", json.loads(text)["data"]["API_KEY"])

    def test_path_outside_roots_is_refused(self):
        result, text = self.s.call("read_plist", {"path": "/etc/hosts"})
        self.assertTrue(result["isError"])
        self.assertIn("outside the allowed roots", text)
        result, text = self.s.call("swiftui_lint", {"paths": ["../../../"]})
        self.assertTrue(result["isError"])

    def test_secret_files_are_refused(self):
        for name in ("signing.p12", "AuthKey_ABC123.p8", ".env", "service-account.json"):
            path = os.path.join(APP, name)
            with open(path, "w") as fh:
                fh.write("secret")
            try:
                result, text = self.s.call("scan_runtime_log", {"path": name})
                self.assertTrue(result["isError"], name)
                self.assertIn("credentials", text)
            finally:
                os.unlink(path)

    def test_argument_validation(self):
        result, text = self.s.call("list_crash_reports", {"limit": "ten"})
        self.assertTrue(result["isError"])
        self.assertIn("integer", text)
        result, text = self.s.call("summarize_crash_report", {})
        self.assertTrue(result["isError"])
        self.assertIn("missing required argument", text)
        result, text = self.s.call("simulator_logs", {"process": 'x" OR 1==1'})
        self.assertTrue(result["isError"])

    def test_unknown_tool_and_method(self):
        self.assertEqual(self.s.request("tools/call", {"name": "rm_rf", "arguments": {}})["error"]["code"], -32602)
        self.assertEqual(self.s.request("does/not/exist")["error"]["code"], -32601)
        self.assertEqual(self.s.request("ping")["result"], {})

    def test_parse_error_does_not_kill_session(self):
        self.s.proc.stdin.write("{not json\n")
        self.s.proc.stdin.flush()
        self.assertEqual(self.s.recv()["error"]["code"], -32700)
        self.assertEqual(self.s.request("ping")["result"], {})


class McpRootsTests(unittest.TestCase):
    def test_client_roots_define_allowed_paths(self):
        s = McpSession(env_roots=None, cwd=ROOT)
        try:
            s.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {"roots": {"listChanged": True}}})
            s.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            req = s.recv()
            self.assertEqual(req["method"], "roots/list")
            s.send({"jsonrpc": "2.0", "id": req["id"], "result": {"roots": [{"uri": "file://" + APP}]}})
            result, text = s.call("read_plist", {"path": "App/Info.plist"})
            self.assertFalse(result["isError"], text)
            result, text = s.call("read_plist", {"path": os.path.join(ROOT, "skills", "xcode-build-signing",
                                                                       "assets", "ExportOptions.plist")})
            self.assertTrue(result["isError"])  # repo root is no longer an allowed root
        finally:
            s.close()


class McpCliTests(unittest.TestCase):
    def test_list_tools_and_one_shot_call(self):
        out = subprocess.run([sys.executable, SERVER, "--list-tools"], capture_output=True, text=True, check=True)
        self.assertIn("summarize_crash_report", out.stdout)
        env = dict(os.environ, IOS_DEBUG_MCP_ROOTS=APP)
        proc = subprocess.run([sys.executable, SERVER, "--call", "lint_firebase_rules",
                               "--args", json.dumps({"today": "2026-11-21"})],
                              capture_output=True, text=True, cwd=APP, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("FR002", proc.stdout)


if __name__ == "__main__":
    unittest.main()
