"""Tool registry. Every tool is a thin, path-checked wrapper around a skill script.

The skill scripts under ../skills/*/scripts are the single source of truth for diagnostics;
this module only validates arguments, enforces the path policy and shapes results.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .safety import MAX_TEXT_INPUT, PathPolicy, PolicyError

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.environ.get("IOS_DEBUG_SKILLS_DIR") or os.path.join(os.path.dirname(os.path.dirname(HERE)), "skills")

_modules: Dict[str, Any] = {}


class SkillsNotFound(RuntimeError):
    """The skills/ directory (or a script in it) is missing; reported to the client as a tool error."""


def _exec(name: str, path: str):
    if not os.path.isfile(path):
        raise SkillsNotFound(f"skill script not found: {path} (set IOS_DEBUG_SKILLS_DIR to the repo's skills/ folder)")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)  # never leave a half-initialised module behind
        raise
    return module


def common():
    """The helper every skill script imports as `_common` (all copies are byte-identical, enforced
    by tools/sync_common.py). Loaded explicitly and first, so redaction and the path guard never
    depend on which script happened to be imported before."""
    if "_common" not in sys.modules:
        _exec("_common", os.path.join(SKILLS_DIR, "xcode-build-signing", "scripts", "_common.py"))
    return sys.modules["_common"]


def load(skill: str, script: str):
    """Import skills/<skill>/scripts/<script>.py once."""
    key = f"{skill}/{script}"
    if key not in _modules:
        common()
        _modules[key] = _exec(f"skill_{skill.replace('-', '_')}_{script}",
                              os.path.join(SKILLS_DIR, skill, "scripts", f"{script}.py"))
    return _modules[key]


@dataclass
class Tool:
    name: str
    title: str
    description: str
    properties: Dict[str, Dict[str, Any]]
    handler: Callable[[Dict[str, Any], PathPolicy], Any]
    required: Optional[List[str]] = None

    def schema(self) -> Dict[str, Any]:
        s: Dict[str, Any] = {"type": "object", "properties": self.properties, "additionalProperties": False}
        if self.required:
            s["required"] = self.required
        return s

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.schema(),
            "annotations": {"title": self.title, "readOnlyHint": True, "destructiveHint": False,
                            "idempotentHint": True, "openWorldHint": False},
        }


class ArgumentError(Exception):
    pass


_TYPES = {"string": str, "integer": int, "boolean": bool, "array": list, "object": dict}


def validate(tool: Tool, args: Any) -> Dict[str, Any]:
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ArgumentError("arguments must be an object")
    unknown = sorted(set(args) - set(tool.properties))
    if unknown:
        raise ArgumentError(f"unknown argument(s): {', '.join(unknown)}; expected: {', '.join(tool.properties) or 'none'}")
    for name in tool.required or []:
        if args.get(name) in (None, "", []):
            raise ArgumentError(f"missing required argument: {name}")
    for name, value in args.items():
        spec = tool.properties[name]
        expected = _TYPES[spec["type"]]
        if value is None:
            continue
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ArgumentError(f"{name} must be of type {spec['type']}")
        if spec["type"] == "array" and not all(isinstance(v, str) for v in value):
            raise ArgumentError(f"{name} must be an array of strings")
        if "enum" in spec and value not in spec["enum"]:
            raise ArgumentError(f"{name} must be one of {spec['enum']}")
        if spec["type"] == "integer":
            if "minimum" in spec and value < spec["minimum"] or "maximum" in spec and value > spec["maximum"]:
                raise ArgumentError(f"{name} must be between {spec.get('minimum')} and {spec.get('maximum')}")
        if spec["type"] == "string" and len(value) > MAX_TEXT_INPUT:
            raise ArgumentError(f"{name} is larger than {MAX_TEXT_INPUT} bytes")
    return args


# --------------------------------------------------------------------------- schema helpers

def S(desc: str, **extra) -> Dict[str, Any]:
    return dict({"type": "string", "description": desc}, **extra)


def I(desc: str, lo: int, hi: int, default: int) -> Dict[str, Any]:
    return {"type": "integer", "description": desc, "minimum": lo, "maximum": hi, "default": default}


def B(desc: str) -> Dict[str, Any]:
    return {"type": "boolean", "description": desc}


def A(desc: str) -> Dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "description": desc}


SOURCE_EXTS = (".swift",)
PLIST_EXTS = (".plist", ".entitlements", ".mobileprovision", ".provisionprofile", ".xcprivacy")
PROFILE_EXTS = (".mobileprovision", ".provisionprofile")
PROJECT_EXTS = (".xcodeproj", ".pbxproj")
LOG_EXTS = (".log", ".txt", ".xcactivitylog")


def _kw(a: Dict[str, Any], *names: str) -> Dict[str, Any]:
    """Only the arguments the caller actually passed, so each script's own defaults apply."""
    return {n: a[n] for n in names if a.get(n) is not None}


