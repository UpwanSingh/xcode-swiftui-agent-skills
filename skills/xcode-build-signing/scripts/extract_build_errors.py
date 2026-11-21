#!/usr/bin/env python3
"""Extract, de-duplicate and classify errors from xcodebuild output or .xcactivitylog files.

Turns a 20k-line build log into the handful of root-cause errors, each tagged with a
category (signing, provisioning, linker, ...) and a fix hint from the catalog below.

Inputs: a text log (`xcodebuild ... 2>&1 | tee build.log`), an Xcode .xcactivitylog
(gzip, best effort) or `-` for stdin.

Examples:
  xcodebuild -scheme App build 2>&1 | extract_build_errors.py -
  extract_build_errors.py build.log --warnings --json
  extract_build_errors.py ~/Library/Developer/Xcode/DerivedData/App-*/Logs/Build/*.xcactivitylog
"""
from __future__ import annotations

import argparse
import gzip
import os
import re
import sys
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

# (id, category, regex, hint). First match wins, so keep specific patterns above generic ones.
CATALOG = [
    ("SIGN001", "signing", r"Signing for \".+\" requires a development team",
     "Set DEVELOPMENT_TEAM for the target (Signing & Capabilities > Team) or pass DEVELOPMENT_TEAM=XXXXXXXXXX."),
    ("SIGN002", "signing", r"No (?:signing certificate|certificate) \"?[^\"]*\"? (?:found|for team)|"
     r"No signing certificate \".+\" found",
     "The keychain has no identity (cert + private key) for this team/type. Check `inspect_signing.py identities`; "
     "download it in Xcode > Settings > Accounts or import the .p12 on CI."),
    ("SIGN003", "signing", r"errSecInternalComponent|User interaction is not allowed",
     "codesign cannot use the private key, usually a locked keychain on CI. Unlock it (`security unlock-keychain`) "
     "and set the key partition list before building."),
    ("SIGN004", "signing", r"The identity used to sign the executable is no longer valid|"
     r"certificate .*(?:has expired|is revoked|revoked)",
     "The signing certificate expired or was revoked. Create a new one and regenerate profiles that include it."),
    ("SIGN005", "signing", r"is automatically signed,? but provisioning profile .+ has been manually specified|"
     r"conflicting provisioning settings",
     "Automatic signing is on but PROVISIONING_PROFILE_SPECIFIER is set (often only for one configuration). "
     "Clear the specifier or switch the target to manual signing."),
    ("SIGN006", "signing", r"Command CodeSign failed with a nonzero exit code",
     "Read the codesign lines just above. Common causes: resource fork/xattr junk (`xattr -cr` on the asset), "
     "an unsigned embedded framework, or a locked keychain."),
    ("PROV001", "provisioning", r"No profiles for '.+' were found",
     "No profile matches this bundle id + team + type. Use automatic signing with `-allowProvisioningUpdates`, "
     "or create/download a profile for the exact bundle id."),
    ("PROV002", "provisioning", r"Provisioning profile \".+\" doesn't include signing certificate",
     "The profile was generated for a different certificate. Regenerate the profile including your cert, or "
     "sign with an identity the profile lists (`inspect_signing.py profile X.mobileprovision --check-keychain`)."),
    ("PROV003", "provisioning", r"Provisioning profile \".+\" doesn't (?:support|include) the .+ (?:capability|entitlement)",
     "The App ID lacks a capability that your .entitlements requests. Enable it for the App ID in the developer "
     "portal, regenerate the profile, or remove the entitlement."),
    ("PROV004", "provisioning", r"doesn't match the entitlements file's value for the \S+ entitlement",
     "The .entitlements file disagrees with the profile (e.g. aps-environment, app groups, iCloud containers). "
     "Run `inspect_signing.py compare --entitlements App.entitlements --profile X.mobileprovision`."),
    ("PROV005", "provisioning", r"Provisioning profile \".+\" (?:has expired|is expired)",
     "Regenerate/download a fresh profile (Xcode > Settings > Accounts > Download Manual Profiles)."),
    ("PROV006", "provisioning", r"doesn't include the currently selected device|"
     r"device .+ isn't registered|Unregistered device",
     "Register the device UDID in the developer portal and regenerate the development/ad hoc profile."),
    ("PROV007", "provisioning", r"Embedded binary's bundle identifier is not prefixed with the parent app's",
     "Extension/watch bundle ids must start with the app's id (com.acme.app.widget)."),
    ("PLIST001", "info-plist", r"Cannot code sign because the target does not have an Info\.plist|"
     r"Info\.plist file .*(?:was not found|could not be read)|Build input file cannot be found: '.*Info\.plist'",
     "INFOPLIST_FILE points to a missing file. Fix the path (relative to SRCROOT) or set "
     "GENERATE_INFOPLIST_FILE = YES."),
    ("PLIST002", "info-plist", r"Multiple commands produce '.*Info\.plist'",
     "Info.plist is in Copy Bundle Resources (or a synchronized folder) and is also processed as the target's "
     "Info.plist. Remove it from target membership/resources (or add it as a membership exception)."),
    ("PLIST003", "info-plist", r"ITMS-90683|Missing purpose string in Info\.plist|"
     r"(?:Info\.plist|Info plist) must contain an? \w+UsageDescription",
     "Add the NS*UsageDescription key the upload names (or INFOPLIST_KEY_* when the plist is generated). "
     "Run `check_plist.py Info.plist --sources .`."),
    ("PLIST004", "info-plist", r"CFBundle(?:Short)?Version(?:String)? .*(?:must be|is invalid|must contain)|ITMS-9006[01]",
     "Version strings must be up to three period-separated integers; CFBundleVersion must increase per upload."),
    ("RES001", "resources", r"Multiple commands produce '",
     "Two files map to the same bundle path (duplicate names in different folders, or a file both copied and "
     "compiled). Rename one or remove the duplicate from Copy Bundle Resources."),
    ("RES002", "resources", r"Build input file cannot be found: '",
     "A file referenced by the project was moved or deleted. Re-add it or fix the path in the target."),
    ("RES003", "resources", r"None of the input catalogs contained a matching .*app icon set named",
     "ASSETCATALOG_COMPILER_APPICON_NAME names an icon set that does not exist in the target's asset catalog."),
    ("SCRIPT001", "script-phase", r"Sandbox: \S+\(\d+\) deny\(1\) file-(?:read|write)",
     "ENABLE_USER_SCRIPT_SANDBOXING blocked a Run Script phase. Declare its input/output files, or set the "
     "setting to NO for that target."),
    ("SCRIPT002", "script-phase", r"Command PhaseScriptExecution failed with a nonzero exit code",
     "A Run Script phase failed. The real error is in the script output just above this line."),
    ("POD001", "cocoapods", r"The sandbox is not in sync with the Podfile\.lock",
     "Run `pod install` and open the .xcworkspace (not the .xcodeproj)."),
    ("SPM001", "spm", r"(?:Could not resolve package dependencies|Failed to resolve dependencies|"
     r"Package resolution failed|package .* is using Swift tools version .* but the installed version)",
     "Resolve packages (File > Packages > Resolve, or `xcodebuild -resolvePackageDependencies`); check "
     "Package.resolved, network/auth for private repos and the tools version."),
    ("SPM002", "spm", r"Missing package product '",
     "A package product is linked but not resolved. Reset package caches or re-add the product to the target."),
    ("MOD001", "module", r"No such module '",
     "The module is not built for this target/SDK: check target membership/linking, resolve packages, open the "
     ".xcworkspace for CocoaPods, and make sure the scheme builds the dependency."),
    ("MOD002", "module", r"has a minimum deployment target of|was built for newer \w+ version",
     "A dependency targets a newer OS than your target. Raise IPHONEOS_DEPLOYMENT_TARGET or pin an older version."),
    ("MOD003", "module", r"Could not build (?:Objective-C )?module|module .* was built with|"
     r"has been modified since the module file|compiled module was created by a (?:newer|different)",
     "Stale module cache. Clean the build folder and delete this project's DerivedData directory."),
    ("LINK001", "linker", r"Undefined symbols? (?:for architecture|:)",
     "A symbol is declared but not linked: add the framework/library to Link Binary With Libraries, check target "
     "membership of the implementing file, and match architectures (simulator vs device slices)."),
    ("LINK002", "linker", r"duplicate symbols?",
     "The same symbol is linked twice (library linked statically into two targets, or -ObjC/-all_load pulling "
     "duplicates). Link it once."),
    ("LINK003", "linker", r"linker command failed with exit code",
     "Summary line; the actionable `ld:` errors are above it."),
    ("DEST001", "destination", r"Unable to find a destination matching|Unable to find a device matching|"
     r"Supported platforms for the buildables in the current scheme is empty",
     "The simulator/runtime is not installed or the name/OS is wrong. List with `xcrun simctl list devices` or "
     "`xcodebuild -showdestinations -scheme X`."),
    ("SCHEME001", "scheme", r"does not contain a scheme named|is not currently configured for the \w+ action|"
     r"Scheme .+ is not currently configured",
     "Schemes must be shared (Manage Schemes > Shared) to be visible to xcodebuild/CI; list them with "
     "`xcodebuild -list`."),
    ("DEP001", "dependencies", r"Cycle in dependencies between targets|Cycle inside",
     "Usually a Run Script or Embed phase ordered before the phases it depends on. Move 'Embed' and scripts "
     "with outputs after 'Copy Bundle Resources', or declare script inputs/outputs."),
    ("SWIFT001", "concurrency", r"is not concurrency-safe|non-[Ss]endable|risks causing data races|"
     r"actor-isolated .* (?:can not|cannot) be (?:referenced|mutated|called)|"
     r"(?:Call|call) to main actor-isolated|Main actor-isolated .* from a nonisolated|"
     r"Sending .* risks|global actor '.+'-isolated",
     "Swift 6 data-race diagnostic. See the swiftui-state-layout skill (references/swift6-concurrency.md) "
     "and run `concurrency_check.py`."),
    ("SWIFT003", "swiftui", r"invalid redeclaration of synthesized property '_|"
     r"ReferenceWritableKeyPath<.+, Published<",
     "A property wrapper such as @Published is used inside an @Observable class. Remove the wrapper; "
     "@Observable already tracks stored properties (swiftui_lint.py rule SUI005)."),
    ("SWIFT002", "swiftui", r"requires that '.+' conform to 'ObservableObject'|"
     r"(?:StateObject|ObservedObject|EnvironmentObject).* requires .* ObservableObject",
     "An @Observable class is used with @StateObject/@ObservedObject/@EnvironmentObject. Use @State, a plain "
     "property/@Bindable, or @Environment(Type.self) (swiftui_lint.py rule SUI004)."),
    ("EXPORT001", "export", r"exportArchive|error: exportArchive|IDEDistribution",
     "Export options do not match the archive (method/team/profile). Check ExportOptions.plist "
     "(method: app-store-connect / release-testing / debugging / enterprise) and signingStyle."),
]
_CATALOG = [(i, cat, re.compile(rx, re.I), hint) for i, cat, rx, hint in CATALOG]

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
DIAG = re.compile(r"(?P<file>(?:\.{0,2}/)?[^\s:\"|`'<>/][^:\"\n|]*?\.[A-Za-z][A-Za-z0-9+]*):(?P<line>\d+):(?:(?P<col>\d+):)?\s*"
                  r"(?P<sev>fatal error|error|warning):\s*(?P<msg>.+)")
