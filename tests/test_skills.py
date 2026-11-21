"""Behavioural tests for every skill script against the BuggyApp fixture (stdlib unittest).

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import APP, FIXTURES, ROOT, load_script, rules, write_profile  # noqa: E402

lint = load_script("swiftui-state-layout", "swiftui_lint")
conc = load_script("swiftui-state-layout", "concurrency_check")
builderr = load_script("xcode-build-signing", "extract_build_errors")
plist = load_script("xcode-build-signing", "check_plist")
signing = load_script("xcode-build-signing", "inspect_signing")
resources = load_script("xcode-build-signing", "check_bundle_resources")
firebase = load_script("ios-map-firebase-diagnostics", "check_firebase_config")
fbrules = load_script("ios-map-firebase-diagnostics", "lint_firebase_rules")
mapkit = load_script("ios-map-firebase-diagnostics", "mapkit_check")
crash = load_script("ios-crash-log-triage", "crash_report")
simlogs = load_script("ios-crash-log-triage", "sim_logs")


class SwiftUILintTests(unittest.TestCase):
    def setUp(self):
        self.result = lint.analyze([os.path.join(APP, "App")])

    def test_every_rule_fires_on_fixture(self):
        self.assertEqual(sorted(set(rules(self.result))), sorted(lint.RULES))

    def test_comments_and_strings_are_ignored(self):
        with open(os.path.join(APP, "App", "ContentView.swift")) as fh:
            negatives = [i for i, line in enumerate(fh, 1) if "must not be reported" in line]
        self.assertEqual(len(negatives), 2)
        lines = {f["line"] for f in self.result["findings"] if f["file"].endswith("ContentView.swift")}
        self.assertFalse(lines & set(negatives))

    def test_state_seeded_from_constant_is_not_sui002(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "A.swift")
            with open(p, "w") as fh:
                fh.write("struct A: App {\n  @State private var m: M\n  init() { _m = State(initialValue: M()) }\n}\n")
            self.assertNotIn("SUI002", rules(lint.analyze([p])))

    def test_clean_code_has_no_findings(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "Clean.swift")
            with open(p, "w") as fh:
                fh.write("""import SwiftUI