def _opt(policy: PathPolicy, value: Optional[str], **kw) -> Optional[str]:
    return policy.resolve(value, **kw) if value else None


def _system(policy: PathPolicy) -> None:
    if not policy.include_system:
        raise PolicyError("reading Xcode's system folders is disabled (IOS_DEBUG_MCP_SYSTEM_DIRS=0)")


def _paths(policy: PathPolicy, args: Dict[str, Any], key: str = "paths") -> List[str]:
    return policy.resolve_many(args.get(key) or [policy.default_root()], kind="any")


# --------------------------------------------------------------------------- handlers

def h_environment(a, p):
    return load("xcode-build-signing", "xcode_env").env()


def h_find_projects(a, p):
    root = p.resolve(a.get("root") or p.default_root(), kind="dir")
    return load("xcode-build-signing", "xcode_env").projects(root, **_kw(a, "max_depth"))


def h_list_schemes(a, p):
    return load("xcode-build-signing", "xcode_env").schemes(p.resolve(a["path"], kind="dir",
                                                                      exts=(".xcodeproj", ".xcworkspace")))


def h_build_settings(a, p):
    path = p.resolve(a["path"], kind="dir", exts=(".xcodeproj", ".xcworkspace"))
    result = load("xcode-build-signing", "xcode_env").settings(
        path, scheme=a.get("scheme"), configuration=a.get("configuration"), target=a.get("target"),
        keys=a.get("keys"), all_keys=bool(a.get("all")))
    # Build settings can carry xcconfig secrets (SENTRY_AUTH_TOKEN, *_API_KEY): mask by key name too.
    return common().redact_obj(result)


def h_build_logs(a, p):
    _system(p)
    return load("xcode-build-signing", "xcode_env").logs(**_kw(a, "project", "limit", "kind"))


def h_build_errors(a, p):
    mod = load("xcode-build-signing", "extract_build_errors")
    if bool(a.get("path")) == bool(a.get("text")):
        raise ArgumentError("pass exactly one of path or text")
    path = _opt(p, a.get("path"), kind="file", exts=LOG_EXTS)
    return mod.analyze(path=path, text=a.get("text"), **_kw(a, "include_warnings", "max_items"))


def h_read_plist(a, p):
    c = common()
    path = p.resolve(a["path"], kind="file", exts=PLIST_EXTS)
    if path.endswith(PROFILE_EXTS):
        data = load("xcode-build-signing", "inspect_signing").decode_profile(path)
    else:
        data = c.to_jsonable(c.load_plist(path))
    if a.get("keys") and isinstance(data, dict):
        data = {k: data[k] for k in a["keys"] if k in data}
    return {"path": path, "data": c.redact_obj(data)}


def h_check_info_plist(a, p):
    if not a.get("plist") and not a.get("project"):
        raise ArgumentError("pass plist and/or project")
    plist = _opt(p, a.get("plist"), kind="any", exts=(".plist", ".app"))
    project = _opt(p, a.get("project"), kind="any", exts=PROJECT_EXTS)
    sources = p.resolve_many(a["sources"], kind="any") if a.get("sources") else None
    return load("xcode-build-signing", "check_plist").analyze(plist, sources, project, built=bool(a.get("built")))


def h_bundle_resources(a, p):
    return load("xcode-build-signing", "check_bundle_resources").analyze(
        _paths(p, a, "sources"), _opt(p, a.get("app"), kind="dir", exts=(".app",)),
        _opt(p, a.get("plist"), kind="file", exts=(".plist",)))


def h_inspect_profile(a, p):
    return load("xcode-build-signing", "inspect_signing").inspect_profile(
        p.resolve(a["path"], kind="file", exts=PROFILE_EXTS), check_keychain=bool(a.get("check_keychain")))


def h_list_profiles(a, p):
    _system(p)
    return load("xcode-build-signing", "inspect_signing").list_profiles(a.get("bundle_id"))


def h_identities(a, p):
    return {"identities": load("xcode-build-signing", "inspect_signing").list_identities()}


