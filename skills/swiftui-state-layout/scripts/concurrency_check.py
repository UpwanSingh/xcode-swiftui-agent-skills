#!/usr/bin/env python3
"""Find Swift 6 concurrency / actor-isolation hazards and report the project's concurrency settings.

Reads Swift sources plus project.pbxproj / Package.swift (never modifies them).
Settings matter: with SWIFT_DEFAULT_ACTOR_ISOLATION = MainActor (Xcode 26 "approachable
concurrency" templates) most UI types are already main-actor isolated, so some rules are skipped.

Examples:
  concurrency_check.py Sources/
  concurrency_check.py App/ --settings App.xcodeproj
  concurrency_check.py . --json
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

RULES = {
    "SC001": ("info", "DispatchQueue.main.async – prefer @MainActor / await MainActor.run"),
    "SC002": ("warning", "DispatchQueue.main.sync – deadlocks when already on the main thread"),
    "SC003": ("warning", "@unchecked Sendable – the compiler no longer checks this type"),
    "SC004": ("warning", "nonisolated(unsafe) – opts out of data-race checking"),
    "SC005": ("warning", "Task.detached drops actor isolation, priority and task-locals"),
    "SC006": ("warning", "ObservableObject with @Published properties is not @MainActor"),
    "SC007": ("info", "@Observable class is not @MainActor"),
    "SC008": ("warning", "Mutable static/global variable (Swift 6 error: not concurrency-safe)"),
    "SC009": ("info", "MainActor.assumeIsolated traps when not running on the main actor"),
    "SC010": ("info", "@preconcurrency import hides Sendable diagnostics from that module"),
}

CLASS_DECL = re.compile(r"((?:@\w+(?:\([^)]*\))?\s+)*)(?:(?:public|internal|package|open|private|fileprivate|final)\s+)*"
                        r"class\s+(\w+)\s*(?:<[^>{]*>)?\s*(?::\s*([^{]+))?\{")
STATIC_VAR = re.compile(r"\bstatic\s+var\s+(\w+)([^\n]*)")
GLOBAL_VAR = re.compile(r"^(?:(?:public|internal|package|private|fileprivate)\s+)?var\s+(\w+)([^\n]*)", re.M)
SIMPLE = [
    ("SC001", re.compile(r"\bDispatchQueue\.main\.async\b"), "DispatchQueue.main.async hop",
     "Mark the type/function @MainActor, or use `await MainActor.run { }` / `Task { @MainActor in }`. "
     "Under Swift 6 the closure must be @Sendable, so captured non-Sendable state becomes an error."),
    ("SC002", re.compile(r"\bDispatchQueue\.main\.sync\b"), "DispatchQueue.main.sync",
     "Calling this on the main thread deadlocks. Make the caller async and `await` a @MainActor function."),
    ("SC003", re.compile(r"@unchecked\s+Sendable\b"), "@unchecked Sendable conformance",
     "Only valid if every mutable property is protected (Mutex/OSAllocatedUnfairLock/serial queue). "
     "Prefer an actor, a final class with only `let` Sendable properties, or `Mutex` (iOS 18+)."),
    ("SC004", re.compile(r"\bnonisolated\s*\(\s*unsafe\s*\)"), "nonisolated(unsafe)",
     "Acceptable for truly immutable-after-init values; otherwise isolate the value to an actor or protect it."),
    ("SC005", re.compile(r"\bTask\.detached\b"), "Task.detached",
     "Use `Task { }` to inherit the current actor, or a `nonisolated`/`@concurrent` async function for "
     "background work. UI updates from a detached task run off the main thread."),
    ("SC009", re.compile(r"\bMainActor\.assumeIsolated\b"), "MainActor.assumeIsolated",
     "Crashes at runtime if the caller is not on the main actor. Prefer making the callback @MainActor."),
    ("SC010", re.compile(r"@preconcurrency\s+import\s+(\w+)"), "@preconcurrency import",
     "Fine as a migration step; remove once the module adopts Sendable so real races are reported."),
]


def detect_settings(paths: Sequence[str], settings_path: Optional[str]) -> Dict[str, object]:
    """Collect language mode / isolation settings from an Xcode project or Package.swift."""
    info: Dict[str, object] = {}
    sources: List[str] = []
    if settings_path:
        sources = [settings_path]
    else:
        for p in paths:
            if os.path.isdir(p):
                sources.extend(c.find_xcodeprojs(p, max_depth=2))
                if os.path.isfile(os.path.join(p, "Package.swift")):
                    sources.append(os.path.join(p, "Package.swift"))
    for src in sources:
        if src.endswith("Package.swift"):
            text = c.read_text(src)
            m = re.search(r"swift-tools-version\s*:\s*([\d.]+)", text)
            if m:
                info["swift_tools_version"] = m.group(1)
            if re.search(r"swiftLanguageModes?\s*:\s*\[[^\]]*\.v5", text) or ".swiftLanguageMode(.v5)" in text:
                info["package_language_mode"] = "5 (opt-out present)"
            if re.search(r"\.defaultIsolation\s*\(\s*MainActor\.self\s*\)", text):
                info["default_isolation"] = "MainActor"
            features = re.findall(r'\.enable(?:Upcoming|Experimental)Feature\(\s*"(\w+)"', text)
            if features:
                info["package_features"] = sorted(set(features))
            info.setdefault("settings_sources", []).append(src)
        else:
            settings = c.read_build_settings(src)
            for key in ("SWIFT_VERSION", "SWIFT_STRICT_CONCURRENCY", "SWIFT_DEFAULT_ACTOR_ISOLATION",
                        "SWIFT_APPROACHABLE_CONCURRENCY"):
                vals = c.setting_values(settings, key)
                if vals:
                    info[key] = vals
            upcoming = sorted(k for k in settings if k.startswith("SWIFT_UPCOMING_FEATURE_"))
            if upcoming:
                info["upcoming_features"] = upcoming
            if "MainActor" in c.setting_values(settings, "SWIFT_DEFAULT_ACTOR_ISOLATION"):
                info["default_isolation"] = "MainActor"
            info.setdefault("settings_sources", []).append(src)
    versions = info.get("SWIFT_VERSION") or []
    tools = str(info.get("swift_tools_version", ""))
    if any(str(v).startswith("6") for v in versions) or (tools[:1].isdigit() and int(tools.split(".")[0]) >= 6
                                                         and "package_language_mode" not in info):
        info["language_mode"] = "6 (data-race errors)"
    elif versions or tools:
        info["language_mode"] = "5 (" + ("strict concurrency: " + ",".join(info["SWIFT_STRICT_CONCURRENCY"])
                                         if info.get("SWIFT_STRICT_CONCURRENCY") else "minimal checking") + ")"
    return info


def _is_stored(rest: str) -> bool:
    """`rest` is the text after `var name` on the same line."""
    eq, brace = rest.find("="), rest.find("{")
    if brace >= 0 and (eq < 0 or brace < eq):
        return "willSet" in rest or "didSet" in rest
    return True


def check_file(sf: c.SourceFile, default_main_actor: bool) -> List[c.Finding]:
    out: List[c.Finding] = []
    code = sf.code
    for rule, rx, title, fix in SIMPLE:
        for m in rx.finditer(code):
            out.append(c.Finding(rule, RULES[rule][0], title, sf.path, sf.line_of(m.start()), fix))

    for m in CLASS_DECL.finditer(code):
        attrs, name, inherits = m.group(1) or "", m.group(2), m.group(3) or ""
        if "@MainActor" in attrs or default_main_actor:
            continue
        brace = m.end() - 1
        close = sf.match_close(brace)
        body = code[brace:close] if close > 0 else ""
        if re.search(r"\bObservableObject\b", inherits) and "@Published" in body:
            out.append(c.Finding("SC006", RULES["SC006"][0], f"class {name}: ObservableObject publishes from any "
                                 "thread", sf.path, sf.line_of(m.start(2)),
                                 "Annotate the class @MainActor. Otherwise assigning @Published from a background "
                                 "task logs 'Publishing changes from background threads is not allowed' and races."))
        elif "@Observable" in attrs and re.search(r"\b(func|var)\b", body):
            out.append(c.Finding("SC007", RULES["SC007"][0], f"@Observable class {name} has no actor isolation",
                                 sf.path, sf.line_of(m.start(2)),
                                 "If views read it, mark it @MainActor (or set Default Actor Isolation = MainActor) "
                                 "so mutations from async work hop to the main actor."))

    for m in STATIC_VAR.finditer(code):
        line_start = code.rfind("\n", 0, m.start()) + 1
        prefix = code[line_start:m.start()]
        if re.search(r"@MainActor|nonisolated\s*\(\s*unsafe\s*\)|@TaskLocal|@\w+Actor\b", prefix):
            continue
        if not _is_stored(m.group(2)):
            continue
        enclosing = sf.enclosing_open(m.start())
        header = sf.header_before(enclosing) if enclosing >= 0 else ""
        if re.search(r"@MainActor|\bactor\b|@\w+Actor\b", header) or default_main_actor:
            continue
        out.append(c.Finding("SC008", RULES["SC008"][0], f"static var {m.group(1)} is shared mutable state",
                             sf.path, sf.line_of(m.start()),
                             "Make it `static let`, isolate it (`@MainActor static var`), or move it into an actor."))

    if os.path.basename(sf.path) != "main.swift" and not default_main_actor:
        for m in GLOBAL_VAR.finditer(code):
            if sf.depth_at(m.start()) == 0 and _is_stored(m.group(2)):
                out.append(c.Finding("SC008", RULES["SC008"][0], f"global var {m.group(1)} is shared mutable state",
                                     sf.path, sf.line_of(m.start()),
                                     "Use `let`, isolate it with @MainActor, or wrap it in an actor."))
    return out


def analyze(paths: Sequence[str], settings_path: Optional[str] = None) -> dict:
    settings = detect_settings(paths, settings_path)
    default_main = settings.get("default_isolation") == "MainActor"
    files = c.load_swift_files(paths)
    findings: List[c.Finding] = []
    for sf in files:
        findings.extend(check_file(sf, default_main))
    return c.make_result(findings, files_scanned=len(files), settings=settings)


@c.cli
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="*", help="Swift files or directories to scan")
    p.add_argument("--settings", metavar="PATH",
                   help=".xcodeproj, project.pbxproj or Package.swift to read concurrency settings from "
                        "(default: auto-detect inside directory arguments)")
    p.add_argument("--list-rules", action="store_true", help="print rule ids and exit")
    c.add_output_args(p)
    args = p.parse_args(argv)
    if args.list_rules:
        c.print_rules(RULES)
        return 0
    if not args.paths:
        p.error("at least one path is required")
    result = analyze(args.paths, args.settings)
    if args.json:
        c.print_json(result)
    else:
        s = result["settings"]
        if s:
            print("Concurrency settings:")
            for k, v in s.items():
                print(f"  {k}: {', '.join(v) if isinstance(v, list) else v}")
        else:
            print("Concurrency settings: none found (pass --settings to read a project)")
        print(c.format_findings(result))
        print(f"scanned {result['files_scanned']} Swift file(s)")
    return c.exit_status(result, args.fail_on)


if __name__ == "__main__":
    sys.exit(main())
