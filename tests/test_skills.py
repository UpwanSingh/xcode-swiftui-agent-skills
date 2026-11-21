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
xenv = load_script("xcode-build-signing", "xcode_env")


# One realistic message per classifier. Each must classify to its own id (first match wins), so
# a typo, a shadowing pattern or an over-broad regex fails here instead of shipping.
BUILD_SAMPLES = {
    "SIGN001": 'error: Signing for "App" requires a development team. Select a development team in the Signing & Capabilities editor.',
    "SIGN002": 'error: No signing certificate "iOS Distribution" found: No "iOS Distribution" signing certificate matching team ID "ABCDE12345" with a private key was found.',
    "SIGN003": "error: /path/App.app: errSecInternalComponent",
    "SIGN004": "error: The identity used to sign the executable is no longer valid.",
    "SIGN005": 'error: App is automatically signed, but provisioning profile Foo has been manually specified.',
    "SIGN006": "error: Command CodeSign failed with a nonzero exit code",
    "PROV001": "error: No profiles for 'com.acme.app' were found: Xcode couldn't find any iOS App Development provisioning profiles",
    "PROV002": 'error: Provisioning profile "App Dev" doesn\'t include signing certificate "Apple Development: Jane (X)".',
    "PROV003": 'error: Provisioning profile "App Dev" doesn\'t support the Push Notifications capability.',
    "PROV004": "error: Provisioning profile doesn't match the entitlements file's value for the aps-environment entitlement.",
    "PROV005": 'error: Provisioning profile "App Dev" has expired.',
    "PROV006": "error: Provisioning profile doesn't include the currently selected device \"Jane's iPhone\".",
    "PROV007": "error: Embedded binary's bundle identifier is not prefixed with the parent app's bundle identifier.",
    "PLIST001": "error: Cannot code sign because the target does not have an Info.plist file",
    "PLIST002": "error: Multiple commands produce '/DD/Build/Products/Debug-iphoneos/App.app/Info.plist'",
    "PLIST003": "error: ITMS-90683: Missing purpose string in Info.plist",
    "PLIST004": "error: ITMS-90060: This bundle is invalid. The value for key CFBundleShortVersionString must be a period-separated list",
    "RES001": "error: Multiple commands produce '/DD/Build/Products/Debug-iphoneos/App.app/config.json'",
    "RES002": "error: Build input file cannot be found: '/src/App/Old.swift'",
    "RES003": 'error: None of the input catalogs contained a matching stickers icon set or app icon set named "AppIcon".',
    "SCRIPT001": "Sandbox: rsync(123) deny(1) file-write-create /DD/App.app/Frameworks/X.framework",
    "SCRIPT002": "Command PhaseScriptExecution failed with a nonzero exit code",
    "POD001": "error: The sandbox is not in sync with the Podfile.lock. Run 'pod install' or update your CocoaPods installation.",
    "SPM001": "xcodebuild: error: Could not resolve package dependencies:",
    "SPM002": "error: Missing package product 'FirebaseAuth'",
    "MOD001": "/src/App/A.swift:1:8: error: no such module 'FirebaseFirestore'",
    "MOD002": "/src/A.swift:1:8: error: compiling for iOS 15.0, but module 'Kit' has a minimum deployment target of iOS 17.0",
    "MOD003": "error: Could not build Objective-C module 'Kit'",
    "LINK001": "Undefined symbols for architecture arm64:",
    "LINK002": "duplicate symbol '_OBJC_CLASS_$_Foo' in:",
    "LINK003": "clang: error: linker command failed with exit code 1 (use -v to see invocation)",
    "DEST001": "xcodebuild: error: Unable to find a destination matching the provided destination specifier:",
    "SCHEME001": 'xcodebuild: error: The project named "App" does not contain a scheme named "Ap".',
    "DEP001": "error: Cycle in dependencies between targets 'App' and 'Widget'; building could produce unreliable results.",
    "SWIFT001": "/src/A.swift:3:5: error: main actor-isolated property 'count' can not be mutated from a nonisolated context",
    "SWIFT002": "/src/A.swift:9:6: error: generic struct 'StateObject' requires that 'Cart' conform to 'ObservableObject'",
    "SWIFT003": "macro expansion @ObservationTracked:3:14: error: invalid redeclaration of synthesized property '_total'",
    "EXPORT001": "error: exportArchive: No signing certificate \"iOS Distribution\" found",
}
RUNTIME_SAMPLES = {
    "RW001": "[SwiftUI] Publishing changes from background threads is not allowed; make sure to publish values from the main thread",
    "RW002": "Publishing changes from within view updates is not allowed, this will cause undefined behavior.",
    "RW003": "Modifying state during view update, this will cause undefined behavior.",
    "RW004": "=== AttributeGraph: cycle detected through attribute 1234 ===",
    "RW005": "onChange(of: String) action tried to update multiple times per frame.",
    "RW006": "Bound preference SizeKey tried to update multiple times per frame.",
    "RW007": 'A NavigationLink is presenting a value of type "Post" but there is no matching navigationDestination declaration',
    "RW008": 'Do not put a navigation destination modifier inside a "lazy" container, like `List` or `LazyVStack`.',
    "RW009": "Only root-level navigation destinations are effective for a navigation stack with a homogeneous path.",
    "RW010": "[LayoutConstraints] Unable to simultaneously satisfy constraints.",
    "RW011": "Invalid frame dimension (negative or non-finite).",
    "RW012": "Incorrect actor executor assumption; expected 'MainActor' executor.",
    "RW013": "Main Thread Checker: UI API called on a background thread: -[UIView setNeedsLayout]",
    "RW014": "This app has crashed because it attempted to access privacy-sensitive data without a usage description.",
    "RW015": "App Transport Security has blocked a cleartext HTTP (http://) resource load since it is insecure.",
    "RW016": "*** Terminating app due to uncaught exception 'NSInvalidArgumentException', reason: 'Invalid Region <center:+0.0, span:+0.0>'",
    "RW017": "Error Domain=kCLErrorDomain Code=1 \"(null)\"",
    "RW018": "This method can cause UI unresponsiveness if invoked on the main thread.",
    "RW019": "The default FirebaseApp instance must be configured before the default Firestore instance can be initialized.",
    "RW020": "[FirebaseFirestore][I-FST000001] Listen for query at posts failed: Missing or insufficient permissions.",
    "RW021": "The Firestore instance has already been started and its settings can no longer be changed.",
    "RW022": "[FirebaseAuth][I-AUT000002] Error: API key not valid. Please pass a valid API key.",
    "RW023": "APNS device token not set before retrieving FCM Token for Sender ID '123'.",
    "RW024": "[FirebaseStorage] Object users/a.jpg does not exist.",
    "RW025": "The query requires an index. You can create it here: https://console.firebase.google.com/",
}


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

    def test_project_isolation_does_not_apply_to_package_sources(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "App.xcodeproj"))
            os.makedirs(os.path.join(d, "App"))
            os.makedirs(os.path.join(d, "Pkg", "Sources", "Pkg"))
            with open(os.path.join(d, "App.xcodeproj", "project.pbxproj"), "w") as fh:
                fh.write("buildSettings = {\n SWIFT_DEFAULT_ACTOR_ISOLATION = MainActor;\n};\n")
            with open(os.path.join(d, "App", "A.swift"), "w") as fh:
                fh.write("enum A { static var x = 1 }\n")
            with open(os.path.join(d, "Pkg", "Package.swift"), "w") as fh:
                fh.write('// swift-tools-version: 6.0\nlet p = Package(name: "Pkg", targets: [.target(name: "Pkg")])\n')
            with open(os.path.join(d, "Pkg", "Sources", "Pkg", "B.swift"), "w") as fh:
                fh.write("enum B { static var y = 1 }\n")
            flagged = [os.path.basename(f["file"]) for f in conc.analyze([d])["findings"] if f["rule"] == "SC008"]
            self.assertEqual(flagged, ["B.swift"])

    def test_default_main_actor_isolation_skips_rules(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "Sources", "X"))
            shutil.copy(os.path.join(APP, "App", "Services.swift"), os.path.join(d, "Sources", "X"))
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

    def test_every_catalog_entry_has_a_matching_sample(self):
        self.assertEqual(sorted(BUILD_SAMPLES), sorted(e[0] for e in builderr.CATALOG))
        for rid, line in BUILD_SAMPLES.items():
            errors = builderr.analyze(text=line + "\n")["errors"]
            self.assertEqual([e.get("id") for e in errors], [rid], line)

    def test_readme_states_the_catalog_size(self):
        for doc in ("README.md", "CHANGELOG.md"):
            with open(os.path.join(ROOT, doc)) as fh:
                text = fh.read()
            self.assertTrue(f"{len(builderr.CATALOG)} " in text and "build" in text,
                            f"{doc} must state the catalog size ({len(builderr.CATALOG)})")
            self.assertNotRegex(text, r"\b(?!%d\b)\d+ (?:build patterns|classified error patterns)" % len(builderr.CATALOG),
                                f"{doc} states a stale catalog size")

    def test_large_logs_are_read_to_the_end(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "big.log")  # above the old 8 MB text limit
            with open(p, "w") as fh:
                fh.write("CompileSwift normal arm64 /src/File.swift\n" * 250000)
                fh.write("/src/A.swift:3:1: error: cannot find 'foo' in scope\n** BUILD FAILED **\n")
            r = builderr.analyze(p)
            self.assertEqual((r["status"], r["error_count"], r["input_truncated"]), ("BUILD FAILED", 1, False))

    def test_truncated_gzip_is_flagged_not_crashed(self):
        import gzip
        with tempfile.TemporaryDirectory() as d:
            full = os.path.join(d, "full.xcactivitylog")
            with gzip.open(full, "wb") as fh:
                fh.write(b"/src/A.swift:1:1: error: early problem\r" + b"noise line\r" * 200000 + b"** BUILD FAILED **\r")
            cut = os.path.join(d, "cut.xcactivitylog")
            with open(full, "rb") as src, open(cut, "wb") as dst:
                dst.write(src.read()[:2000])
            r = builderr.analyze(cut)
            self.assertTrue(r["input_truncated"])
            self.assertIn("ended early", r["input_note"])
            self.assertEqual(r["error_count"], 1)
            self.assertIn("WARNING", builderr.format_text(r))

    def test_long_lines_stay_fast(self):
        import time
        start = time.time()
        builderr.analyze(text="/x/y.swift:" + "1" * 60000 + "\n" + "a" * 500000 + "\n")
        self.assertLess(time.time() - start, 2.0)

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

    def test_xcconfig_settings_count_as_defined(self):
        with tempfile.TemporaryDirectory() as d:
            import plistlib
            os.makedirs(os.path.join(d, "X.xcodeproj"))
            with open(os.path.join(d, "X.xcodeproj", "project.pbxproj"), "w") as fh:
                fh.write("buildSettings = {\n PRODUCT_BUNDLE_IDENTIFIER = com.x;\n};\n")
            with open(os.path.join(d, "Base.xcconfig"), "w") as fh:
                fh.write("// shared\nMARKETING_VERSION = 2.1\nCURRENT_PROJECT_VERSION = 9\n")
            info = os.path.join(d, "Info.plist")
            with open(info, "wb") as fh:
                plistlib.dump({"CFBundleIdentifier": "$(PRODUCT_BUNDLE_IDENTIFIER)", "CFBundleExecutable": "X",
                               "CFBundleShortVersionString": "$(MARKETING_VERSION)",
                               "CFBundleVersion": "$(CURRENT_PROJECT_VERSION)", "UILaunchScreen": {}}, fh)
            self.assertEqual(rules(plist.analyze(info, project=os.path.join(d, "X.xcodeproj"))), [])

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


class XcodeEnvTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.output = (0, "{}", "")
        self._orig = xenv.c.run_command
        xenv.c.run_command = lambda argv, timeout=60: (self.calls.append(argv) or self.output)
        self.addCleanup(setattr, xenv.c, "run_command", self._orig)

    def test_schemes_never_resolves_packages(self):
        self.output = (0, 'noise {"project": {"schemes": ["App"]}}', "")
        self.assertEqual(xenv.schemes("/p/App.xcodeproj")["project"]["schemes"], ["App"])
        self.assertIn("-disableAutomaticPackageResolution", self.calls[0])
        self.assertTrue(self._orig.__globals__["command_allowed"](self.calls[0]))

    def test_unresolved_packages_get_an_actionable_error(self):
        self.output = (74, "", "xcodebuild: error: Could not resolve package dependencies: a resolved file is required")
        with self.assertRaises(xenv.c.UsageError) as ctx:
            xenv.schemes("/p/App.xcworkspace")
        self.assertIn("never resolves packages", str(ctx.exception))

    def test_garbage_json_is_a_usage_error(self):
        self.output = (0, "not json at all", "")
        with self.assertRaises(xenv.c.UsageError):
            xenv.settings("/p/App.xcodeproj")

    def test_settings_command_is_allowed(self):
        self.output = (0, '[{"target": "App", "buildSettings": {"PRODUCT_NAME": "App", "OTHER": "x"}}]', "")
        r = xenv.settings("/p/App.xcworkspace", scheme="App", configuration="Release")
        self.assertEqual(r["targets"][0]["settings"], {"PRODUCT_NAME": "App"})
        self.assertTrue(self._orig.__globals__["command_allowed"](self.calls[0]), self.calls[0])

    def test_logs_default_to_build_kind(self):
        import time
        with tempfile.TemporaryDirectory() as d:
            for kind, age in (("Build", 100), ("Package", 0)):
                folder = os.path.join(d, "App-abc", "Logs", kind)
                os.makedirs(folder)
                path = os.path.join(folder, f"{kind}.xcactivitylog")
                open(path, "w").close()
                os.utime(path, (time.time() - age, time.time() - age))
            self.assertEqual([x["kind"] for x in xenv.logs(derived_data=d)["logs"]], ["Build"])
            self.assertEqual([x["kind"] for x in xenv.logs(derived_data=d, kind="all")["logs"]], ["Package", "Build"])
            with self.assertRaises(xenv.c.UsageError):
                xenv.logs(project="../..", derived_data=d)


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

    def test_unexpanded_variable_matches_granted_value(self):
        import plistlib
        p = write_profile(os.path.join(self.tmp, "a.mobileprovision"), entitlements={
            "com.apple.developer.icloud-container-identifiers": ["iCloud.com.example.buggy"]})
        ent = os.path.join(self.tmp, "x.entitlements")
        with open(ent, "wb") as fh:
            plistlib.dump({"com.apple.developer.icloud-container-identifiers": ["iCloud.$(CFBundleIdentifier)"]}, fh)
        self.assertEqual(rules(signing.compare(ent, profile=p)), [])  # no bundle id passed
        with open(ent, "wb") as fh:
            plistlib.dump({"com.apple.developer.icloud-container-identifiers": ["iCloud.org.other.$(X)"]}, fh)
        self.assertEqual(rules(signing.compare(ent, profile=p)), ["SIG007"])

    def test_rules_table_covers_emitted_ids(self):
        import re as _re
        with open(signing.__file__) as fh:
            emitted = set(_re.findall(r'c\.Finding\("(SIG\d{3})"', fh.read()))
        self.assertEqual(emitted, set(signing.RULES))

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

    def test_compiled_and_localized_resources(self):
        with tempfile.TemporaryDirectory() as d:
            src, app = os.path.join(d, "src"), os.path.join(d, "App.app")
            os.makedirs(os.path.join(src, "Model.xcdatamodeld", "Model.xcdatamodel"))
            os.makedirs(os.path.join(app, "en.lproj"))
            os.makedirs(os.path.join(app, "Model.momd"))
            for f in (os.path.join(src, "Main.storyboard"), os.path.join(src, "Help.html"),
                      os.path.join(app, "en.lproj", "Help.html")):
                open(f, "w").close()
            with open(os.path.join(src, "A.swift"), "w") as fh:
                fh.write('let m = Bundle.main.url(forResource: "Model", withExtension: "momd")\n'
                         'let h = Bundle.main.url(forResource: "Help", withExtension: "html")\n'
                         'let s = Bundle.main.path(forResource: "Main", ofType: "storyboardc")\n')
            r = resources.analyze([src], app=app)
            self.assertEqual([(f["rule"], f["line"]) for f in r["findings"]], [("BR002", 3)])

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

    def test_lazy_property_manager_is_not_a_local(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "M.swift")
            with open(p, "w") as fh:
                fh.write("import CoreLocation\nfinal class M: NSObject {\n"
                         "    private lazy var manager: CLLocationManager = {\n"
                         "        let m = CLLocationManager()\n        m.delegate = self\n        return m\n    }()\n"
                         "    func f() {\n        let local = CLLocationManager()\n        _ = local\n    }\n}\n")
            lines = [f["line"] for f in mapkit.analyze([p])["findings"] if f["rule"] == "MK001"]
            self.assertEqual(lines, [9])

    def test_project_without_generated_plist_reads_its_info_plist(self):
        import plistlib
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "X.xcodeproj"))
            os.makedirs(os.path.join(d, "App"))
            with open(os.path.join(d, "X.xcodeproj", "project.pbxproj"), "w") as fh:
                fh.write("buildSettings = {\n GENERATE_INFOPLIST_FILE = NO;\n INFOPLIST_FILE = App/Info.plist;\n};\n")
            with open(os.path.join(d, "App", "Info.plist"), "wb") as fh:
                plistlib.dump({"NSLocationWhenInUseUsageDescription": "Shows nearby stores",
                               "UIBackgroundModes": ["location"]}, fh)
            with open(os.path.join(d, "App", "L.swift"), "w") as fh:
                fh.write("final class L { let m = CLLocationManager()\n func go() { m.requestWhenInUseAuthorization()\n"
                         " m.allowsBackgroundLocationUpdates = true } }\n")
            found = rules(mapkit.analyze([os.path.join(d, "App")], project=os.path.join(d, "X.xcodeproj")))
            self.assertNotIn("MK002", found)
            self.assertNotIn("MK004", found)

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

    def test_near_null_with_16_digit_address(self):
        body = {"exception": {"type": "EXC_BAD_ACCESS", "signal": "SIGSEGV",
                              "subtype": "KERN_INVALID_ADDRESS at 0x0000000000000010"},
                "faultingThread": 0, "threads": [{"triggered": True, "frames": []}], "usedImages": []}
        with tempfile.NamedTemporaryFile("w", suffix=".ips", delete=False) as fh:
            fh.write(json.dumps({"app_name": "A", "bug_type": "309"}) + "\n" + json.dumps(body))
        self.addCleanup(os.unlink, fh.name)
        r = crash.summarize(fh.name)
        self.assertEqual(r["diagnosis"][0]["finding"], "Memory access to a null/near-null address.")

    def test_listing_survives_unreadable_reports(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(os.path.join(FIXTURES, "crashes", "Watchdog-2026-11-19.ips"), d)
            bad = os.path.join(d, "Broken.ips")
            with open(bad, "w") as fh:
                fh.write("{}")
            os.chmod(bad, 0)
            try:
                r = crash.list_reports([d])
            finally:
                os.chmod(bad, 0o600)
            self.assertEqual(r["total"], 1 if os.geteuid() != 0 else 2)
            if os.geteuid() != 0:
                self.assertEqual(len(r["errors"]), 1)

    def test_listing_reads_only_the_header(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "Huge.ips"), "w") as fh:  # over read_text's 8 MB limit
                fh.write(json.dumps({"app_name": "Huge", "bug_type": "288"}) + "\n" + "x" * (9 * 1024 * 1024))
            r = crash.list_reports([d])
            self.assertEqual([(x["app"], x["kind"]) for x in r["reports"]], [("Huge", "stackshot/hang")])

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
        # Each case must fail on its own validation message, before any command could run
        # (so the test cannot pass merely because xcrun is missing or log show fails).
        cases = [({"device": "not-a-udid"}, "--device must be"), ({"last": "2d"}, "--last must look like"),
                 ({"last": "90m"}, "capped at 1h"), ({"level": "trace"}, "--level must be"),
                 ({"process": 'x" OR 1==1'}, "--process may only contain")]
        for kwargs, message in cases:
            with self.assertRaises(simlogs.c.UsageError) as ctx:
                simlogs.logs(**kwargs)
            self.assertIn(message, str(ctx.exception), kwargs)

    def test_logs_builds_allow_listed_command(self):
        seen = []
        original = simlogs.c.run_command
        simlogs.c.run_command = lambda argv, timeout=60: (seen.append(argv) or (0, "a\nb\n", ""))
        try:
            r = simlogs.logs(process="MyApp", last="2m", level="debug", errors_only=True, max_lines=1)
        finally:
            simlogs.c.run_command = original
        self.assertTrue(simlogs.c.command_allowed(seen[0]), seen[0])
        self.assertEqual(r["lines"], ["b"])

    def test_every_runtime_warning_has_a_matching_sample(self):
        for rid, sample in RUNTIME_SAMPLES.items():
            got = [m["id"] for m in simlogs.scan_text(sample)["matches"]]
            self.assertEqual(got[:1], [rid], (rid, sample, got))
        self.assertEqual(sorted(RUNTIME_SAMPLES), sorted(r[0] for r in simlogs.RUNTIME_WARNINGS))


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
        for argv in (["xcrun", "simctl", "list", "devices", "--json"],
                     ["xcrun", "simctl", "spawn", "booted", "log", "show", "--style", "compact", "--last", "5m",
                      "--info", "--predicate", 'process == "A"'],
                     ["xcodebuild", "-list", "-json", "-disableAutomaticPackageResolution", "-project", "/A.xcodeproj"],
                     ["xcodebuild", "-showBuildSettings", "-json", "-disableAutomaticPackageResolution",
                      "-workspace", "/A.xcworkspace", "-scheme", "App", "-configuration", "Release"],
                     ["codesign", "-d", "--entitlements", "-", "--xml", "/A.app"],
                     ["security", "find-identity", "-p", "codesigning"],
                     ["xcrun", "devicectl", "list", "devices", "--json-output", "/tmp/x.json"]):
            self.assertTrue(allowed(argv), argv)
        for argv in (["xcrun", "simctl", "erase", "all"], ["xcodebuild", "build"], ["rm", "-rf", "/"],
                     ["security", "delete-keychain"], ["xcrun", "simctl", "spawn", "booted", "rm"],
                     # widened variants of allowed commands must not slip through as a prefix
                     ["codesign", "-d", "--extract-certificates", "/A.app"],
                     ["codesign", "-d", "--entitlements", "-", "--xml", "/A.app", "/B.app"],
                     ["xcodebuild", "-list", "-json", "-project", "/A.xcodeproj"],  # resolves packages
                     ["xcodebuild", "-list", "-json", "-disableAutomaticPackageResolution", "-project", "/A",
                      "-exportArchive"],
                     ["xcrun", "simctl", "spawn", "booted", "log", "show", "--predicate"],
                     ["xcrun", "simctl", "spawn", "-x", "log", "show"],
                     ["security", "find-identity", "-p", "codesigning", "-v", "/x.keychain"]):
            self.assertFalse(allowed(argv), argv)
        with self.assertRaises(self.c.UsageError):
            self.c.run_command(["xcodebuild", "archive"])

    def test_raw_strings_are_stripped(self):
        src = 'let r = #"a \\" // still string"# ; let x = 1 // c\nlet m = ##"""\nsecret"q\n"""## + "z"\n'
        code = self.c.strip_swift(src)
        self.assertEqual(len(code), len(src))
        self.assertIn("let x = 1", code)
        self.assertNotIn("still string", code)
        self.assertNotIn("secret", code)

    def test_redaction_covers_every_pattern(self):
        samples = ["AIza" + "C" * 35, "gh" + "o_" + "D" * 36, "github_" + "pat_" + "E" * 30, "xo" + "xb-" + "1" * 12,
                   "AK" + "IA" + "F" * 16, "s" + "k-ant-" + "G" * 24, "sk_" + "live_" + "H" * 24,
                   "sntr" + "ys_" + "I" * 30, "eyJ" + "a" * 10 + "." + "b" * 10 + "." + "c" * 10]
        for sample in samples:
            self.assertNotIn(sample, self.c.redact_text(f"value={sample} end"), sample)
        masked = self.c.redact_obj({"SENTRY_AUTH_TOKEN": "x" * 20, "MY_API_KEY": "y" * 20, "CLIENT_SECRET": "z" * 20,
                                    "PRODUCT_NAME": "App"})
        self.assertEqual(masked["PRODUCT_NAME"], "App")
        self.assertTrue(all("REDACTED" in masked[k] for k in ("SENTRY_AUTH_TOKEN", "MY_API_KEY", "CLIENT_SECRET")))

    def test_path_guard_skips_walked_files(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "root"))
            with open(os.path.join(d, "outside.swift"), "w") as fh:
                fh.write("var leaked = 1\n")
            with open(os.path.join(d, "root", "ok.swift"), "w") as fh:
                fh.write("let ok = 1\n")
            os.symlink(os.path.join(d, "outside.swift"), os.path.join(d, "root", "link.swift"))
            root = os.path.realpath(os.path.join(d, "root"))
            self.c.PATH_GUARD = lambda real: real.startswith(root + os.sep)
            try:
                found = [os.path.basename(p) for p in self.c.iter_files([os.path.join(d, "root")], (".swift",))]
                with self.assertRaises(self.c.UsageError):
                    self.c.read_text(os.path.join(d, "root", "link.swift"))
            finally:
                self.c.PATH_GUARD = None
            self.assertEqual(found, ["ok.swift"])

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