def h_compare_entitlements(a, p):
    if bool(a.get("profile")) == bool(a.get("app")):
        raise ArgumentError("pass exactly one of profile or app")
    ent = a["entitlements"]
    if ent != "-signed-":
        ent = p.resolve(ent, kind="file", exts=(".entitlements", ".plist"))
    return load("xcode-build-signing", "inspect_signing").compare(
        ent, _opt(p, a.get("profile"), kind="file", exts=PROFILE_EXTS),
        _opt(p, a.get("app"), kind="dir", exts=(".app",)), a.get("bundle_id"))


def h_swiftui_lint(a, p):
    return load("swiftui-state-layout", "swiftui_lint").analyze(_paths(p, a))


def h_concurrency(a, p):
    settings = _opt(p, a.get("settings"), kind="any", exts=PROJECT_EXTS + ("Package.swift",))
    return load("swiftui-state-layout", "concurrency_check").analyze(_paths(p, a), settings)


def h_firebase_config(a, p):
    root = p.resolve(a.get("root") or p.default_root(), kind="dir")
    return load("ios-map-firebase-diagnostics", "check_firebase_config").analyze(
        root, _opt(p, a.get("plist"), kind="file", exts=(".plist",)), a.get("bundle_id"),
        _opt(p, a.get("info_plist"), kind="file", exts=(".plist",)),
        p.resolve_many(a["sources"], kind="any") if a.get("sources") else None)


def h_firebase_rules(a, p):
    files = p.resolve_many(a["paths"], kind="file", exts=(".rules",)) if a.get("paths") else None
    root = None if files else p.resolve(a.get("root") or p.default_root(), kind="dir")
    sources = p.resolve_many(a["sources"], kind="any") if a.get("sources") else None
    return load("ios-map-firebase-diagnostics", "lint_firebase_rules").analyze(files, root, sources, a.get("today"))


def h_mapkit(a, p):
    return load("ios-map-firebase-diagnostics", "mapkit_check").analyze(
        _paths(p, a), _opt(p, a.get("plist"), kind="file", exts=(".plist",)),
        _opt(p, a.get("project"), kind="any", exts=PROJECT_EXTS))


def h_simulators(a, p):
    return load("ios-crash-log-triage", "sim_logs").devices(booted_only=bool(a.get("booted_only")))


def h_devices(a, p):
    return load("ios-crash-log-triage", "sim_logs").physical()


def h_sim_logs(a, p):
    return load("ios-crash-log-triage", "sim_logs").logs(**_kw(
        a, "device", "last", "process", "subsystem", "category", "contains", "errors_only", "level", "max_lines"))


def h_scan_log(a, p):
    if bool(a.get("path")) == bool(a.get("text")):
        raise ArgumentError("pass exactly one of path or text")
    c = common()
    text = a.get("text") or c.read_text(p.resolve(a["path"], kind="file", exts=(".log", ".txt")))
    return load("ios-crash-log-triage", "sim_logs").scan_text(text)


def h_crash_list(a, p):
    dirs = [p.resolve(a["dir"], kind="dir")] if a.get("dir") else None
    if dirs is None:
        _system(p)
    return load("ios-crash-log-triage", "crash_report").list_reports(dirs, **_kw(a, "app", "limit", "crashes_only"))


def h_crash_summary(a, p):
    return load("ios-crash-log-triage", "crash_report").summarize(
        p.resolve(a["path"], kind="file", exts=(".ips", ".crash")), **_kw(a, "max_frames"))


PATHS = A("Swift files or directories (relative paths resolve against the project root; default: project root)")