@MainActor @Observable final class Model { var items: [Item] = [] }
struct Feed: View {
    @State private var model = Model()
    var body: some View {
        NavigationStack {
            List(model.items) { item in NavigationLink(item.name, value: item.id) }
                .navigationTitle("Feed")
                .navigationDestination(for: Item.ID.self) { DetailView(id: $0) }
        }
    }
}
""")
            self.assertEqual(lint.analyze([p])["findings"], [])

    def test_cli_exit_codes(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(lint.main([os.path.join(APP, "App")]), 1)
            self.assertEqual(lint.main([os.path.join(APP, "App"), "--fail-on", "never"]), 0)
            self.assertEqual(lint.main(["/does/not/exist"]), 2)


class ConcurrencyTests(unittest.TestCase):
    def test_findings_and_settings(self):
        result = conc.analyze([APP])
        self.assertEqual(result["settings"]["language_mode"], "6 (data-race errors)")
        found = rules(result)
        for rid in ("SC001", "SC002", "SC003", "SC004", "SC005", "SC006", "SC007", "SC008"):
            self.assertIn(rid, found)
        messages = " ".join(f["message"] for f in result["findings"])
        self.assertNotIn("computed", messages)        # computed static var is not state
        self.assertNotIn("instances", messages)       # isolated by @MainActor type
        self.assertNotIn("SafeStore", messages)       # @MainActor ObservableObject

    def test_default_main_actor_isolation_skips_rules(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(os.path.join(APP, "App", "Services.swift"), d)
            with open(os.path.join(d, "Package.swift"), "w") as fh:
                fh.write('// swift-tools-version: 6.2\nlet p = Package(name: "X", targets: [.target(name: "X", '
                         'swiftSettings: [.defaultIsolation(MainActor.self)])])\n')
            result = conc.analyze([d])
            self.assertEqual(result["settings"]["default_isolation"], "MainActor")
            self.assertNotIn("SC008", rules(result))


class BuildErrorTests(unittest.TestCase):
    def setUp(self):
        self.result = builderr.analyze(os.path.join(FIXTURES, "logs", "build_failed.log"))

    def test_status_and_classification(self):
        r = self.result
        self.assertEqual(r["status"], "ARCHIVE FAILED")
        ids = [e.get("id") for e in r["errors"]]
        for rid in ("SWIFT001", "SWIFT002", "MOD001", "PROV001", "PROV002", "SCRIPT001", "LINK001", "PLIST002"):
            self.assertIn(rid, ids)
        self.assertEqual(len(r["failed_commands"]), 2)
        self.assertEqual(r["warning_count"], 1)

    def test_duplicates_are_counted_once(self):
        dup = [e for e in self.result["errors"] if e.get("id") == "SWIFT001"]
        self.assertEqual(len(dup), 1)
        self.assertEqual(dup[0]["count"], 2)

    def test_paths_with_spaces(self):
        files = [e.get("file") for e in self.result["errors"]]
        self.assertIn("/Users/dev/My App/Sources/Feed.swift", files)

    def test_summary_lines_sort_last(self):
        self.assertEqual(self.result["errors"][-1]["id"], "LINK003")

    def test_gzip_activity_log(self):
        import gzip
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.xcactivitylog")
            with gzip.open(p, "wb") as fh:
                fh.write(b'SLF0\r12"/Users/dev/A.swift:3:1: error: cannot find \'foo\' in scope\r** BUILD FAILED **')
            r = builderr.analyze(p)
            self.assertEqual(r["error_count"], 1)
            self.assertEqual(r["errors"][0]["file"], "/Users/dev/A.swift")

    def test_real_swiftc_swift6_output(self):
        """Captured from `swiftc -typecheck -swift-version 6` (Xcode 27.2): relative paths, macro expansions."""
        r = builderr.analyze(os.path.join(FIXTURES, "logs", "swiftc_swift6.log"))
        ids = sorted(e.get("id") for e in r["errors"])
        self.assertEqual(ids, ["SWIFT001", "SWIFT001", "SWIFT002", "SWIFT003", "SWIFT003"])
        self.assertIn("Bugs.swift", [e.get("file") for e in r["errors"]])
        self.assertIn("macro expansion @ObservationTracked:3:14", [e.get("location") for e in r["errors"]])

    def test_clean_log(self):
        r = builderr.analyze(text="CompileSwift normal arm64\n** BUILD SUCCEEDED **\n")
        self.assertEqual((r["status"], r["error_count"]), ("BUILD SUCCEEDED", 0))


class PlistTests(unittest.TestCase):
    def test_fixture(self):
        r = plist.analyze(os.path.join(APP, "App", "Info.plist"), [os.path.join(APP, "App")],
                          os.path.join(APP, "BuggyApp.xcodeproj"))
        found = rules(r)
        for rid in ("PL003", "PL004", "PL005", "PL006", "PL007", "PL009", "PL010"):
            self.assertIn(rid, found)
        missing = [f["message"] for f in r["findings"] if f["rule"] == "PL004"]
        self.assertTrue(any("NSCameraUsageDescription" in m for m in missing))
        self.assertTrue(any("NSLocationWhenInUseUsageDescription" in m for m in missing))

    def test_generated_plist_uses_build_settings(self):
        with tempfile.TemporaryDirectory() as d:
            proj = os.path.join(d, "X.xcodeproj")
            os.makedirs(proj)
            with open(os.path.join(proj, "project.pbxproj"), "w") as fh:
                fh.write("buildSettings = {\n GENERATE_INFOPLIST_FILE = YES;\n PRODUCT_BUNDLE_IDENTIFIER = com.x;\n"
                         " MARKETING_VERSION = 1.0;\n CURRENT_PROJECT_VERSION = 3;\n"
                         ' INFOPLIST_KEY_NSCameraUsageDescription = "Scan receipts";\n'
                         " INFOPLIST_KEY_UILaunchScreen_Generation = YES;\n};\n")
            src = os.path.join(d, "Cam.swift")
            with open(src, "w") as fh:
                fh.write("let s = AVCaptureSession()\n")
            self.assertEqual(rules(plist.analyze(None, [d], proj)), [])

    def test_built_bundle_flags_unexpanded_variables(self):
        with tempfile.TemporaryDirectory() as d:
            app = os.path.join(d, "X.app")
            os.makedirs(app)
            import plistlib
            with open(os.path.join(app, "Info.plist"), "wb") as fh:
                plistlib.dump({"CFBundleIdentifier": "com.x", "CFBundleExecutable": "X", "CFBundleVersion": "$(NOPE)",
                               "CFBundleShortVersionString": "1.0", "UILaunchScreen": {}}, fh)
            r = plist.analyze(app)
            self.assertEqual([(f["rule"], f["severity"]) for f in r["findings"]], [("PL003", "error")])


class SigningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_decode_profile(self):
        info = signing.decode_profile(write_profile(os.path.join(self.tmp, "a.mobileprovision")))
        self.assertEqual(info["type"], "development")
        self.assertEqual(info["bundle_id_pattern"], "com.example.*")
        self.assertEqual(info["certificates"][0]["subject_cn"], "Apple Development: Jane Dev (XYZ9876543)")
        self.assertEqual(info["certificates"][0]["subject_ou"], "ABCDE12345")
        self.assertEqual(len(info["certificates"][0]["sha1"]), 40)

    def test_profile_types(self):
        store = signing.decode_profile(write_profile(os.path.join(self.tmp, "s.mobileprovision"), devices=False,
                                                     get_task_allow=False))
        adhoc = signing.decode_profile(write_profile(os.path.join(self.tmp, "h.mobileprovision"), get_task_allow=False))
        self.assertEqual((store["type"], adhoc["type"]), ("app-store", "ad-hoc"))

    def test_expired_profile(self):
        p = write_profile(os.path.join(self.tmp, "old.mobileprovision"), expires=dt.datetime(2020, 1, 1))
        self.assertIn("SIG001", rules(signing.inspect_profile(p)))

    def test_compare_entitlements(self):
        p = write_profile(os.path.join(self.tmp, "a.mobileprovision"))
        r = signing.compare(os.path.join(APP, "App", "App.entitlements"), profile=p, bundle_id="com.example.buggy")
        msgs = {f["rule"]: f["message"] for f in r["findings"]}
        self.assertEqual(sorted(rules(r)), ["SIG006", "SIG006", "SIG007"])
        self.assertIn("aps-environment", msgs["SIG007"])

    def test_bundle_mismatch_and_get_task_allow(self):
        p = write_profile(os.path.join(self.tmp, "s.mobileprovision"), devices=False, get_task_allow=False,
                          app_id="ABCDE12345.com.other.app")
        ent = os.path.join(self.tmp, "x.entitlements")
        import plistlib
        with open(ent, "wb") as fh:
            plistlib.dump({"get-task-allow": True}, fh)
        self.assertEqual(sorted(rules(signing.compare(ent, profile=p, bundle_id="com.example.buggy"))),
                         ["SIG004", "SIG005"])

    def test_list_profiles_filters_by_bundle_id(self):
        write_profile(os.path.join(self.tmp, "a.mobileprovision"))
        self.assertEqual(signing.list_profiles("com.example.buggy", [self.tmp])["count"], 1)
        self.assertEqual(signing.list_profiles("org.other.app", [self.tmp])["count"], 0)


class ResourceTests(unittest.TestCase):
    def test_sources_only(self):
        r = resources.analyze([APP], plist=os.path.join(APP, "App", "Info.plist"))
        self.assertEqual(sorted(set(rules(r))), ["BR001", "BR004", "BR005", "BR006", "BR007"])

    def test_against_built_app(self):
        with tempfile.TemporaryDirectory() as d:
            app = os.path.join(d, "BuggyApp.app")
            os.makedirs(os.path.join(app, "Resources"))
            with open(os.path.join(app, "Resources", "config.json"), "w") as fh:
                fh.write("{}")
            r = resources.analyze([os.path.join(APP, "App")], app=app)
            self.assertIn("BR003", rules(r))  # bundled under Resources/, looked up at the root


class FirebaseTests(unittest.TestCase):
    def test_config(self):
        r = firebase.analyze(APP)
        found = rules(r)
        for rid in ("FB002", "FB004", "FB007", "FB008", "FB011"):
            self.assertIn(rid, found)
        self.assertEqual(r["app_bundle_ids"], ["com.example.buggy"])
        self.assertTrue(all("[REDACTED" in p["api_key"] for p in r["plists"]))
        self.assertNotIn("FAKE-TEST-KEY-0000000000000000000", json.dumps(r))

    def test_wrong_platform_plist(self):
        import plistlib
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "GoogleService-Info.plist")
            with open(p, "wb") as fh:
                plistlib.dump({"API_KEY": "k" * 20, "GCM_SENDER_ID": "1", "BUNDLE_ID": "com.x", "PROJECT_ID": "p",
                               "GOOGLE_APP_ID": "1:1:android:abc"}, fh)
            self.assertIn("FB005", rules(firebase.analyze(plist=p, bundle_id="com.x")))

    def test_rules(self):
        r = fbrules.analyze(root=APP, sources=[os.path.join(APP, "App")], today="2026-11-21")
        found = rules(r)
        for rid in ("FR001", "FR002", "FR005", "FR006"):
            self.assertIn(rid, found)
        self.assertIn("posts", r["collections_in_code"])

    def test_rules_future_expiry_and_missing_collection(self):
        with tempfile.TemporaryDirectory() as d:
            rf = os.path.join(d, "firestore.rules")
            with open(rf, "w") as fh:
                fh.write("rules_version = '2';\nservice cloud.firestore {\n match /databases/{database}/documents {\n"
                         "  match /users/{uid} { allow read: if request.time < timestamp.date(2030, 1, 1); }\n }\n}\n")
            src = os.path.join(d, "A.swift")
            with open(src, "w") as fh:
                fh.write('db.collection("orders").getDocuments()\ndb.collectionGroup("comments").getDocuments()\n')
            found = rules(fbrules.analyze([rf], sources=[d], today="2026-01-01"))
            self.assertEqual(sorted(found), ["FR003", "FR007", "FR008"])

    def test_templates_are_secure(self):
        assets = os.path.join(ROOT, "skills", "ios-map-firebase-diagnostics", "assets")
        r = fbrules.analyze([os.path.join(assets, "firestore.rules.template"),
                             os.path.join(assets, "storage.rules.template")], today="2026-01-01")
        self.assertEqual(r["summary"]["error"], 0)


class MapKitTests(unittest.TestCase):
    def test_fixture(self):
        r = mapkit.analyze([os.path.join(APP, "App")], plist=os.path.join(APP, "App", "Info.plist"))
        found = rules(r)
        for rid in ("MK001", "MK002", "MK004", "MK005", "MK006", "MK007", "MK008", "MK009", "MK010", "MK011"):
            self.assertIn(rid, found)
        swapped = [f for f in r["findings"] if f["rule"] == "MK005" and "swapped" in f["message"]]
        self.assertEqual(len(swapped), 1)

    def test_reference_asset_is_clean(self):
        assets = os.path.join(ROOT, "skills", "ios-map-firebase-diagnostics", "assets")
        self.assertEqual(mapkit.analyze([assets])["findings"], [])


class CrashTests(unittest.TestCase):
    def test_swift_trap_summary(self):
        r = crash.summarize(os.path.join(FIXTURES, "crashes", "BuggyApp-2026-11-20-101500.ips"))
        self.assertEqual(r["exception"]["type"], "EXC_BREAKPOINT")
        self.assertEqual(r["bundle_id"], "com.example.buggy")
        self.assertIn("force-unwrapped", r["diagnosis"][0]["finding"])
        self.assertIn("atos -arch arm64", r["symbolicate"]["atos"])
        self.assertIn("0x10000a000", r["symbolicate"]["atos"])

    def test_watchdog(self):
        r = crash.summarize(os.path.join(FIXTURES, "crashes", "Watchdog-2026-11-19.ips"))
        self.assertIn("Watchdog", r["diagnosis"][0]["finding"])

    def test_list_and_filter(self):
        r = crash.list_reports([os.path.join(FIXTURES, "crashes")], app="Buggy")
        self.assertEqual(r["total"], 2)
        self.assertEqual(crash.list_reports([os.path.join(FIXTURES, "crashes")], app="Other")["total"], 0)

    def test_legacy_text_report(self):
        with tempfile.NamedTemporaryFile("w", suffix=".crash", delete=False) as fh:
            fh.write("Process: Buggy [1]\nException Type:  EXC_CRASH (SIGABRT)\n\nApplication Specific Information:\n"
                     "*** Terminating app due to uncaught exception 'NSInvalidArgumentException'\n\n"
                     "Thread 0 Crashed:\n0 CoreFoundation 0x1 __exceptionPreprocess\n\n")
        self.addCleanup(os.unlink, fh.name)
        r = crash.summarize(fh.name)
        self.assertEqual(r["fields"]["Exception Type"], "EXC_CRASH (SIGABRT)")
        self.assertIn("NSException", r["diagnosis"][0]["finding"])


class SimLogTests(unittest.TestCase):
    def test_scan(self):
        with open(os.path.join(FIXTURES, "logs", "runtime.log")) as fh:
            r = simlogs.scan_text(fh.read())
        ids = {m["id"]: m["count"] for m in r["matches"]}
        self.assertEqual(ids, {"RW001": 2, "RW005": 1, "RW007": 1, "RW020": 1})

    def test_predicate_is_built_safely(self):
        pred = simlogs.build_predicate(process="My App", subsystem="com.acme.app", errors_only=True)
        self.assertEqual(pred, 'process == "My App" AND subsystem == "com.acme.app" AND '
                               "(messageType == error OR messageType == fault)")
        for bad in ('x" OR 1==1', "a;rm -rf /", "$(whoami)"):
            with self.assertRaises(simlogs.c.UsageError):
                simlogs.build_predicate(process=bad)

    def test_logs_validates_inputs_before_running(self):
        for kwargs in ({"device": "not-a-udid"}, {"last": "2d"}, {"last": "90m"}, {"level": "trace"}):
            with self.assertRaises(simlogs.c.UsageError):
                simlogs.logs(**kwargs)


class CommonTests(unittest.TestCase):
    def setUp(self):
        self.c = lint.c

    def test_redaction(self):
        google = "AIza" + "B" * 35
        token = "gh" + "p_" + "A" * 36
        pem = "-----BEGIN " + "PRIVATE KEY-----\nabc\n-----END " + "PRIVATE KEY-----"   # built at runtime
        text = f"key={google} token={token} {pem}"
        out = self.c.redact_text(text)
        self.assertNotIn(google, out)
        self.assertNotIn(token, out)
        self.assertNotIn("abc", out)
        self.assertEqual(self.c.redact_obj({"API_KEY": "abcdefghijkl", "name": "x"})["name"], "x")
        self.assertIn("REDACTED", self.c.redact_obj({"API_KEY": "abcdefghijkl"})["API_KEY"])

    def test_command_allowlist(self):
        allowed = self.c.command_allowed
        self.assertTrue(allowed(["xcrun", "simctl", "list", "devices", "--json"]))
        self.assertTrue(allowed(["xcrun", "simctl", "spawn", "booted", "log", "show"]))
        for argv in (["xcrun", "simctl", "erase", "all"], ["xcodebuild", "build"], ["rm", "-rf", "/"],
                     ["security", "delete-keychain"], ["xcrun", "simctl", "spawn", "booted", "rm"]):
            self.assertFalse(allowed(argv), argv)
        with self.assertRaises(self.c.UsageError):
            self.c.run_command(["xcodebuild", "archive"])

    def test_strip_swift_preserves_offsets(self):
        src = 'let a = "x // not a comment \\(f("y"))" // real comment\n/* a /* nested */ b */ let b = 1\n'
        code = self.c.strip_swift(src)
        self.assertEqual(len(code), len(src))
        self.assertNotIn("real comment", code)
        self.assertNotIn("nested", code)
        self.assertIn("let b = 1", code)
        self.assertEqual(code.count("\n"), src.count("\n"))

    def test_common_copies_in_sync(self):
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "sync_common.py"), "--check"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout)


class SkillFormatTests(unittest.TestCase):
    def test_validate_skills(self):
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "validate_skills.py")],
                              capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout)


if __name__ == "__main__":
    unittest.main()
