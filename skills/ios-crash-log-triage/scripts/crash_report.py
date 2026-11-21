#!/usr/bin/env python3
"""List and summarise iOS/macOS crash reports (.ips JSON and legacy .crash text).

Prints the exception, termination reason, crashed-thread backtrace and a plain-English
diagnosis (Swift runtime trap, null dereference, watchdog, uncaught NSException, actor
isolation violation, ...), plus ready-to-run `atos` commands for unsymbolicated frames.

Subcommands:
  list [--dir D] [--app NAME] [--limit N]   newest reports (default: ~/Library/Logs/DiagnosticReports
                                             and device logs synced by Xcode/Finder)
  summary PATH [--frames N]                 summarise one report

Examples:
  crash_report.py list --app MyApp
  crash_report.py summary ~/Library/Logs/DiagnosticReports/MyApp-2026-11-20-101500.ips
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

REPORT_DIRS = [
    "~/Library/Logs/DiagnosticReports",
    "~/Library/Logs/CrashReporter/MobileDevice",
]
BUG_TYPES = {"309": "crash", "109": "crash (legacy)", "288": "stackshot/hang", "298": "jetsam (memory)"}

# (regex over exception + termination + app-specific info + top frames, diagnosis, next step)
DIAGNOSES = [
    (r"0x8badf00d|8badf00d", "Watchdog kill: the main thread was blocked too long (launch, resume or scene update).",
     "Move synchronous I/O, network, Core Data/SwiftData fetches and big decoding off the main actor."),
    (r"0xdead10cc|dead10cc", "Killed while suspended holding a file lock (often SQLite/Core Data in an App Group "
     "container).", "Finish or cancel database work before suspension (beginBackgroundTask) and close files."),
    (r"Unexpectedly found nil|unexpectedly found nil", "Swift runtime trap: force-unwrapped an Optional that was nil.",
     "Find the `!` / implicitly unwrapped optional on the top app frame; use guard let / if let."),
    (r"Index out of range|index out of range", "Swift runtime trap: array index out of range.",
     "Check the collection count before subscripting; watch for stale indices after async updates."),
    (r"Incorrect actor executor assumption|swift_task_isCurrentExecutor|dispatch_assert_queue|"
     r"_dispatch_assert_queue_fail|BUG IN CLIENT OF LIBDISPATCH: Assertion failed: Block was expected to execute on queue",
     "Actor isolation violated at runtime: @MainActor code ran on a background queue (Swift 6 dynamic check).",
     "A callback from a non-isolated API (delegate, completion handler, NotificationCenter) reached @MainActor code. "
     "Mark the callback nonisolated and hop with `Task { @MainActor in ... }`, or fix the queue the API is given."),
    (r"Fatal error|fatalError|precondition|assertionFailure", "Swift runtime trap: fatalError/precondition failed.",
     "Read the 'Application Specific Information' message; it contains the trap text and source location."),
    (r"The default FirebaseApp instance must be configured|must call `?FirebaseApp\.configure",
     "Firebase used before FirebaseApp.configure().",
     "See ios-map-firebase-diagnostics: run check_firebase_config.py and fix initialization order."),
    (r"Invalid Region|MKCoordinateRegion", "MapKit received an invalid region (NaN/out-of-range center or span).",
     "Validate coordinates and spans before setRegion; see ios-map-firebase-diagnostics/references/mapkit.md."),
    (r"NSInternalInconsistencyException.*(?:background location|UIBackgroundModes)|allowsBackgroundLocationUpdates",
     "Background location enabled without the 'location' background mode.", "Add UIBackgroundModes: location."),
    (r"This app has crashed because it attempted to access privacy-sensitive data|UsageDescription",
     "TCC crash: a privacy-protected API was used without its Info.plist usage description.",
     "Add the NS*UsageDescription key named in the message (check_plist.py --sources)."),
    (r"NSInvalidArgumentException|unrecognized selector", "Uncaught NSException: invalid argument or unrecognized "
     "selector (often a nil passed to Objective-C, wrong outlet/class, or a zombie).",
     "Read lastExceptionBacktrace; enable Zombie Objects if the receiver type looks wrong."),
    (r"NSRangeException", "Uncaught NSException: index beyond bounds in an Objective-C collection.", ""),
    (r"NSUnknownKeyException|not key value coding-compliant", "Broken IBOutlet/KVC connection.",
     "Remove stale outlet connections in the storyboard/xib."),
    (r"EXC_BAD_ACCESS.*KERN_INVALID_ADDRESS at 0x0{0,12}[0-9a-f]{0,3}\b|SIGSEGV.*0x0+\b",
     "Memory access to a null/near-null address.",
     "Usually an unowned/unsafe reference or C/ObjC pointer. Run with Address Sanitizer."),
    (r"EXC_BAD_ACCESS|SIGSEGV|SIGBUS", "Invalid memory access (use-after-free, data race, or unsafe pointer).",
     "Reproduce with Address Sanitizer and Thread Sanitizer enabled in the scheme."),
    (r"EXC_RESOURCE", "Exceeded a CPU/memory/wakeup resource limit.", "Profile with Instruments (Allocations/Time)."),
    (r"EXC_CRASH.*SIGABRT|SIGABRT", "abort(): uncaught exception or failed assertion.",
     "Check Application Specific Information and lastExceptionBacktrace."),
    (r"EXC_BREAKPOINT|SIGTRAP", "Trap instruction: Swift runtime error or explicit breakpoint/trap.",
     "The top app frame is the failing line; look for !, try!, as!, array subscripts, or fatalError."),
]
_DIAG = [(re.compile(rx, re.S), d, n) for rx, d, n in DIAGNOSES]


def _read_ips(path: str) -> tuple:
    text = c.read_text(path)
    first, _, rest = text.partition("\n")
    try:
        header = json.loads(first)
    except ValueError:
        return None, None, text
    try:
        body = json.loads(rest) if rest.strip() else None
    except ValueError:
        body = None
    return header, body, text


def list_reports(dirs: Optional[List[str]] = None, app: Optional[str] = None, limit: int = 20,
                 crashes_only: bool = False) -> Dict[str, Any]:
    reports = []
    for d in dirs or REPORT_DIRS:
        d = os.path.expanduser(d)
        if not os.path.isdir(d):
            continue
        for dirpath, _, filenames in os.walk(d):
            for f in filenames:
                if not f.endswith((".ips", ".crash")):
                    continue
                path = os.path.join(dirpath, f)
                header, _, _ = _read_ips(path) if f.endswith(".ips") else (None, None, None)
                name = (header or {}).get("app_name") or (header or {}).get("name") or f.split("-")[0]
                bug = str((header or {}).get("bug_type", "109" if f.endswith(".crash") else ""))
                if app and app.lower() not in name.lower() and app.lower() not in f.lower():
                    continue
                if crashes_only and bug not in ("309", "109"):
                    continue
                reports.append({"path": path, "app": name, "bundle_id": (header or {}).get("bundleID"),
                                "version": (header or {}).get("app_version"), "os": (header or {}).get("os_version"),
                                "timestamp": (header or {}).get("timestamp"), "kind": BUG_TYPES.get(bug, f"bug_type {bug}"),
                                "mtime": os.path.getmtime(path)})
    reports.sort(key=lambda r: r["mtime"], reverse=True)
    return {"reports": reports[:max(1, limit)], "total": len(reports),
            "searched": [os.path.expanduser(d) for d in dirs or REPORT_DIRS]}


def _frame_text(frame: Dict[str, Any], images: List[Dict[str, Any]]) -> Dict[str, Any]:
    img = images[frame.get("imageIndex", -1)] if 0 <= frame.get("imageIndex", -1) < len(images) else {}
    out = {"image": img.get("name") or "???", "symbol": frame.get("symbol"), "offset": frame.get("imageOffset")}
    if frame.get("sourceFile"):
        out["source"] = f"{frame['sourceFile']}:{frame.get('sourceLine', '?')}"
    if img.get("base") is not None and frame.get("imageOffset") is not None:
        out["address"] = hex(img["base"] + frame["imageOffset"])
        out["load_address"] = hex(img["base"])
    return out


def summarize(path: str, max_frames: int = 15) -> Dict[str, Any]:
    if not os.path.isfile(path):
        raise c.UsageError(f"no such crash report: {path}")
    header, body, text = _read_ips(path) if path.endswith(".ips") else (None, None, c.read_text(path))
    result: Dict[str, Any] = {"path": path}
    if body:
        images = body.get("usedImages", [])
        exc = body.get("exception", {})
        term = body.get("termination", {})
        threads = body.get("threads", [])
        idx = body.get("faultingThread", next((i for i, t in enumerate(threads) if t.get("triggered")), 0))
        crashed = threads[idx] if 0 <= idx < len(threads) else {}
        asi = body.get("asi", {})
        result.update({
            "app": body.get("procName") or (header or {}).get("app_name"),
            "bundle_id": (body.get("bundleInfo") or {}).get("CFBundleIdentifier") or (header or {}).get("bundleID"),
            "version": "{} ({})".format((body.get("bundleInfo") or {}).get("CFBundleShortVersionString", "?"),
                                        (body.get("bundleInfo") or {}).get("CFBundleVersion", "?")),
            "os": (header or {}).get("os_version") or (body.get("osVersion") or {}).get("train"),
            "device": body.get("modelCode"),
            "incident": body.get("incident") or (header or {}).get("incident_id"),
            "exception": {k: exc.get(k) for k in ("type", "signal", "codes", "subtype") if exc.get(k)},
            "termination": {k: term.get(k) for k in ("namespace", "code", "indicator", "reasons", "byProc") if term.get(k)},
            "app_specific_info": asi,
            "crashed_thread": {"index": idx, "name": crashed.get("name"), "queue": crashed.get("queue"),
                               "frames": [_frame_text(f, images) for f in crashed.get("frames", [])[:max_frames]]},
        })
        if isinstance(term.get("code"), int):
            result["termination"]["code_hex"] = hex(term["code"])
        last_exc = body.get("lastExceptionBacktrace")
        if last_exc:
            result["last_exception_backtrace"] = [_frame_text(f, images) for f in last_exc[:max_frames]]
        app_name = result.get("app") or ""
        main_image = next((i for i in images if i.get("name") == app_name), images[0] if images else {})
        unsym = [f for f in result["crashed_thread"]["frames"] if f["image"] == main_image.get("name") and not f.get("symbol")]
        if unsym and main_image:
            result["symbolicate"] = {
                "dsym_uuid": main_image.get("uuid"),
                "atos": f"atos -arch {main_image.get('arch', 'arm64')} -o {app_name}.app.dSYM/Contents/Resources/DWARF/"
                        f"{app_name} -l {unsym[0]['load_address']} " + " ".join(f["address"] for f in unsym[:10]),
                "find_dsym": f"mdfind \"com_apple_xcode_dsym_uuids == {str(main_image.get('uuid', '')).upper()}\"",
            }
        haystack = json.dumps([result["exception"], result["termination"], asi,
                               result["crashed_thread"]["frames"][:8], result.get("last_exception_backtrace", [])[:8]])
    else:
        fields = {}
        for key in ("Process", "Identifier", "Version", "OS Version", "Exception Type", "Exception Codes",
                    "Termination Reason", "Crashed Thread", "Hardware Model"):
            m = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, re.M)
            if m:
                fields[key] = m.group(1).strip()
        asi = re.search(r"Application Specific Information:\n(.*?)\n\n", text, re.S)
        thread = re.search(r"^Thread \d+ Crashed:.*?\n(.*?)\n\n", text, re.S | re.M)
        result.update({"format": "legacy text", "fields": fields,
                       "app_specific_info": asi.group(1).strip() if asi else None,
                       "crashed_thread": {"frames": thread.group(1).splitlines()[:max_frames] if thread else []}})
        haystack = json.dumps(fields) + (asi.group(1) if asi else "") + (thread.group(1)[:3000] if thread else "")

    diagnosis = []
    for rx, what, nxt in _DIAG:
        if rx.search(haystack):
            diagnosis.append({"finding": what, "next_step": nxt})
    # The generic signal diagnoses add little once a specific cause matched.
    if len(diagnosis) > 1:
        generic = ("abort()", "Trap instruction", "Invalid memory access", "Swift runtime trap: fatalError")
        diagnosis = [d for d in diagnosis if not d["finding"].startswith(generic)] or diagnosis
    result["diagnosis"] = diagnosis[:4]
    return result


def format_summary(r: Dict[str, Any]) -> str:
    out = [f"Report:    {r['path']}"]
    if r.get("format") == "legacy text":
        out += [f"{k}: {v}" for k, v in r["fields"].items()]
        if r.get("app_specific_info"):
            out.append(f"App info:  {r['app_specific_info']}")
        out.append("Crashed thread:")
        out += [f"  {line}" for line in r["crashed_thread"]["frames"]]
    else:
        out.append(f"App:       {r.get('app')} {r.get('version')}  [{r.get('bundle_id')}]  on {r.get('os')} {r.get('device') or ''}")
        e, t = r.get("exception", {}), r.get("termination", {})
        out.append(f"Exception: {e.get('type', '-')} {e.get('signal', '')} {e.get('subtype', '')}".rstrip())
        if t:
            out.append(f"Termination: {t.get('namespace', '')} {t.get('code_hex', t.get('code', ''))} "
                       f"{'; '.join(t.get('reasons', []) or [])} {t.get('indicator', '')}".rstrip())
        if r.get("app_specific_info"):
            out.append(f"App info:  {json.dumps(r['app_specific_info'])[:600]}")
        th = r["crashed_thread"]
        out.append(f"Crashed thread {th['index']}" + (f" ({th['queue']})" if th.get("queue") else "") + ":")
        for i, f in enumerate(th["frames"]):
            sym = f.get("symbol") or f.get("address") or "?"
            out.append(f"  #{i:<2} {f['image']:<28} {sym}" + (f"  ({f['source']})" if f.get("source") else ""))
        if r.get("last_exception_backtrace"):
            out.append("Last exception backtrace:")
            for i, f in enumerate(r["last_exception_backtrace"]):
                out.append(f"  #{i:<2} {f['image']:<28} {f.get('symbol') or f.get('address')}")
        if r.get("symbolicate"):
            out.append(f"Unsymbolicated app frames. dSYM UUID {r['symbolicate']['dsym_uuid']}:")
            out.append(f"  {r['symbolicate']['find_dsym']}")
            out.append(f"  {r['symbolicate']['atos']}")
    out.append("Diagnosis:" if r["diagnosis"] else "Diagnosis: no known pattern matched; read the top app frame.")
    for d in r["diagnosis"]:
        out.append(f"  - {d['finding']}" + (f"\n    next: {d['next_step']}" if d["next_step"] else ""))
    return "\n".join(out)


@c.cli
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list", help="list recent crash reports")
    pl.add_argument("--dir", action="append", help="report directory (repeatable)")
    pl.add_argument("--app", help="filter by app/process name")
    pl.add_argument("--limit", type=int, default=20)
    pl.add_argument("--crashes-only", action="store_true", help="skip hangs/jetsam/diagnostic reports")
    pl.add_argument("--json", action="store_true")
    ps = sub.add_parser("summary", help="summarise one report")
    ps.add_argument("path")
    ps.add_argument("--frames", type=int, default=15, help="frames to show per thread (default: %(default)s)")
    ps.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    if args.cmd == "list":
        result = list_reports(args.dir, args.app, args.limit, args.crashes_only)
        if args.json:
            c.print_json(result)
        else:
            for r in result["reports"]:
                print(f"{r['timestamp'] or '-':<26} {r['kind']:<15} {r['app']:<24} {r['version'] or '':<10} {r['path']}")
            print(f"{len(result['reports'])} of {result['total']} report(s) in {', '.join(result['searched'])}")
        return 0
    result = summarize(args.path, args.frames)
    print(json.dumps(result, indent=2, default=str) if args.json else format_summary(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