GENERIC = re.compile(r"^(?:[\w./+-]+:\s+)?(?P<sev>fatal error|error|warning):\s*(?P<msg>.+)$")
# Any other "<location>: error: msg" line (macro expansions, "<unknown>:0"); not swiftc's ASCII-art echo lines.
LOOSE = re.compile(r"^(?P<loc>[^|`\s][^|`]*?):\s+(?P<sev>fatal error|error):\s*(?P<msg>.+)$")
BARE = re.compile(r"(?P<msg>Undefined symbols? for architecture \S+|duplicate symbols? .+|"
                  r"Sandbox: \S+\(\d+\) deny\(1\) file-\S+ .+|Command \w+ failed with a nonzero exit code|"
                  r"The sandbox is not in sync with the Podfile\.lock.*|linker command failed with exit code \d+.*|"
                  r"Supported platforms for the buildables in the current scheme is empty.*)")
STATUS = re.compile(r"\*\* ((?:BUILD|ARCHIVE|TEST|EXPORT|ANALYZE|CLEAN) (?:SUCCEEDED|FAILED|INTERRUPTED)) \*\*")
FAILED_HEADER = "The following build commands failed:"


def read_log(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    if not os.path.isfile(path):
        raise c.UsageError(f"no such log file: {path}")
    with open(path, "rb") as fh:
        head = fh.read(2)
    if head == b"\x1f\x8b":  # .xcactivitylog is gzip-compressed SLF; text survives decompression
        try:
            with gzip.open(path, "rb") as fh:
                data = fh.read(64 * 1024 * 1024)
        except OSError as exc:
            raise c.UsageError(f"cannot decompress {path}: {exc}") from exc
        return data.decode("utf-8", "replace").replace("\r", "\n")
    return c.read_text(path)


def classify(message: str) -> Optional[Dict[str, str]]:
    for rid, cat, rx, hint in _CATALOG:
        if rx.search(message):
            return {"id": rid, "category": cat, "hint": hint}
    return None


def parse(text: str, include_warnings: bool = False, max_items: int = 50) -> dict:
    items: Dict[tuple, dict] = {}
    failed_cmds: List[str] = []
    status = None
    warnings = 0
    in_failed = False
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = ANSI.sub("", raw).rstrip()
        sm = STATUS.search(line)
        if sm:
            status = sm.group(1)
        if FAILED_HEADER in line:
            in_failed = True
            continue
        if in_failed:
            stripped = line.strip()
            if not stripped or re.match(r"^\(\d+ failures?\)$", stripped):
                in_failed = False
            elif stripped not in failed_cmds:
                failed_cmds.append(stripped)
            continue

        entry = None
        m = DIAG.search(line)
        if m:
            entry = {"severity": "error" if "error" in m.group("sev") else "warning",
                     "message": m.group("msg").strip(), "file": m.group("file"),
                     "line": int(m.group("line"))}
            if m.group("col"):
                entry["column"] = int(m.group("col"))
        else:
            m = GENERIC.search(line.strip().lstrip("❌⚠️ ").strip())
            loose = None if m else LOOSE.match(line.strip())
            if m:
                entry = {"severity": "error" if "error" in m.group("sev") else "warning",
                         "message": m.group("msg").strip()}
            elif loose:
                entry = {"severity": "error", "message": loose.group("msg").strip(), "location": loose.group("loc")}
            else:
                m = BARE.search(line)
                if m:
                    entry = {"severity": "error", "message": m.group("msg").strip()}
        if not entry:
            continue
        if entry["severity"] == "warning":
            warnings += 1
            if not include_warnings:
                continue
        key = (entry["severity"], entry.get("file"), entry.get("line"), entry["message"])
        if key in items:
            items[key]["count"] += 1
            continue
        entry["log_line"] = lineno
        entry["count"] = 1
        cls = classify(entry["message"])
        if cls:
            entry.update(cls)
        items[key] = entry

    entries = list(items.values())
    errors = [e for e in entries if e["severity"] == "error"]
    # Summary lines (e.g. "linker command failed") are less useful than the errors they summarise.
    errors.sort(key=lambda e: (e.get("id") in ("LINK003", "SCRIPT002", "SIGN006"), e["log_line"]))
    shown_warnings = [e for e in entries if e["severity"] == "warning"]
    categories: Dict[str, int] = {}
    for e in errors:
        categories[e.get("category", "other")] = categories.get(e.get("category", "other"), 0) + 1
    if status is None:
        status = "FAILED (no status line)" if errors else "UNKNOWN (no status line found)"
    return {
        "status": status,
        "error_count": len(errors),
        "warning_count": warnings,
        "categories": categories,
        "errors": errors[:max_items],
        "warnings": shown_warnings[:max_items] if include_warnings else [],
        "failed_commands": failed_cmds,
        "truncated": len(errors) > max_items,
    }


def analyze(path: Optional[str] = None, text: Optional[str] = None, include_warnings: bool = False,
            max_items: int = 50) -> dict:
    if text is None:
        if not path:
            raise c.UsageError("pass a log path or text")
        text = read_log(path)
    result = parse(text, include_warnings, max_items)
    if path:
        result["source"] = path
    return result


def format_text(result: dict) -> str:
    out = [f"Build status: {result['status']}",
           f"Errors: {result['error_count']} unique   Warnings: {result['warning_count']}"
           + ("" if result["warnings"] else " (hidden; use --warnings)")]
    if result["categories"]:
        out.append("Categories: " + ", ".join(f"{k}={v}" for k, v in sorted(result["categories"].items())))
    for e in result["errors"] + result["warnings"]:
        loc = f"{e['file']}:{e['line']}: " if e.get("file") else (f"{e['location']}: " if e.get("location") else "")
        tag = f"[{e['category']}/{e['id']}] " if e.get("id") else ""
        times = f" (x{e['count']})" if e["count"] > 1 else ""
        out.append("")
        out.append(f"{loc}{e['severity']}: {tag}{e['message']}{times}")
        out.append(f"    log line {e['log_line']}")
        if e.get("hint"):
            out.append(f"    hint: {e['hint']}")
    if result["failed_commands"]:
        out.append("")
        out.append("Failed commands:")
        out.extend(f"    {cmd}" for cmd in result["failed_commands"])
    if result["truncated"]:
        out.append("\n(output truncated; raise --max)")
    return "\n".join(out)


@c.cli
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("logs", nargs="+", help="log file(s), .xcactivitylog, or - for stdin")
    p.add_argument("--warnings", action="store_true", help="include warnings in the report")
    p.add_argument("--max", type=int, default=50, help="max items per section (default: %(default)s)")
    p.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = p.parse_args(argv)
    results = [analyze(path=log, include_warnings=args.warnings, max_items=args.max) for log in args.logs]
    if args.json:
        c.print_json(results[0] if len(results) == 1 else results)
    else:
        print("\n\n".join((f"== {r['source']} ==\n" if len(results) > 1 else "") + format_text(r) for r in results))
    return 1 if any(r["error_count"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
