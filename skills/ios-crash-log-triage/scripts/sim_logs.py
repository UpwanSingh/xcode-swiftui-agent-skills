#!/usr/bin/env python3
"""Read-only simulator/device inventory, unified-log queries and runtime-warning triage.

Subcommands:
  devices [--booted]          simulators (`xcrun simctl list devices --json`)
  physical                    connected/paired devices (`xcrun devicectl list devices`)
  logs [--device booted|UDID] recent unified-log lines from a simulator, filtered safely
       [--last 5m] [--process App] [--subsystem com.acme.app] [--contains TEXT] [--errors-only]
  scan FILE|-                 classify a saved console/Xcode log for known runtime warnings
                              (SwiftUI, concurrency, layout, MapKit, Firebase, privacy, ATS)

Predicates are built from validated flags (no raw predicate strings, no shell).

Examples:
  sim_logs.py devices --booted
  sim_logs.py logs --process MyApp --last 10m --errors-only
  pbpaste | sim_logs.py scan -
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

UDID = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
LAST = re.compile(r"^(\d{1,4})([smh])$")
SAFE_VALUE = re.compile(r"^[\w .:/@+-]{1,120}$")

# (id, area, regex, meaning, next step)
RUNTIME_WARNINGS = [
    ("RW001", "swiftui", r"Publishing changes from background threads is not allowed",
     "An ObservableObject @Published property was set off the main thread.",
     "Mark the model @MainActor (swiftui-state-layout: concurrency_check.py SC006)."),
    ("RW002", "swiftui", r"Publishing changes from within view updates is not allowed",
     "State was mutated during body evaluation (undefined behaviour, can loop).",
     "Move the mutation into .task/.onAppear/.onChange or an action; never assign state in body or a getter."),
    ("RW003", "swiftui", r"Modifying state during view update",
     "A @State/@Binding was written while SwiftUI was rendering.",
     "Defer the write (action, .onChange, .task); check bindings passed to Map/TextField/sheets."),
    ("RW004", "swiftui", r"AttributeGraph: cycle detected|cycle detected through attribute",
     "Layout/state dependency cycle: a view's layout depends on state it writes.",
     "Look for GeometryReader/preference writes into state (swiftui_lint.py SUI008)."),
    ("RW005", "swiftui", r"onChange\(of: .*\) action tried to update multiple times per frame",
     "An onChange handler keeps changing the value it observes.", "Guard the write (SUI007)."),
    ("RW006", "swiftui", r"Bound preference .* tried to update multiple times per frame",
     "A PreferenceKey value oscillates between renders.", "Make the reduced value stable/quantised."),
    ("RW007", "navigation", r"A NavigationLink is presenting a value of type .* but there is no matching "
     r"navigationDestination|no matching navigationDestination",
     "NavigationLink(value:) has no .navigationDestination(for:) in the stack.",
     "Add .navigationDestination(for: Type.self) inside the NavigationStack, outside lazy containers."),
    ("RW008", "navigation", r"Do not put a navigation destination modifier inside a \"lazy\" container",
     ".navigationDestination is inside List/LazyVStack.", "Move it to the List/stack root (SUI012)."),
    ("RW009", "navigation", r"Only root-level navigation destinations are effective",
     "A navigationDestination is declared in a pushed view for a type the root already handles.",
     "Declare each destination type once, near the NavigationStack root."),
    ("RW010", "layout", r"Unable to simultaneously satisfy constraints",
     "Conflicting Auto Layout constraints (UIKit or hosted SwiftUI in UIKit).",
     "Find the constraint listed after 'Will attempt to recover by breaking'; lower a priority."),
    ("RW011", "layout", r"Invalid frame dimension \(negative or non-finite\)",
     "A frame/size computed from GeometryReader was NaN, infinite or negative.",
     "Clamp computed sizes (max(0, ...)) and guard divisions by zero."),
    ("RW012", "concurrency", r"Incorrect actor executor assumption|Main actor-isolated .* called on a background",
     "Runtime actor-isolation check failed (Swift 6).", "See references/swift6-concurrency.md in swiftui-state-layout."),
    ("RW013", "performance", r"Thread Performance Checker|Main Thread Checker: UI API called on a background thread",
     "UI API used off the main thread or a priority inversion.", "Hop to @MainActor before touching UI."),
    ("RW014", "privacy", r"This app has crashed because it attempted to access privacy-sensitive data|"
     r"must contain an? \w+UsageDescription key",
     "Missing NS*UsageDescription key (TCC).", "check_plist.py Info.plist --sources ."),
    ("RW015", "network", r"App Transport Security has blocked a cleartext HTTP",
     "ATS blocked an http:// request.", "Use https or add a narrow NSExceptionDomains entry."),
    ("RW016", "mapkit", r"Invalid Region|MKMapView must be initialized on the main thread",
     "MapKit misuse: invalid region or off-main map access.", "mapkit_check.py (MK005)."),
    ("RW017", "location", r"kCLErrorDomain|Error Domain=kCLErrorDomain Code=(?:0|1)",
     "Core Location error (0 = location unknown, 1 = denied).",
     "Check authorization status/usage keys; on the simulator set Features > Location."),
    ("RW018", "location", r"This method can cause UI unresponsiveness if invoked on the main thread",
     "CLLocationManager.locationServicesEnabled()/authorization queried on the main thread.",
     "Use the delegate's locationManagerDidChangeAuthorization instead of polling."),
    ("RW019", "firebase", r"The default FirebaseApp instance must be configured|FirebaseApp\.configure\(\) .* before",
     "Firebase used before configure().", "check_firebase_config.py (FB007/FB008)."),
    ("RW020", "firebase", r"Missing or insufficient permissions|PERMISSION_DENIED",
     "Firestore/Storage security rules denied the request.", "lint_firebase_rules.py --sources App/ (FR002/FR007)."),
    ("RW021", "firebase", r"The Firestore instance has already been started and its settings can no longer be changed",
     "Firestore settings assigned after first use.", "Set Firestore.firestore().settings right after configure()."),
    ("RW022", "firebase", r"\[FirebaseAuth\].*(?:invalid-api-key|app-not-authorized|INVALID_APP_CREDENTIAL)|"
     r"ERROR_APP_NOT_AUTHORIZED|API key not valid",
     "Auth rejected the app's config (bundle id / API key restrictions / wrong plist).",
     "check_firebase_config.py; verify the key's iOS bundle id restriction in Google Cloud console."),
    ("RW023", "firebase", r"APNS device token not set before retrieving FCM Token|No APNS token specified",
     "FCM token requested before APNs registration finished.",
     "Register for remote notifications and set Messaging.messaging().apnsToken first."),
    ("RW025", "firebase", r"The query requires an index|FAILED_PRECONDITION",
     "Firestore composite index missing for this query.",
     "Open the console link printed with the error, or add the index to firestore.indexes.json."),
    ("RW024", "storage", r"Object .* does not exist|storage/object-not-found|bucket.*does not exist",
     "Wrong Storage path or bucket (STORAGE_BUCKET).", "Compare STORAGE_BUCKET with the console."),
]
_RW = [(i, a, re.compile(rx), m, n) for i, a, rx, m, n in RUNTIME_WARNINGS]


def _runtime_label(identifier: str) -> str:
    m = re.search(r"SimRuntime\.(\w+)-([\d-]+)$", identifier)
    return f"{m.group(1)} {m.group(2).replace('-', '.')}" if m else identifier


def devices(booted_only: bool = False) -> Dict[str, Any]:
    code, out, err = c.run_command(["xcrun", "simctl", "list", "devices", "--json"], timeout=60)
    if code != 0:
        raise c.UsageError(f"simctl failed: {(err or out).strip()[:500]}")
    data = json.loads(out)
    sims = []
    for runtime, devs in data.get("devices", {}).items():
        for d in devs:
            if booted_only and d.get("state") != "Booted":
                continue
            if not d.get("isAvailable", True) and not booted_only:
                continue
            sims.append({"name": d.get("name"), "udid": d.get("udid"), "state": d.get("state"),
                         "runtime": _runtime_label(runtime)})
    sims.sort(key=lambda s: (s["state"] != "Booted", s["runtime"], s["name"] or ""))
    return {"simulators": sims, "count": len(sims)}


def physical() -> Dict[str, Any]:
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        code, out, err = c.run_command(["xcrun", "devicectl", "list", "devices", "--json-output", tmp], timeout=60)
        if code != 0:
            raise c.UsageError(f"devicectl failed (Xcode 15+ required): {(err or out).strip()[:500]}")
        with open(tmp) as fh:
            data = json.load(fh)
    finally:
        os.unlink(tmp)
    devs = []
    for d in data.get("result", {}).get("devices", []):
        props, hw, conn = d.get("deviceProperties", {}), d.get("hardwareProperties", {}), d.get("connectionProperties", {})
        devs.append({"name": props.get("name"), "identifier": d.get("identifier"), "udid": hw.get("udid"),
                     "model": hw.get("marketingName") or hw.get("productType"), "os": props.get("osVersionNumber"),
                     "developer_mode": props.get("developerModeStatus"), "pairing": conn.get("pairingState"),
                     "transport": conn.get("transportType"), "tunnel": conn.get("tunnelState")})
    return {"devices": devs, "count": len(devs)}


def _quote(value: str, flag: str) -> str:
    if not SAFE_VALUE.match(value):
        raise c.UsageError(f"{flag} may only contain letters, digits, spaces and . : / @ + - _")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_predicate(process: Optional[str] = None, subsystem: Optional[str] = None, category: Optional[str] = None,
                    contains: Optional[str] = None, errors_only: bool = False) -> Optional[str]:
    parts = []
    if process:
        parts.append(f"process == {_quote(process, '--process')}")
    if subsystem:
        parts.append(f"subsystem == {_quote(subsystem, '--subsystem')}")
    if category:
        parts.append(f"category == {_quote(category, '--category')}")
    if contains:
        parts.append(f"eventMessage CONTAINS[c] {_quote(contains, '--contains')}")
    if errors_only:
        parts.append("(messageType == error OR messageType == fault)")
    return " AND ".join(parts) or None


def logs(device: str = "booted", last: str = "5m", process: Optional[str] = None, subsystem: Optional[str] = None,
         category: Optional[str] = None, contains: Optional[str] = None, errors_only: bool = False,
         level: str = "default", max_lines: int = 300) -> Dict[str, Any]:
    if device != "booted" and not UDID.match(device):
        raise c.UsageError("--device must be 'booted' or a simulator UDID")
    m = LAST.match(last)
    if not m:
        raise c.UsageError("--last must look like 30s, 5m or 1h")
    seconds = int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)]
    if seconds > 3600:
        raise c.UsageError("--last is capped at 1h to keep queries fast")
    if level not in ("default", "info", "debug"):
        raise c.UsageError("--level must be default, info or debug")
    cmd = ["xcrun", "simctl", "spawn", device, "log", "show", "--style", "compact", "--last", last]
    if level in ("info", "debug"):
        cmd.append("--info")
    if level == "debug":
        cmd.append("--debug")
    predicate = build_predicate(process, subsystem, category, contains, errors_only)
    if predicate:
        cmd += ["--predicate", predicate]
    code, out, err = c.run_command(cmd, timeout=120)
    if code != 0:
        hint = " (no booted simulator? run `sim_logs.py devices --booted`)" if "booted" in (err or "").lower() else ""
        raise c.UsageError(f"log show failed: {(err or out).strip()[:500]}{hint}")
    lines = [ln for ln in out.splitlines() if ln.strip() and not ln.startswith(("Filtering the log data", "Timestamp"))]
    tail = lines[-max(1, min(max_lines, 2000)):]
    text = c.redact_text("\n".join(tail))
    return {"device": device, "predicate": predicate, "line_count": len(lines), "lines": text.splitlines(),
            "runtime_warnings": scan_text(text)["matches"]}


def scan_text(text: str, max_examples: int = 3) -> Dict[str, Any]:
    hits: Dict[str, Dict[str, Any]] = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        for rid, area, rx, meaning, nxt in _RW:
            if rx.search(line):
                h = hits.setdefault(rid, {"id": rid, "area": area, "meaning": meaning, "next_step": nxt,
                                          "count": 0, "examples": []})
                h["count"] += 1
                if len(h["examples"]) < max_examples:
                    h["examples"].append({"line": lineno, "text": c.redact_text(line.strip())[:400]})
    return {"matches": sorted(hits.values(), key=lambda h: (-h["count"], h["id"]))}


@c.cli
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pd = sub.add_parser("devices", help="list simulators")
    pd.add_argument("--booted", action="store_true")
    sub.add_parser("physical", help="list physical devices (devicectl)")
    pl = sub.add_parser("logs", help="show recent simulator log lines")
    pl.add_argument("--device", default="booted")
    pl.add_argument("--last", default="5m")
    pl.add_argument("--process")
    pl.add_argument("--subsystem")
    pl.add_argument("--category")
    pl.add_argument("--contains")
    pl.add_argument("--errors-only", action="store_true")
    pl.add_argument("--level", default="default", choices=("default", "info", "debug"))
    pl.add_argument("--max-lines", type=int, default=300)
    ps = sub.add_parser("scan", help="classify runtime warnings in a saved log")
    ps.add_argument("file", help="log file or - for stdin")
    for sp in sub.choices.values():
        sp.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if args.cmd == "devices":
        result = devices(args.booted)
        lines = [f"{s['state']:<9} {s['runtime']:<12} {s['name']:<32} {s['udid']}" for s in result["simulators"]]
    elif args.cmd == "physical":
        result = physical()
        lines = [f"{d['name']:<24} {d['model'] or '':<20} {d['os'] or '':<8} {d['pairing'] or ''} {d['udid'] or d['identifier']}"
                 for d in result["devices"]]
    elif args.cmd == "logs":
        result = logs(args.device, args.last, args.process, args.subsystem, args.category, args.contains,
                      args.errors_only, args.level, args.max_lines)
        lines = result["lines"] + ["", f"{result['line_count']} line(s); predicate: {result['predicate']}"]
        lines += [f"[{h['id']} {h['area']}] x{h['count']} {h['meaning']} -> {h['next_step']}" for h in result["runtime_warnings"]]
    else:
        text = sys.stdin.read() if args.file == "-" else c.read_text(args.file)
        result = scan_text(text)
        lines = []
        for h in result["matches"]:
            lines.append(f"[{h['id']} {h['area']}] x{h['count']} {h['meaning']}\n    next: {h['next_step']}")
            lines += [f"    line {e['line']}: {e['text']}" for e in h["examples"]]
        lines.append(f"{len(result['matches'])} known runtime warning type(s) found")
    if args.json:
        c.print_json(result)
    else:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