TOOLS: List[Tool] = [
    Tool("xcode_environment", "Xcode environment",
         "macOS, Xcode, Swift versions and the active developer directory, with a hint when Xcode is "
         "unselected or its license is not accepted. Run first when xcodebuild/simctl tools fail.",
         {}, h_environment),
    Tool("find_xcode_projects", "Find Xcode projects",
         "Locate .xcodeproj/.xcworkspace, Package.swift, Info.plist, entitlements, GoogleService-Info.plist and "
         "Firebase rules files under a directory.",
         {"root": S("directory to search (default: project root)"), "max_depth": I("directory depth", 1, 10, 5)},
         h_find_projects),
    Tool("list_schemes", "List schemes", "`xcodebuild -list -json`: schemes, targets and configurations.",
         {"path": S(".xcodeproj or .xcworkspace")}, h_list_schemes, ["path"]),
    Tool("show_build_settings", "Show build settings",
         "Resolved build settings that matter for signing, bundle ids, Info.plist, deployment target and Swift "
         "concurrency (`xcodebuild -showBuildSettings`, no build, no package resolution).",
         {"path": S(".xcodeproj or .xcworkspace"), "scheme": S("scheme (required for workspaces)"),
          "target": S("target name"), "configuration": S("e.g. Debug or Release"),
          "keys": A("only these settings"), "all": B("return every setting")},
         h_build_settings, ["path"]),
    Tool("find_build_logs", "Find build logs",
         "Newest .xcactivitylog files in DerivedData: Build logs by default (feed one to extract_build_errors); "
         "pass kind for Test/Package logs.",
         {"project": S("DerivedData folder prefix, usually the project name"), "limit": I("max logs", 1, 50, 5),
          "kind": {"type": "string", "enum": ["Build", "Test", "Package", "Launch", "Localization", "all"],
                   "default": "Build", "description": "log folder; Build is what extract_build_errors needs"}},
         h_build_logs),
    Tool("extract_build_errors", "Extract build errors",
         "Parse xcodebuild output or an .xcactivitylog: de-duplicated errors classified as signing, provisioning, "
         "info-plist, resources, linker, module, spm, concurrency... each with a fix hint, plus failed commands.",
         {"path": S("build log (.log/.txt/.xcactivitylog)"), "text": S("raw build output instead of a path"),
          "include_warnings": B("also return warnings"), "max_items": I("max errors", 1, 500, 50)},
         h_build_errors),
    Tool("read_plist", "Read property list",
         "Read an Info.plist, .entitlements, PrivacyInfo.xcprivacy or provisioning profile as JSON. Secrets "
         "(API keys, tokens) are masked; profile certificates are reduced to fingerprints.",
         {"path": S("property list path"), "keys": A("only return these top-level keys")}, h_read_plist, ["path"]),
    Tool("check_info_plist", "Check Info.plist",
         "Validate required keys, $(VARIABLE) expansion, privacy usage descriptions against APIs used in sources, "
         "ATS, LSApplicationQueriesSchemes, version formats and launch screen. Understands INFOPLIST_KEY_* "
         "settings of generated Info.plists.",
         {"plist": S("Info.plist or built .app"), "sources": A("source dirs to scan for privacy-gated APIs"),
          "project": S(".xcodeproj for build settings"), "built": B("treat as a built product")},
         h_check_info_plist),
    Tool("check_bundle_resources", "Check bundle resources",
         "Find Bundle.url(forResource:), UIImage(named:), Image(\"…\"), Color(\"…\") and font lookups that will "
         "return nil: missing files/assets, files not copied into the .app, folder-reference sub-paths, "
         "Bundle.main vs Bundle.module in packages.",
         {"sources": A("source directories"), "app": S("built .app to verify"), "plist": S("Info.plist")},
         h_bundle_resources, ["sources"]),
    Tool("inspect_provisioning_profile", "Inspect provisioning profile",
         "Decode a .mobileprovision: team, App ID, type (development/ad-hoc/app-store/enterprise), expiry, devices, "
         "entitlements and certificate SHA-1s; optionally verify a matching identity exists in the keychain.",
         {"path": S(".mobileprovision / .provisionprofile"), "check_keychain": B("check local identities")},
         h_inspect_profile, ["path"]),
    Tool("list_provisioning_profiles", "List provisioning profiles",
         "Installed provisioning profiles (Xcode's profile folders), optionally only those matching a bundle id.",
         {"bundle_id": S("e.g. com.acme.app")}, h_list_profiles),
    Tool("list_signing_identities", "List signing identities",
         "Code-signing identities in the keychain (name, SHA-1, validity). Never exports keys.",
         {}, h_identities),
    Tool("compare_entitlements", "Compare entitlements to profile",
         "Check that every entitlement requested by an .entitlements file (or a signed .app) is granted by the "
         "provisioning profile: capabilities, aps-environment, app groups, keychain groups, bundle id match.",
         {"entitlements": S(".entitlements path, or -signed- to read from the signed app"),
          "profile": S("provisioning profile"), "app": S("built .app (uses embedded.mobileprovision)"),
          "bundle_id": S("expected bundle id")}, h_compare_entitlements, ["entitlements"]),
    Tool("swiftui_lint", "SwiftUI state & layout lint",
         "Static checks for re-render loops and identity bugs (onChange feedback, .id(UUID()), GeometryReader "
         "state loops), @State/@StateObject/@ObservedObject/@Observable misuse, NavigationStack/TabView title "
         "glitches and lazy navigationDestination.",
         {"paths": PATHS}, h_swiftui_lint),
    Tool("swift_concurrency_check", "Swift concurrency check",
         "Swift 6 isolation hazards (non-@MainActor ObservableObject, mutable statics/globals, Task.detached, "
         "@unchecked Sendable, DispatchQueue.main.sync...) plus the project's language mode and default isolation.",
         {"paths": PATHS, "settings": S(".xcodeproj, project.pbxproj or Package.swift")}, h_concurrency),
    Tool("check_firebase_config", "Check Firebase config",
         "Validate GoogleService-Info.plist (keys, platform, bundle id match, duplicates), FirebaseApp.configure() "
         "count and ordering, Google/Phone sign-in URL schemes and emulator guards. API keys are masked.",
         {"root": S("project root"), "plist": S("explicit GoogleService-Info.plist"), "bundle_id": S("expected id"),
          "info_plist": S("app Info.plist"), "sources": A("Swift sources (default: root)")}, h_firebase_config),
    Tool("lint_firebase_rules", "Lint Firebase security rules",
         "Lint firestore.rules / storage.rules: world-open rules, expired test-mode rules (sudden "
         "PERMISSION_DENIED), over-broad auth, missing rules_version 2, and collections used in code without rules.",
         {"paths": A(".rules files"), "root": S("directory to search when paths is omitted"),
          "sources": A("Swift sources to cross-check collection names"),
          "today": S("YYYY-MM-DD override for expiry checks")}, h_firebase_rules),
    Tool("mapkit_check", "MapKit / Core Location check",
         "Static checks for location permission keys, released CLLocationManager, background location crashes, "
         "invalid coordinates/spans, annotation view reuse, MKUserLocation handling and updateUIView loops.",
         {"paths": PATHS, "plist": S("app Info.plist"), "project": S(".xcodeproj (generated Info.plist)")},
         h_mapkit),
    Tool("list_simulators", "List simulators", "Simulators with state, runtime and UDID (`simctl list`).",
         {"booted_only": B("only booted simulators")}, h_simulators),
    Tool("list_devices", "List devices", "Paired/connected physical devices via `devicectl` (Xcode 15+).",
         {}, h_devices),
    Tool("simulator_logs", "Simulator logs",
         "Recent unified-log lines from a simulator filtered by process/subsystem/category/text, with known "
         "runtime warnings classified. Filters are validated; raw predicates are not accepted.",
         {"device": S("'booted' or a simulator UDID"), "last": S("time window like 30s, 5m, 1h (max 1h)"),
          "process": S("process name"), "subsystem": S("e.g. com.acme.app"), "category": S("log category"),
          "contains": S("message substring (case-insensitive)"), "errors_only": B("only error/fault messages"),
          "level": {"type": "string", "enum": ["default", "info", "debug"], "description": "minimum level"},
          "max_lines": I("max lines returned", 1, 2000, 300)}, h_sim_logs),
    Tool("scan_runtime_log", "Scan runtime log",
         "Classify a saved Xcode console / device log for known runtime warnings (SwiftUI publishing/state "
         "updates, AttributeGraph cycles, navigation, Auto Layout, actor isolation, privacy, ATS, MapKit, "
         "Firebase permission/config errors).",
         {"path": S("log file (.log/.txt)"), "text": S("pasted log text")}, h_scan_log),
    Tool("list_crash_reports", "List crash reports",
         "Newest .ips/.crash reports from ~/Library/Logs/DiagnosticReports and synced device logs.",
         {"app": S("filter by app name"), "limit": I("max reports", 1, 200, 20), "dir": S("other directory"),
          "crashes_only": B("skip hangs/jetsam")}, h_crash_list),
    Tool("summarize_crash_report", "Summarize crash report",
         "Exception, termination reason, crashed-thread frames, diagnosis (Swift trap, watchdog, NSException, "
         "actor-isolation violation, TCC...) and atos commands for unsymbolicated frames.",
         {"path": S(".ips or .crash file"), "max_frames": I("frames per thread", 1, 200, 15)},
         h_crash_summary, ["path"]),
]

BY_NAME = {t.name: t for t in TOOLS}


def call(name: str, args: Any, policy: PathPolicy) -> Any:
    tool = BY_NAME[name]
    args = validate(tool, args)
    c = common()
    # Every file a script walks to or opens during this call must pass the same policy as the
    # path arguments, so a symlink inside a scanned project cannot reach outside the roots.
    c.PATH_GUARD = policy.allows
    try:
        return tool.handler(args, policy)
    finally:
        c.PATH_GUARD = None


__all__ = ["TOOLS", "BY_NAME", "call", "ArgumentError", "PolicyError", "SkillsNotFound", "common"]
