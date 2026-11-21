"""End-to-end tests: spawn the MCP server over stdio and speak JSON-RPC to it."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import APP, ROOT  # noqa: E402

SERVER = os.path.join(ROOT, "mcp-server", "server.py")
sys.path.insert(0, os.path.join(ROOT, "mcp-server"))
from ios_debug_mcp import safety, tools  # noqa: E402
from ios_debug_mcp.server import Server  # noqa: E402

RECV_TIMEOUT = 60  # a server that stops answering fails the test instead of hanging CI


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
        self.lines: "queue.Queue[str]" = queue.Queue()
        self.stderr: list = []
        self.threads = [threading.Thread(target=self._pump, args=(self.proc.stdout, self.lines.put), daemon=True),
                        threading.Thread(target=self._pump, args=(self.proc.stderr, self.stderr.append), daemon=True)]
        for t in self.threads:
            t.start()

    @staticmethod
    def _pump(stream, sink):
        for line in stream:
            sink(line)
        sink("")

    def send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def recv(self):
        try:
            line = self.lines.get(timeout=RECV_TIMEOUT)
        except queue.Empty:
            self.proc.kill()
            raise AssertionError(f"no reply within {RECV_TIMEOUT}s; stderr: {''.join(self.stderr)[-2000:]}")
        if not line:
            raise AssertionError("server closed stdout: " + "".join(self.stderr)[-2000:])
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
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        for t in self.threads:
            t.join(timeout=10)
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
        with tempfile.TemporaryDirectory() as d:  # never write credential-shaped files into the repo
            s = McpSession(env_roots=[d], cwd=d)
            try:
                s.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
                for name in ("signing.p12", "AuthKey_ABC123.p8", ".env", "service-account.json"):
                    with open(os.path.join(d, name), "w") as fh:
                        fh.write("secret")
                    result, text = s.call("scan_runtime_log", {"path": name})
                    self.assertTrue(result["isError"], name)
                    self.assertIn("credential", text)
            finally:
                s.close()

    def test_server_redacts_and_truncates_tool_output(self):
        token = "github_" + "pat_" + "Z" * 30
        result, text = self.s.call("extract_build_errors", {"text": f"error: push failed with {token}\n"})
        self.assertFalse(result["isError"])
        self.assertNotIn(token, text)
        s = McpSession(env_roots=[APP], extra_env={"IOS_DEBUG_MCP_MAX_OUTPUT": "200"})
        try:
            s.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
            _, text = s.call("swiftui_lint", {"paths": ["App"]})
            self.assertIn("[truncated", text)
            self.assertLess(len(text), 400)
        finally:
            s.close()

    def test_every_validate_branch(self):
        cases = [("list_crash_reports", {"limit": 0}, "between"), ("list_crash_reports", {"bogus": 1}, "unknown argument"),
                 ("simulator_logs", {"level": "trace"}, "must be one of"), ("swiftui_lint", {"paths": "App"}, "type array"),
                 ("swiftui_lint", {"paths": [1]}, "array of strings"), ("list_simulators", {"booted_only": "yes"}, "boolean"),
                 ("extract_build_errors", {"text": "x" * (safety.MAX_TEXT_INPUT + 1)}, "larger than")]
        for tool, args, message in cases:
            result, text = self.s.call(tool, args)
            self.assertTrue(result["isError"], (tool, args))
            self.assertIn(message, text, (tool, args))

    def test_malformed_input_does_not_kill_the_server(self):
        self.s.proc.stdin.write("[" * 100000 + "\n")
        self.s.proc.stdin.flush()
        self.assertEqual(self.s.recv()["error"]["code"], -32700)
        self.assertEqual(self.s.request("ping", [1])["error"]["code"], -32602)  # params must be an object
        result, _ = self.s.call("scan_runtime_log", {"text": "\ud800 Modifying state during view update"})
        self.assertFalse(result["isError"])
        self.assertEqual(self.s.request("ping")["result"], {})

    def test_symlinks_inside_a_root_cannot_reach_outside(self):
        with tempfile.TemporaryDirectory() as d:
            root, outside = os.path.join(d, "root"), os.path.join(d, "outside")
            os.makedirs(os.path.join(root, "App"))
            os.makedirs(os.path.join(outside, ".ssh"))
            with open(os.path.join(outside, "Secret.swift"), "w") as fh:
                fh.write("var STOLEN_VALUE = 1\n")
            with open(os.path.join(outside, "GoogleService-Info.plist"), "w") as fh:
                fh.write('<?xml version="1.0"?><plist version="1.0"><dict><key>PROJECT_ID</key>'
                         "<string>STOLEN_PROJECT</string></dict></plist>")
            with open(os.path.join(root, "App", "Ok.swift"), "w") as fh:
                fh.write("import FirebaseCore\nstruct Ok {}\n")
            os.symlink(os.path.join(outside, "Secret.swift"), os.path.join(root, "App", "Leak.swift"))
            os.symlink(os.path.join(outside, "GoogleService-Info.plist"), os.path.join(root, "App", "GoogleService-Info.plist"))
            s = McpSession(env_roots=[root], cwd=root, extra_env={"IOS_DEBUG_MCP_SYSTEM_DIRS": "0"})
            try:
                s.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
                for tool, args in (("swiftui_lint", {"paths": ["App"]}), ("swift_concurrency_check", {"paths": ["App"]}),
                                   ("mapkit_check", {"paths": ["App"]}), ("check_firebase_config", {}),
                                   ("check_bundle_resources", {"sources": ["App"]}), ("find_xcode_projects", {})):
                    _, text = s.call(tool, args)
                    self.assertNotIn("STOLEN", text, tool)
                result, text = s.call("read_plist", {"path": "App/GoogleService-Info.plist"})
                self.assertTrue(result["isError"])  # the direct path is refused too
            finally:
                s.close()

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


def _tree_digest(root):
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(dirnames + filenames):
            path = os.path.join(dirpath, name)
            h.update(os.path.relpath(path, root).encode())
            if os.path.isfile(path):
                with open(path, "rb") as fh:
                    h.update(fh.read())
    return h.hexdigest()


class ReadOnlyTests(unittest.TestCase):
    def test_tools_leave_the_project_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "BuggyApp")
            shutil.copytree(APP, root)
            before = _tree_digest(root)
            policy = safety.PathPolicy(env={"IOS_DEBUG_MCP_ROOTS": root, "IOS_DEBUG_MCP_SYSTEM_DIRS": "0", "HOME": d})
            calls = [("find_xcode_projects", {}), ("swiftui_lint", {}), ("swift_concurrency_check", {}),
                     ("mapkit_check", {"plist": "App/Info.plist"}), ("check_firebase_config", {}),
                     ("lint_firebase_rules", {"sources": ["App"]}), ("check_bundle_resources", {"sources": ["App"]}),
                     ("check_info_plist", {"plist": "App/Info.plist", "sources": ["App"], "project": "BuggyApp.xcodeproj"}),
                     ("read_plist", {"path": "App/Info.plist"}), ("scan_runtime_log", {"text": "x"}),
                     ("extract_build_errors", {"text": "error: x"})]
            for name, args in calls:
                tools.call(name, args, policy)
            self.assertEqual(_tree_digest(root), before)

    def test_build_settings_are_masked_by_key(self):
        c = tools.common()
        original = c.run_command
        c.run_command = lambda argv, timeout=60: (0, json.dumps([{"target": "App", "buildSettings": {
            "SENTRY_AUTH_TOKEN": "abcdefghijklmnopqrstuvwxyz", "PRODUCT_NAME": "App"}}]), "")
        try:
            policy = safety.PathPolicy(env={"IOS_DEBUG_MCP_ROOTS": APP, "IOS_DEBUG_MCP_SYSTEM_DIRS": "0"})
            r = tools.call("show_build_settings", {"path": "BuggyApp.xcodeproj", "all": True}, policy)
        finally:
            c.run_command = original
        settings = r["targets"][0]["settings"]
        self.assertEqual(settings["PRODUCT_NAME"], "App")
        self.assertIn("REDACTED", settings["SENTRY_AUTH_TOKEN"])


class PathPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.realpath(os.path.join(self.tmp, "root"))
        os.makedirs(self.root)
        self.policy = safety.PathPolicy(env={"IOS_DEBUG_MCP_ROOTS": self.root, "IOS_DEBUG_MCP_SYSTEM_DIRS": "0"})

    def touch(self, rel):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        return path

    def assertRefused(self, rel, message, **kw):
        with self.assertRaises(safety.PolicyError) as ctx:
            self.policy.resolve(rel, **kw)
        self.assertIn(message, str(ctx.exception), rel)

    def test_credential_file_names(self):
        for name in ("id_rsa", "id_ed25519.pub", ".netrc", "x.pem", "x.key", "x.p12", "x.p8", "x.jks", ".env",
                     ".env.local", "credentials.json", "service-account-prod.json", "app-firebase-adminsdk-1.json",
                     "AuthKey_ABC.p8", "login.keychain-db"):
            self.touch(name)
            self.assertRefused(name, "credential file name")

    def test_protected_directories_case_insensitive(self):
        for rel in (".ssh/config", ".SSH/config", ".aws/credentials.txt", ".git/config", "Keychains/a.plist",
                    "KEYCHAINS/b.plist", ".gnupg/x.txt"):
            self.touch(rel)
            self.assertRefused(rel, "protected directory")

    def test_symlink_out_of_root_is_refused(self):
        outside = os.path.join(self.tmp, "outside.plist")
        open(outside, "w").close()
        os.symlink(outside, os.path.join(self.root, "link.plist"))
        self.assertRefused("link.plist", "outside the allowed roots")
        self.assertFalse(self.policy.allows(os.path.join(self.root, "link.plist")))
        self.assertTrue(self.policy.allows(self.touch("ok.plist")))

    def test_extension_and_existence(self):
        self.touch("a.txt")
        self.assertRefused("a.txt", "is not one of", exts=(".plist",))
        self.assertRefused("missing.plist", "no such file")
        self.assertRefused("a.txt", "expected a directory", kind="dir")

    def test_system_dirs_can_be_disabled(self):
        self.assertFalse(self.policy.include_system)
        with self.assertRaises(safety.PolicyError):
            tools.call("find_build_logs", {}, self.policy)
        on = safety.PathPolicy(env={"IOS_DEBUG_MCP_ROOTS": self.root})
        self.assertTrue(any("DerivedData" in r for r in on.allowed_roots))

    def test_broad_cwd_fallback_is_refused(self):
        home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(home, "proj"))
        for cwd in ("/", home, self.tmp):  # root, home, and an ancestor of home
            p = safety.PathPolicy(env={"HOME": home}, cwd=cwd)
            with self.assertRaises(safety.PolicyError) as ctx:
                p.project_roots
            self.assertIn("too broad", str(ctx.exception))
        ok = safety.PathPolicy(env={"HOME": home}, cwd=os.path.join(home, "proj"))
        self.assertEqual(ok.project_roots, [os.path.realpath(os.path.join(home, "proj"))])

    def test_env_roots_take_precedence_over_client_roots(self):
        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        self.policy.set_client_roots(["file://" + other])
        self.assertEqual(self.policy.project_roots, [self.root])


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
    def test_call_exit_codes(self):
        env = dict(os.environ, IOS_DEBUG_MCP_ROOTS=APP)
        run = lambda *a: subprocess.run([sys.executable, SERVER, *a], capture_output=True, text=True, cwd=APP, env=env)
        self.assertEqual(run("--call", "read_plist", "--args", '{"path": "/etc/hosts"}').returncode, 1)
        self.assertEqual(run("--call", "nope").returncode, 2)
        self.assertEqual(run("--call", "ping_tool", "--args", "{not json").returncode, 2)

    def test_missing_skills_dir_is_reported(self):
        env = dict(os.environ, IOS_DEBUG_MCP_ROOTS=APP, IOS_DEBUG_SKILLS_DIR=os.path.join(APP, "nope"))
        proc = subprocess.run([sys.executable, SERVER, "--call", "swiftui_lint", "--args", "{}"],
                              capture_output=True, text=True, cwd=APP, env=env)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("IOS_DEBUG_SKILLS_DIR", proc.stdout)

    def test_broad_working_directory_is_refused(self):
        env = {k: v for k, v in os.environ.items() if k != "IOS_DEBUG_MCP_ROOTS"}
        proc = subprocess.run([sys.executable, SERVER, "--call", "swiftui_lint", "--args", "{}"],
                              capture_output=True, text=True, cwd=os.path.expanduser("~"), env=env)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("too broad", proc.stdout)

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
