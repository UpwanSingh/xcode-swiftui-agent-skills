#!/usr/bin/env python3
"""Read-only facts about the local Xcode toolchain and projects.

Subcommands:
  env                         macOS / Xcode / Swift versions and the active developer dir
  projects [--root DIR]       find .xcodeproj, .xcworkspace, Package.swift, Info.plist, entitlements, ...
  schemes PATH                `xcodebuild -list -json` for a .xcodeproj/.xcworkspace
  settings PATH [--scheme S]  signing/bundle/Info.plist/Swift build settings (`-showBuildSettings`)
  logs [--project NAME]       newest .xcactivitylog files in DerivedData (--kind Build by default)

Only allow-listed commands are run (see _common.ALLOWED_COMMANDS); nothing is built.

Examples:
  xcode_env.py env
  xcode_env.py settings App.xcodeproj --scheme App --configuration Release
  xcode_env.py logs --project App --limit 3
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

RELEVANT_SETTINGS = [
    "PRODUCT_BUNDLE_IDENTIFIER", "PRODUCT_NAME", "WRAPPER_NAME", "EXECUTABLE_NAME", "DEVELOPMENT_TEAM",
    "CODE_SIGN_STYLE", "CODE_SIGN_IDENTITY", "PROVISIONING_PROFILE_SPECIFIER", "CODE_SIGN_ENTITLEMENTS",
    "INFOPLIST_FILE", "GENERATE_INFOPLIST_FILE", "MARKETING_VERSION", "CURRENT_PROJECT_VERSION",
    "IPHONEOS_DEPLOYMENT_TARGET", "SDKROOT", "SUPPORTED_PLATFORMS", "TARGETED_DEVICE_FAMILY", "ARCHS",
    "SWIFT_VERSION", "SWIFT_STRICT_CONCURRENCY", "SWIFT_DEFAULT_ACTOR_ISOLATION", "SWIFT_APPROACHABLE_CONCURRENCY",
    "SWIFT_ACTIVE_COMPILATION_CONDITIONS", "OTHER_SWIFT_FLAGS", "ENABLE_USER_SCRIPT_SANDBOXING",
    "BUILT_PRODUCTS_DIR", "CONTENTS_FOLDER_PATH", "UNLOCALIZED_RESOURCES_FOLDER_PATH", "MACH_O_TYPE",
    "LD_RUNPATH_SEARCH_PATHS", "FRAMEWORK_SEARCH_PATHS", "BUILD_LIBRARY_FOR_DISTRIBUTION",
    "ASSETCATALOG_COMPILER_APPICON_NAME",
]
NOTABLE = {
    "GoogleService-Info.plist", "Info.plist", "Package.swift", "Podfile", "Cartfile", "firestore.rules",
    "storage.rules", "firebase.json", "ExportOptions.plist", "PrivacyInfo.xcprivacy",
}
DERIVED_DATA = "~/Library/Developer/Xcode/DerivedData"


def _first_line(cmd: List[str]) -> Dict[str, Any]:
    try:
        code, out, err = c.run_command(cmd, timeout=20)
    except c.UsageError as exc:
        return {"error": str(exc)}
    text = (out or err).strip()
    return {"output": text} if code == 0 else {"error": text.splitlines()[0] if text else f"exit {code}"}


def env() -> Dict[str, Any]:
    info = {
        "macos": _first_line(["sw_vers"]),
        "developer_dir": _first_line(["xcode-select", "-p"]),
        "xcode": _first_line(["xcodebuild", "-version"]),
        "swift": _first_line(["swift", "--version"]),
        "DEVELOPER_DIR_env": os.environ.get("DEVELOPER_DIR"),
    }
    xcode_err = info["xcode"].get("error", "")
    if "license" in xcode_err.lower():
        info["hint"] = "Accept the Xcode license once: sudo xcodebuild -license accept"
    elif "CommandLineTools" in info["developer_dir"].get("output", ""):
        info["hint"] = ("Active developer dir is Command Line Tools; xcodebuild/simctl need full Xcode: "
                        "sudo xcode-select -s /Applications/Xcode.app/Contents/Developer")
    return info


def projects(root: str = ".", max_depth: int = 5) -> Dict[str, Any]:
    if not os.path.isdir(root):
        raise c.UsageError(f"not a directory: {root}")
    found: Dict[str, List[str]] = {"xcodeproj": [], "xcworkspace": [], "entitlements": [], "files": []}
    base = os.path.abspath(root).rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in c.walk(root, skip_dirs=None, skip_hidden=False):
        depth = os.path.abspath(dirpath).count(os.sep) - base
        keep = []
        for d in sorted(dirnames):
            full = os.path.join(dirpath, d)
            if d.endswith(".xcodeproj"):
                found["xcodeproj"].append(full)
            elif d.endswith(".xcworkspace") and not dirpath.endswith(".xcodeproj"):
                found["xcworkspace"].append(full)
            elif d not in c.SKIP_DIRS and not d.startswith(".") and depth < max_depth:
                keep.append(d)
        dirnames[:] = keep
        for f in sorted(filenames):
            if f.endswith(".entitlements"):
                found["entitlements"].append(os.path.join(dirpath, f))
            elif f in NOTABLE or f.startswith("GoogleService-Info"):
                found["files"].append(os.path.join(dirpath, f))
    return found


def _container_flag(path: str) -> List[str]:
    path = path.rstrip("/")
    if path.endswith(".xcworkspace"):
        return ["-workspace", path]
    if path.endswith(".xcodeproj"):
        return ["-project", path]
    raise c.UsageError("expected a .xcodeproj or .xcworkspace path")


def _json_from(out: str, opener: str, what: str) -> Any:
    start = out.find(opener)
    try:
        return json.loads(out[start:]) if start >= 0 else json.loads(out)
    except ValueError as exc:
        raise c.UsageError(f"{what} printed no parseable JSON ({exc}); first output: {out.strip()[:300]!r}") from exc


def _xcodebuild_error(what: str, out: str, err: str) -> c.UsageError:
    text = (err or out).strip()
    if re.search(r"resolved file is required|Package\.resolved|automatic package resolution", text, re.I):
        return c.UsageError(f"{what} needs Swift packages resolved first. This tool never resolves packages "
                            "(that would fetch code and write Package.resolved); open the project in Xcode once "
                            "or run `xcodebuild -resolvePackageDependencies` yourself, then retry.")
    return c.UsageError(f"{what} failed: {text[:800]}")


def schemes(path: str) -> Dict[str, Any]:
    # -disableAutomaticPackageResolution: listing schemes must not fetch packages, run their
    # manifests or write Package.resolved into the user's project.
    cmd = ["xcodebuild", "-list", "-json", "-disableAutomaticPackageResolution"] + _container_flag(path)
    code, out, err = c.run_command(cmd, timeout=90)
    if code != 0:
        raise _xcodebuild_error("xcodebuild -list", out, err)
    return _json_from(out, "{", "xcodebuild -list")


def settings(path: str, scheme: Optional[str] = None, configuration: Optional[str] = None,
             target: Optional[str] = None, keys: Optional[List[str]] = None, all_keys: bool = False) -> Dict[str, Any]:
    cmd = ["xcodebuild", "-showBuildSettings", "-json", "-disableAutomaticPackageResolution"] + _container_flag(path)
    if scheme:
        cmd += ["-scheme", scheme]
    if target:
        cmd += ["-target", target]
    if configuration:
        cmd += ["-configuration", configuration]
    if path.rstrip("/").endswith(".xcworkspace") and not scheme:
        raise c.UsageError("a workspace needs --scheme")
    code, out, err = c.run_command(cmd, timeout=180)
    if code != 0:
        raise _xcodebuild_error("xcodebuild -showBuildSettings", out, err)
    data = _json_from(out, "[", "xcodebuild -showBuildSettings")
    wanted = None if all_keys else set(keys or RELEVANT_SETTINGS)
    targets = []
    for entry in data:
        bs = entry.get("buildSettings", {})
        targets.append({
            "target": entry.get("target"),
            "action": entry.get("action"),
            "settings": {k: v for k, v in sorted(bs.items()) if wanted is None or k in wanted},
        })
    return {"targets": targets, "filtered": wanted is not None}


LOG_KINDS = ("Build", "Test", "Package", "Launch", "Localization", "all")


def logs(project: Optional[str] = None, limit: int = 5, derived_data: str = DERIVED_DATA,
         kind: str = "Build") -> Dict[str, Any]:
    """Newest .xcactivitylog files. `kind` defaults to Build: Package/Test logs fed to
    extract_build_errors look like 'UNKNOWN, 0 errors' and would hide the real build failure."""
    if project and not re.fullmatch(r"[\w .+-]+", project):
        raise c.UsageError("project must be a plain project name (letters, digits, space, . + - _)")
    if kind not in LOG_KINDS:
        raise c.UsageError(f"kind must be one of {', '.join(LOG_KINDS)}")
    root = os.path.expanduser(derived_data)
    pattern = os.path.join(root, f"{glob.escape(project)}-*" if project else "*", "Logs",
                           "*" if kind == "all" else kind, "*.xcactivitylog")
    found = [f for f in glob.glob(pattern) if c.path_allowed(f)]
    files = sorted(found, key=os.path.getmtime, reverse=True)[:max(1, limit)]
    return {
        "derived_data": root,
        "kind": kind,
        "logs": [{"path": f, "kind": os.path.basename(os.path.dirname(f)), "modified": os.path.getmtime(f),
                  "size": os.path.getsize(f)} for f in files],
        "hint": None if files else "No logs found. Build once in Xcode, or capture CLI output with "
                                   "`xcodebuild ... 2>&1 | tee build.log`.",
    }


@c.cli
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("env", help="toolchain versions")
    pp = sub.add_parser("projects", help="find project files")
    pp.add_argument("--root", default=".")
    pp.add_argument("--max-depth", type=int, default=5)
    ps = sub.add_parser("schemes", help="list schemes/targets/configurations")
    ps.add_argument("path")
    pb = sub.add_parser("settings", help="show relevant build settings")
    pb.add_argument("path")
    pb.add_argument("--scheme")
    pb.add_argument("--target")
    pb.add_argument("--configuration")
    pb.add_argument("--key", action="append", dest="keys", help="setting to show (repeatable)")
    pb.add_argument("--all", action="store_true", help="show every build setting")
    pl = sub.add_parser("logs", help="list recent .xcactivitylog files")
    pl.add_argument("--project", help="DerivedData folder prefix (usually the project name)")
    pl.add_argument("--limit", type=int, default=5)
    pl.add_argument("--derived-data", default=DERIVED_DATA)
    pl.add_argument("--kind", default="Build", choices=LOG_KINDS, help="log folder (default: %(default)s)")
    args = p.parse_args(argv)

    if args.cmd == "env":
        result = env()
    elif args.cmd == "projects":
        result = projects(args.root, args.max_depth)
    elif args.cmd == "schemes":
        result = schemes(args.path)
    elif args.cmd == "settings":
        result = settings(args.path, args.scheme, args.configuration, args.target, args.keys, args.all)
    else:
        result = logs(args.project, args.limit, args.derived_data, args.kind)
    c.print_json(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
