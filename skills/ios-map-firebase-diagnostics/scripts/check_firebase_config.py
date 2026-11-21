#!/usr/bin/env python3
"""Validate Firebase iOS setup: GoogleService-Info.plist, bundle ids, configure() order, URL schemes.

Catches the usual causes of "The default FirebaseApp instance must be configured",
"Default app has already been configured", Google/Phone sign-in redirect failures and
apps silently talking to the wrong Firebase project. API keys are masked in all output.

Examples:
  check_firebase_config.py --root .
  check_firebase_config.py --root . --bundle-id com.acme.app
  check_firebase_config.py --plist App/GoogleService-Info.plist --info-plist App/Info.plist --json
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
    "FB001": ("error", "No GoogleService-Info.plist found"),
    "FB002": ("warning", "Several GoogleService-Info plists – make sure the right one is bundled"),
    "FB003": ("error", "GoogleService-Info.plist is missing required keys"),
    "FB004": ("error", "BUNDLE_ID in GoogleService-Info.plist does not match the app"),
    "FB005": ("error", "GOOGLE_APP_ID is not an iOS app id (wrong platform file)"),
    "FB006": ("warning", "GOOGLE_APP_ID project number differs from GCM_SENDER_ID"),
    "FB007": ("error", "FirebaseApp.configure() missing or called more than once"),
    "FB008": ("error", "Firebase used before FirebaseApp.configure() runs"),
    "FB009": ("error", "Google Sign-In URL scheme (REVERSED_CLIENT_ID) not registered"),
    "FB010": ("warning", "Phone Auth reCAPTCHA URL scheme not registered"),
    "FB011": ("warning", "Emulator configured outside #if DEBUG"),
    "FB012": ("info", "FirebaseAppDelegateProxyEnabled = NO – APNs/URL handling must be forwarded manually"),
}
REQUIRED_KEYS = ["API_KEY", "GCM_SENDER_ID", "BUNDLE_ID", "PROJECT_ID", "GOOGLE_APP_ID"]
SERVICE_CALL = re.compile(r"\b(Firestore\.firestore|Auth\.auth|Storage\.storage|Database\.database|"
                          r"Functions\.functions|Messaging\.messaging|RemoteConfig\.remoteConfig|"
                          r"Analytics\.logEvent|Crashlytics\.crashlytics)\s*\(")
SKIP = c.SKIP_DIRS | {".xcodeproj", ".xcworkspace"}


def _find(root: str, predicate) -> List[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP and not d.startswith("."))
        out.extend(os.path.join(dirpath, f) for f in sorted(filenames) if predicate(f))
    return out


def _url_schemes(info: Dict) -> List[str]:
    schemes = []
    for t in info.get("CFBundleURLTypes", []) or []:
        schemes.extend(t.get("CFBundleURLSchemes", []) or [])
    return schemes


def _in_debug(sf: c.SourceFile, idx: int) -> bool:
    """True if `idx` sits inside an `#if DEBUG` (or `#if targetEnvironment(simulator)`) region."""
    stack: List[bool] = []
    for line in sf.code[:idx].splitlines():
        s = line.strip()
        if s.startswith("#if"):
            stack.append(bool(re.search(r"\bDEBUG\b|targetEnvironment\(\s*simulator\s*\)", s)))
        elif s.startswith("#else") and stack:
            stack[-1] = False
        elif s.startswith("#endif") and stack:
            stack.pop()
    return any(stack)


def analyze(root: Optional[str] = None, plist: Optional[str] = None, bundle_id: Optional[str] = None,
            info_plist: Optional[str] = None, sources: Optional[Sequence[str]] = None) -> dict:
    if not root and not plist:
        raise c.UsageError("pass --root or --plist")
    findings: List[c.Finding] = []
    root = root or os.path.dirname(os.path.abspath(plist))
    if not os.path.isdir(root):
        raise c.UsageError(f"not a directory: {root}")
    candidates = [plist] if plist else _find(root, lambda f: f.startswith("GoogleService-Info") and f.endswith(".plist"))
    swift = [c.SourceFile(p) for p in c.iter_files(sources or [root], (".swift",), SKIP)]
    uses_firebase = any(re.search(r"^\s*import\s+Firebase\w*", sf.code, re.M) for sf in swift)

    if not candidates:
        if uses_firebase:
            findings.append(c.Finding("FB001", "error", "Firebase is imported but no GoogleService-Info.plist exists",
                                      root, fix="Download it from Firebase console > Project settings > Your apps "
                                                "(iOS) and add it to the app target."))
        return c.make_result(findings, plists=[])
    if len(candidates) > 1:
        findings.append(c.Finding("FB002", "warning", "multiple Firebase config files: " +
                                  ", ".join(os.path.relpath(p, root) for p in candidates), root,
                                  fix="Only a file named exactly GoogleService-Info.plist at the bundle root is read "
                                      "by FirebaseApp.configure(). Per-environment files need a build phase that "
                                      "copies the right one, or FirebaseOptions(contentsOfFile:)."))

    info = {}
    if not info_plist:
        found = [p for p in _find(root, lambda f: f == "Info.plist") if "Tests" not in p]
        info_plist = found[0] if len(found) == 1 else None
    if info_plist:
        info = c.load_plist(info_plist)
    schemes = _url_schemes(info)

    app_bundle_ids: List[str] = [bundle_id] if bundle_id else []
    if not app_bundle_ids:
        for proj in c.find_xcodeprojs(root, max_depth=3):
            app_bundle_ids += [v for v in c.setting_values(c.read_build_settings(proj), "PRODUCT_BUNDLE_IDENTIFIER")
                               if "$(" not in v]
        bid = info.get("CFBundleIdentifier", "")
        if bid and "$(" not in bid:
            app_bundle_ids.append(bid)

    summaries = []
    for path in candidates:
        try:
            cfg = c.load_plist(path)
        except c.UsageError as exc:
            findings.append(c.Finding("FB003", "error", str(exc), path))
            continue
        missing = [k for k in REQUIRED_KEYS if not cfg.get(k)]
        if missing:
            findings.append(c.Finding("FB003", "error", f"missing keys: {', '.join(missing)}", path,
                                      fix="Re-download the plist for the iOS app from the Firebase console."))
        app_id = str(cfg.get("GOOGLE_APP_ID", ""))
        m = re.match(r"^1:(\d+):(\w+):[0-9a-f]+$", app_id)
        if app_id and (not m or m.group(2) != "ios"):
            findings.append(c.Finding("FB005", "error", f"GOOGLE_APP_ID {app_id!r} is not an iOS app id", path,
                                      fix="This file belongs to an Android/web app. Download the iOS config."))
        if m and cfg.get("GCM_SENDER_ID") and m.group(1) != str(cfg["GCM_SENDER_ID"]):
            findings.append(c.Finding("FB006", "warning", "GOOGLE_APP_ID and GCM_SENDER_ID belong to different "
                                      "projects", path, fix="Files from two projects were merged; re-download."))
        cfg_bundle = cfg.get("BUNDLE_ID")
        if cfg_bundle and app_bundle_ids and cfg_bundle not in app_bundle_ids:
            findings.append(c.Finding("FB004", "error", f"BUNDLE_ID {cfg_bundle} != app bundle id "
                                      f"{', '.join(sorted(set(app_bundle_ids)))}", path,
                                      fix="Register this bundle id as an iOS app in the Firebase project and download "
                                          "its plist (Auth, FCM and App Check reject mismatched ids)."))
        if any(re.search(r"\bGIDSignIn\b", sf.code) for sf in swift):
            reversed_id = cfg.get("REVERSED_CLIENT_ID")
            if not reversed_id:
                findings.append(c.Finding("FB009", "error", "GIDSignIn is used but the plist has no CLIENT_ID/"
                                          "REVERSED_CLIENT_ID", path,
                                          fix="Enable Google as a sign-in provider, then re-download the plist."))
            elif info and reversed_id not in schemes:
                findings.append(c.Finding("FB009", "error", "REVERSED_CLIENT_ID is not in CFBundleURLTypes; the "
                                          "Google sign-in redirect cannot return to the app", info_plist,
                                          fix=f"Add a URL type with scheme {reversed_id}."))
        if any(re.search(r"\bPhoneAuthProvider\b", sf.code) for sf in swift) and info:
            encoded = "app-" + app_id.replace(":", "-")
            if encoded not in schemes and cfg.get("REVERSED_CLIENT_ID") not in schemes:
                findings.append(c.Finding("FB010", "warning", "Phone Auth reCAPTCHA fallback needs a URL scheme",
                                          info_plist, fix=f"Add URL scheme {encoded} (Encoded App ID) to "
                                                          "CFBundleURLTypes."))
        summaries.append({"path": path, "project_id": cfg.get("PROJECT_ID"), "bundle_id": cfg_bundle,
                          "google_app_id": app_id, "storage_bucket": cfg.get("STORAGE_BUCKET"),
                          "api_key": c.mask(str(cfg.get("API_KEY", ""))) if cfg.get("API_KEY") else None})

    configure_calls = [(sf, m) for sf in swift for m in re.finditer(r"\bFirebaseApp\.configure\s*\(", sf.code)]
    if uses_firebase and not configure_calls:
        findings.append(c.Finding("FB007", "error", "Firebase is imported but FirebaseApp.configure() is never "
                                  "called", root, fix="Call it once at launch: in your App's init() or in "
                                                      "application(_:didFinishLaunchingWithOptions:)."))
    elif len(configure_calls) > 1:
        sf, m = configure_calls[1]
        findings.append(c.Finding("FB007", "error", f"FirebaseApp.configure() is called {len(configure_calls)} "
                                  "times ('Default app has already been configured' crash)", sf.path,
                                  sf.line_of(m.start()), fix="Keep a single call (guard with "
                                                              "`if FirebaseApp.app() == nil` for previews/tests)."))

    for sf in swift:
        app_decl = re.search(r"@main\s+struct\s+(\w+)\s*:\s*(?:\w+\s*,\s*)*App\b", sf.code)
        if not app_decl or not re.search(r"\bFirebaseApp\.configure", sf.code):
            continue
        brace = sf.code.find("{", app_decl.end())
        close = sf.match_close(brace)
        if close < 0:
            continue
        for m in re.finditer(r"^[ \t]*(?:@State\s+|@StateObject\s+)?(?:private\s+)?(?:let|var)\s+(\w+)[^=\n{]*=\s*"
                             r"([^\n]+)", sf.code[brace + 1:close], re.M):
            idx = brace + 1 + m.start()
            if sf.enclosing_open(idx) != brace:
                continue
            expr = m.group(2)
            if SERVICE_CALL.search(expr):
                findings.append(c.Finding("FB008", "error", f"stored property `{m.group(1)}` calls Firebase before "
                                          "init() runs FirebaseApp.configure()", sf.path, sf.line_of(idx),
                                          fix="Property initializers run before init's body. Create it inside init() "
                                              "after configure(), or make it lazy/computed."))
            elif "@StateObject" not in m.group(0) and re.match(r"[A-Z]\w*\s*\(", expr) and "@State" in m.group(0):
                findings.append(c.Finding("FB008", "warning", f"@State `{m.group(1)}` = {expr.strip()[:40]} is built "
                                          "before init() configures Firebase; if its initializer touches Firebase the "
                                          "app crashes at launch", sf.path, sf.line_of(idx),
                                          fix="Assign it in init() after FirebaseApp.configure() "
                                              "(`_model = State(initialValue: Model())`)."))
    for sf in swift:
        for m in re.finditer(r"\.useEmulator\s*\(", sf.code):
            if not _in_debug(sf, m.start()):
                findings.append(c.Finding("FB011", "warning", "useEmulator(...) is not inside #if DEBUG", sf.path,
                                          sf.line_of(m.start()), fix="Wrap emulator setup in #if DEBUG so release "
                                                                     "builds never point at localhost."))
    if info.get("FirebaseAppDelegateProxyEnabled") is False:
        findings.append(c.Finding("FB012", "info", "method swizzling disabled", info_plist,
                                  fix="Forward the APNs token (Messaging.messaging().apnsToken / "
                                      "Auth.auth().setAPNSToken) and open URLs (Auth.auth().canHandle) yourself."))
    return c.make_result(findings, plists=summaries, info_plist=info_plist,
                         app_bundle_ids=sorted(set(app_bundle_ids)), configure_calls=len(configure_calls))


@c.cli
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", help="project root to search (default: directory of --plist)")
    p.add_argument("--plist", help="explicit GoogleService-Info.plist")
    p.add_argument("--bundle-id", help="expected app bundle id (default: read from project/Info.plist)")
    p.add_argument("--info-plist", help="app Info.plist (default: auto-detect when unique)")
    p.add_argument("--sources", nargs="+", help="Swift sources to scan (default: --root)")
    p.add_argument("--list-rules", action="store_true", help="print rule ids and exit")
    c.add_output_args(p)
    args = p.parse_args(argv)
    if args.list_rules:
        c.print_rules(RULES)
        return 0
    result = analyze(args.root, args.plist, args.bundle_id, args.info_plist, args.sources)
    if args.json:
        c.print_json(result)
    else:
        for s in result.get("plists", []):
            print(f"{s['path']}: project={s['project_id']} bundle={s['bundle_id']} app={s['google_app_id']} "
                  f"api_key={s['api_key']}")
        print(c.format_findings(result))
    return c.exit_status(result, args.fail_on)


if __name__ == "__main__":
    sys.exit(main())
