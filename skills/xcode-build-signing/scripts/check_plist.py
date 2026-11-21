#!/usr/bin/env python3
"""Validate an Info.plist: required keys, version formats, privacy usage strings, ATS, URL queries.

Understands Xcode's generated Info.plist: pass --project and keys set as
INFOPLIST_KEY_* build settings count as present. Pass --sources to detect APIs that
need a usage description (a missing one crashes the app the first time the API is used).

Examples:
  check_plist.py App/Info.plist --sources App/
  check_plist.py --project App.xcodeproj --sources App/       # GENERATE_INFOPLIST_FILE = YES
  check_plist.py build/Debug-iphonesimulator/App.app --built  # a built bundle
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Dict, List, Optional, Sequence, Set

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

RULES = {
    "PL001": ("error", "Info.plist cannot be parsed"),
    "PL002": ("error", "Required bundle key missing"),
    "PL003": ("error", "Unresolved or undefined $(VARIABLE) in a value"),
    "PL004": ("error", "API used without its privacy usage description (crash on first use)"),
    "PL005": ("warning", "Usage description is empty or a placeholder (App Review rejection)"),
    "PL006": ("warning", "App Transport Security allows arbitrary loads"),
    "PL007": ("warning", "canOpenURL scheme missing from LSApplicationQueriesSchemes"),
    "PL008": ("warning", "Malformed CFBundleVersion / CFBundleShortVersionString"),
    "PL009": ("warning", "No launch screen configured (app runs letterboxed)"),
    "PL010": ("warning", "INFOPLIST_KEY_* settings are ignored because GENERATE_INFOPLIST_FILE is not YES"),
}

REQUIRED = {
    "CFBundleIdentifier": "PRODUCT_BUNDLE_IDENTIFIER",
    "CFBundleExecutable": "EXECUTABLE_NAME",
    "CFBundleShortVersionString": "MARKETING_VERSION",
    "CFBundleVersion": "CURRENT_PROJECT_VERSION",
}

# (API regex, any-of required keys, human name). Regexes run on code with comments/strings removed.
PRIVACY_APIS = [
    (r"\brequestAlwaysAuthorization\s*\(", ["NSLocationAlwaysAndWhenInUseUsageDescription"], "Always location"),
    (r"\bCLLocationManager\b|\brequestWhenInUseAuthorization\s*\(|\bCLLocationUpdate\b|\bCLServiceSession\b",
     ["NSLocationWhenInUseUsageDescription"], "location"),
    (r"\bAVCaptureDevice\b|\bAVCaptureSession\b|sourceType\s*[:=]\s*\.camera\b|isSourceTypeAvailable\s*\(\s*\.camera\b|"
     r"\bDataScannerViewController\b",
     ["NSCameraUsageDescription"], "camera"),
    (r"\bAVAudioRecorder\b|\brequestRecordPermission\b|setCategory\s*\(\s*\.(?:playAndRecord|record)\b|\.inputNode\b",
     ["NSMicrophoneUsageDescription"], "microphone"),
    (r"\bPHPhotoLibrary\.requestAuthorization\s*\(\s*for:\s*\.addOnly|\bUIImageWriteToSavedPhotosAlbum\b|"
     r"\bcreationRequestForAsset\b", ["NSPhotoLibraryAddUsageDescription", "NSPhotoLibraryUsageDescription"],
     "saving to Photos"),
    (r"\bPHPhotoLibrary\.(?:requestAuthorization|authorizationStatus)\b(?!\s*\(\s*for:\s*\.addOnly)|\bPHAsset\.fetch",
     ["NSPhotoLibraryUsageDescription"], "photo library"),
    (r"\bCNContactStore\b", ["NSContactsUsageDescription"], "contacts"),
    (r"\bEKEventStore\b", ["NSCalendarsFullAccessUsageDescription", "NSCalendarsWriteOnlyAccessUsageDescription",
                           "NSCalendarsUsageDescription", "NSRemindersFullAccessUsageDescription",
                           "NSRemindersUsageDescription"], "calendars/reminders"),
    (r"\bCBCentralManager\b|\bCBPeripheralManager\b", ["NSBluetoothAlwaysUsageDescription"], "Bluetooth"),
    (r"\bSFSpeechRecognizer\b", ["NSSpeechRecognitionUsageDescription"], "speech recognition"),
    (r"\bHKHealthStore\b", ["NSHealthShareUsageDescription", "NSHealthUpdateUsageDescription"], "HealthKit"),
    (r"\.deviceOwnerAuthenticationWithBiometrics\b|\bbiometryType\b", ["NSFaceIDUsageDescription"], "Face ID"),
    (r"\bATTrackingManager\b", ["NSUserTrackingUsageDescription"], "App Tracking Transparency"),
    (r"\bNFC(?:NDEF|Tag)ReaderSession\b", ["NFCReaderUsageDescription"], "NFC"),
    (r"\bCMPedometer\b|\bCMMotionActivityManager\b", ["NSMotionUsageDescription"], "motion & fitness"),
    (r"\bNWBrowser\b|\bNetServiceBrowser\b|\bMCNearbyService(?:Browser|Advertiser)\b|\bNWListener\b",
     ["NSLocalNetworkUsageDescription"], "local network"),
    (r"\bMPMediaLibrary\b|\bMPMediaQuery\b", ["NSAppleMusicUsageDescription"], "media library"),
    (r"\bHMHomeManager\b", ["NSHomeKitUsageDescription"], "HomeKit"),
]
_PRIVACY = [(re.compile(rx), keys, name) for rx, keys, name in PRIVACY_APIS]

# Variables Xcode always defines while processing Info.plist.
XCODE_PROVIDED = {
    "PRODUCT_NAME", "EXECUTABLE_NAME", "PRODUCT_BUNDLE_PACKAGE_TYPE", "DEVELOPMENT_LANGUAGE", "PRODUCT_MODULE_NAME",
    "TARGET_NAME", "SRCROOT", "PROJECT_NAME", "BUILT_PRODUCTS_DIR", "CONFIGURATION", "PLATFORM_NAME", "SDK_NAME",
    "AppIdentifierPrefix", "TeamIdentifierPrefix", "DEVELOPMENT_TEAM",
}
VAR_RE = re.compile(r"\$[({]([A-Za-z_][A-Za-z0-9_]*)(?::[^)}]*)?[)}]")
VERSION_RE = re.compile(r"^\d+(\.\d+){0,2}$")
PLACEHOLDER_RE = re.compile(r"^\s*(?:todo|tbd|fixme|placeholder|lorem ipsum|description|usage description|\.+)?\s*$", re.I)


def _walk_strings(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def analyze(plist: Optional[str] = None, sources: Optional[Sequence[str]] = None, project: Optional[str] = None,
            built: bool = False) -> dict:
    if not plist and not project:
        raise c.UsageError("pass an Info.plist (or .app) and/or --project")
    findings: List[c.Finding] = []
    data: Dict = {}
    label = plist or "(generated Info.plist)"
    if plist:
        if plist.rstrip("/").endswith(".app") and os.path.isdir(plist):
            plist = os.path.join(plist, "Info.plist")
            label = plist
            built = True
        try:
            data = c.load_plist(plist)
        except c.UsageError as exc:
            return c.make_result([c.Finding("PL001", "error", str(exc), plist)], plist=plist)
        if not isinstance(data, dict):
            return c.make_result([c.Finding("PL001", "error", "top-level object is not a dictionary", plist)])

    settings: Dict[str, List[str]] = c.read_build_settings(project) if project else {}
    if project:
        # Settings often live in .xcconfig files (baseConfigurationReference); without them every
        # $(MARKETING_VERSION)-style reference would look undefined.
        for key, values in c.read_xcconfig_settings(c.project_dir(project)).items():
            bucket = settings.setdefault(key, [])
            bucket.extend(v for v in values if v not in bucket)
    generated = "YES" in c.setting_values(settings, "GENERATE_INFOPLIST_FILE")
    infoplist_keys: Set[str] = set()
    for k in settings:
        if k.startswith("INFOPLIST_KEY_"):
            key = k[len("INFOPLIST_KEY_"):].split("[", 1)[0]
            infoplist_keys.add("UILaunchScreen" if key == "UILaunchScreen_Generation" else key)
    if infoplist_keys and not generated:
        findings.append(c.Finding("PL010", "warning", f"{len(infoplist_keys)} INFOPLIST_KEY_* setting(s) (e.g. "
                                  f"{sorted(infoplist_keys)[0]}) have no effect: GENERATE_INFOPLIST_FILE is not YES",
                                  project, fix="Set GENERATE_INFOPLIST_FILE = YES (Xcode merges the keys into "
                                               "INFOPLIST_FILE) or move the keys into the Info.plist file."))
        infoplist_keys = set()
    keys = set(data) | infoplist_keys

    for key, setting in REQUIRED.items():
        if key in keys:
            continue
        if generated and (key == "CFBundleExecutable" or c.setting_values(settings, setting)):
            continue
        sev = "error" if built or not generated else "warning"
        findings.append(c.Finding("PL002", sev, f"{key} is missing", label,
                                  fix=f"Add {key} (usually $({setting})) or set {setting} with "
                                      "GENERATE_INFOPLIST_FILE = YES."))

    for path, value in _walk_strings(data):
        for var in VAR_RE.findall(value):
            if built:
                findings.append(c.Finding("PL003", "error", f"{path} = {value!r} was not expanded at build time",
                                          label, fix="Define the build setting for this target/configuration."))
            elif settings and var not in XCODE_PROVIDED and not c.setting_values(settings, var):
                findings.append(c.Finding("PL003", "warning", f"{path} uses $({var}) but no target defines {var}; "
                                          "it expands to an empty string", label,
                                          fix=f"Define {var} in build settings or replace the reference."))

    usage_values = {k: v for k, v in data.items() if isinstance(k, str) and k.endswith("UsageDescription")}
    for k in infoplist_keys:
        if k.endswith("UsageDescription"):
            vals = c.setting_values(settings, "INFOPLIST_KEY_" + k)
            usage_values.setdefault(k, vals[0] if vals else "")
    for k, v in sorted(usage_values.items()):
        if not isinstance(v, str) or PLACEHOLDER_RE.match(v) or VAR_RE.fullmatch(v.strip()):
            findings.append(c.Finding("PL005", "warning", f"{k} is empty or a placeholder ({v!r})", label,
                                      fix="Explain specifically why the app needs access; vague strings are "
                                          "rejected under guideline 5.1.1."))

    if sources:
        code_files = [c.SourceFile(p) for p in c.iter_files(sources, (".swift", ".m", ".mm"))]
        for rx, needed, name in _PRIVACY:
            if any(k in keys for k in needed):
                continue
            for sf in code_files:
                m = rx.search(sf.code)
                if m:
                    findings.append(c.Finding("PL004", "error", f"{name} API used but {' / '.join(needed[:2])} "
                                              "is missing (TCC crash on first access)", sf.path,
                                              sf.line_of(m.start()),
                                              f"Add {needed[0]} to Info.plist (or INFOPLIST_KEY_{needed[0]})."))
                    break
        queried = set(data.get("LSApplicationQueriesSchemes", []) or [])
        for sf in code_files:
            for m in re.finditer(r"canOpenURL\s*\(\s*URL\s*\(\s*string:\s*\"([A-Za-z][\w+.-]*):", sf.text):
                if sf.is_code(m.start()) and m.group(1).lower() not in {s.lower() for s in queried} \
                        and m.group(1).lower() not in ("http", "https", "tel", "mailto", "sms"):
                    findings.append(c.Finding("PL007", "warning", f"canOpenURL for '{m.group(1)}' always returns "
                                              "false: scheme not in LSApplicationQueriesSchemes", sf.path,
                                              sf.line_of(m.start()),
                                              f"Add '{m.group(1)}' to LSApplicationQueriesSchemes."))

    ats = data.get("NSAppTransportSecurity") or {}
    if isinstance(ats, dict) and ats.get("NSAllowsArbitraryLoads") is True:
        findings.append(c.Finding("PL006", "warning", "NSAllowsArbitraryLoads = YES disables ATS for all domains",
                                  label, fix="Use NSExceptionDomains for the specific hosts; App Review asks for "
                                             "justification otherwise."))

    for key in ("CFBundleVersion", "CFBundleShortVersionString"):
        value = data.get(key)
        if isinstance(value, str) and not VAR_RE.search(value) and not VERSION_RE.match(value.strip()):
            findings.append(c.Finding("PL008", "warning", f"{key} = {value!r} is not 1-3 period-separated integers",
                                      label, fix="Use e.g. 1.4.2 (marketing) and an increasing integer build number."))

    has_launch = any(k in keys for k in ("UILaunchScreen", "UILaunchStoryboardName", "UILaunchScreens",
                                         "UILaunchStoryboards"))
    is_app = data.get("CFBundlePackageType", "APPL") not in ("FMWK", "BNDL", "XPC!") and "NSExtension" not in data
    if not has_launch and is_app and (data or generated):
        findings.append(c.Finding("PL009", "warning", "No UILaunchScreen / UILaunchStoryboardName", label,
                                  fix="Add an empty UILaunchScreen dictionary (or "
                                      "INFOPLIST_KEY_UILaunchScreen_Generation = YES)."))

    return c.make_result(findings, plist=label, generated_info_plist=generated,
                         keys_present=sorted(keys))


@c.cli
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("plist", nargs="?", help="Info.plist path, or a built .app bundle")
    p.add_argument("--sources", nargs="+", metavar="DIR", help="Swift/ObjC sources to scan for privacy-gated APIs")
    p.add_argument("--project", metavar="XCODEPROJ", help=".xcodeproj or project.pbxproj for INFOPLIST_KEY_* settings")
    p.add_argument("--built", action="store_true", help="treat as a built product (unexpanded $(VARS) are errors)")
    p.add_argument("--list-rules", action="store_true", help="print rule ids and exit")
    c.add_output_args(p)
    args = p.parse_args(argv)
    if args.list_rules:
        c.print_rules(RULES)
        return 0
    result = analyze(args.plist, args.sources, args.project, args.built)
    if args.json:
        c.print_json(result)
    else:
        print(f"Checked {result.get('plist')}" + (" (GENERATE_INFOPLIST_FILE = YES)" if result.get("generated_info_plist") else ""))
        print(c.format_findings(result))
    return c.exit_status(result, args.fail_on)


if __name__ == "__main__":
    sys.exit(main())
