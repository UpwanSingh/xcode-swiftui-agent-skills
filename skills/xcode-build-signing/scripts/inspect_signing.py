#!/usr/bin/env python3
"""Inspect provisioning profiles, entitlements and signing identities (read-only).

Subcommands:
  profile PATH              decode a .mobileprovision/.provisionprofile (team, app id, type,
                            expiry, devices, entitlements, certificate fingerprints)
  list [--bundle-id ID]     list installed profiles, optionally only those matching a bundle id
  compare --entitlements F  check every entitlement the app requests is granted by a profile
          (--profile P | --app App.app) [--bundle-id ID]
  identities                list code-signing identities in the keychain (names + SHA-1 only)
  rules                     print the SIG rule ids

Private keys and certificate bodies are never printed. Nothing is installed, deleted or re-signed.

Examples:
  inspect_signing.py profile ~/Downloads/App_Dev.mobileprovision --check-keychain
  inspect_signing.py list --bundle-id com.acme.app
  inspect_signing.py compare --entitlements App/App.entitlements --profile App_Dev.mobileprovision \\
      --bundle-id com.acme.app
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import os
import plistlib
import re
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

RULES = {
    "SIG001": ("error", "Provisioning profile expired (warning when it expires within 14 days)"),
    "SIG002": ("warning", "A certificate inside the profile has expired"),
    "SIG003": ("error", "No valid keychain identity matches any certificate in the profile"),
    "SIG004": ("error", "Bundle id does not match the profile's App ID"),
    "SIG005": ("error", "get-task-allow requested with a distribution profile"),
    "SIG006": ("error", "Entitlement requested but not granted by the profile (capability missing)"),
    "SIG007": ("error", "Entitlement value not allowed by the profile"),
}

PROFILE_DIRS = [
    "~/Library/Developer/Xcode/UserData/Provisioning Profiles",  # Xcode 16+
    "~/Library/MobileDevice/Provisioning Profiles",              # Xcode 15 and earlier
]
PROFILE_EXTS = (".mobileprovision", ".provisionprofile")

CAPABILITY = {
    "aps-environment": "Push Notifications",
    "com.apple.developer.associated-domains": "Associated Domains",
    "com.apple.security.application-groups": "App Groups",
    "com.apple.developer.icloud-container-identifiers": "iCloud (containers)",
    "com.apple.developer.icloud-services": "iCloud",
    "com.apple.developer.ubiquity-kvstore-identifier": "iCloud (key-value storage)",
    "com.apple.developer.applesignin": "Sign in with Apple",
    "com.apple.developer.healthkit": "HealthKit",
    "com.apple.developer.homekit": "HomeKit",
    "com.apple.developer.in-app-payments": "Apple Pay",
    "keychain-access-groups": "Keychain Sharing",
    "com.apple.developer.networking.wifi-info": "Access Wi-Fi Information",
    "com.apple.developer.nfc.readersession.formats": "NFC Tag Reading",
    "com.apple.developer.usernotifications.time-sensitive": "Time Sensitive Notifications",
    "com.apple.developer.siri": "Siri",
    "com.apple.developer.game-center": "Game Center",
    "com.apple.developer.weatherkit": "WeatherKit",
    "com.apple.developer.default-data-protection": "Data Protection",
    "com.apple.developer.family-controls": "Family Controls",
    "com.apple.developer.associated-appclip-app-identifiers": "App Clips",
}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aware(value: Any) -> Optional[dt.datetime]:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    return None


def profile_type(p: Dict[str, Any]) -> str:
    ents = p.get("Entitlements", {})
    if p.get("ProvisionsAllDevices"):
        return "enterprise"
    if p.get("ProvisionedDevices"):
        return "development" if ents.get("get-task-allow") else "ad-hoc"
    return "app-store"


def decode_profile(path: str) -> Dict[str, Any]:
    p = c.load_plist(path)
    if not isinstance(p, dict) or "Entitlements" not in p:
        raise c.UsageError(f"{path} does not look like a provisioning profile")
    ents = p.get("Entitlements", {})
    app_id = ents.get("application-identifier") or ents.get("com.apple.application-identifier", "")
    team = (p.get("TeamIdentifier") or [""])[0]
    expires = _aware(p.get("ExpirationDate"))
    certs = [c.cert_info(bytes(d)) for d in p.get("DeveloperCertificates", [])]
    return {
        "path": path,
        "name": p.get("Name"),
        "uuid": p.get("UUID"),
        "team_id": team,
        "team_name": p.get("TeamName"),
        "app_id_name": p.get("AppIDName"),
        "application_identifier": app_id,
        "bundle_id_pattern": app_id.split(".", 1)[1] if "." in app_id else app_id,
        "type": profile_type(p),
        "platforms": p.get("Platform", []),
        "created": _aware(p.get("CreationDate")).isoformat() if _aware(p.get("CreationDate")) else None,
        "expires": expires.isoformat() if expires else None,
        "expired": bool(expires and expires < _now()),
        "days_left": (expires - _now()).days if expires else None,
        "device_count": len(p.get("ProvisionedDevices", []) or []),
        "devices": list(p.get("ProvisionedDevices", []) or []),
        "entitlements": c.to_jsonable(ents),
        "certificates": certs,
    }


def bundle_matches(pattern: str, bundle_id: str) -> bool:
    return fnmatch.fnmatchcase(bundle_id, pattern)


def list_identities() -> List[Dict[str, str]]:
    code, out, err = c.run_command(["security", "find-identity", "-p", "codesigning"], timeout=30)
    if code != 0:
        raise c.UsageError(f"security find-identity failed: {err.strip() or out.strip()}")
    found: Dict[str, Dict[str, str]] = {}
    for m in re.finditer(r"^\s*\d+\)\s+([0-9A-F]{40})\s+\"(.+?)\"(?:\s+\((.+)\))?\s*$", out, re.M):
        sha, name, status = m.group(1), m.group(2), m.group(3) or "valid"
        if sha not in found or status != "valid":
            found[sha] = {"sha1": sha, "name": name, "status": status}
    return list(found.values())


def inspect_profile(path: str, check_keychain: bool = False) -> Dict[str, Any]:
    info = decode_profile(path)
    findings: List[c.Finding] = []
    if info["expired"]:
        findings.append(c.Finding("SIG001", "error", f"profile expired on {info['expires']}", path,
                                  fix="Regenerate it in the developer portal or let automatic signing refresh it."))
    elif info["days_left"] is not None and info["days_left"] < 14:
        findings.append(c.Finding("SIG001", "warning", f"profile expires in {info['days_left']} day(s)", path))
    for cert in info["certificates"]:
        if cert.get("expired"):
            findings.append(c.Finding("SIG002", RULES["SIG002"][0], f"certificate {cert.get('subject_cn', cert['sha1'])} in "
                                      "this profile has expired", path))
    if check_keychain:
        local = {i["sha1"]: i for i in list_identities()}
        usable = [cert for cert in info["certificates"] if local.get(cert["sha1"], {}).get("status") == "valid"]
        info["keychain_matches"] = [local[cert["sha1"]] for cert in info["certificates"] if cert["sha1"] in local]
        if not usable:
            findings.append(c.Finding("SIG003", RULES["SIG003"][0], "none of the profile's certificates has a valid identity "
                                      "(certificate + private key) in this keychain", path,
                                      fix="Install the matching .p12, or regenerate the profile to include a "
                                          "certificate you hold (Xcode error: \"doesn't include signing certificate\")."))
    return c.make_result(findings, profile=info)


def list_profiles(bundle_id: Optional[str] = None, dirs: Optional[List[str]] = None) -> Dict[str, Any]:
    profiles, errors = [], []
    for d in dirs or PROFILE_DIRS:
        d = os.path.expanduser(d)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(PROFILE_EXTS):
                continue
            try:
                info = decode_profile(os.path.join(d, name))
            except c.UsageError as exc:
                errors.append(str(exc))
                continue
            if bundle_id and not bundle_matches(info["bundle_id_pattern"], bundle_id):
                continue
            info.pop("devices", None)
            info.pop("entitlements", None)
            profiles.append(info)
    profiles.sort(key=lambda i: (i["expired"], i["name"] or ""))
    return {"profiles": profiles, "count": len(profiles), "searched": [os.path.expanduser(d) for d in dirs or PROFILE_DIRS],
            "errors": errors}


def _substitute(value: Any, team: str, bundle_id: Optional[str]) -> Any:
    if isinstance(value, list):
        return [_substitute(v, team, bundle_id) for v in value]
    if not isinstance(value, str):
        return value
    value = value.replace("$(AppIdentifierPrefix)", f"{team}.").replace("$(TeamIdentifierPrefix)", f"{team}.")
    value = value.replace("$(DEVELOPMENT_TEAM)", team)
    if bundle_id:
        value = value.replace("$(PRODUCT_BUNDLE_IDENTIFIER)", bundle_id).replace("$(CFBundleIdentifier)", bundle_id)
    return re.sub(r"\$\([A-Za-z_]\w*\)", "*", value)


def _granted(requested: Any, granted: Any) -> bool:
    if isinstance(granted, str) and granted == "*":
        return True
    if isinstance(requested, bool):
        return (not requested) or granted is True
    if isinstance(requested, str):
        options = granted if isinstance(granted, list) else [granted]
        # A requested value with an unexpanded $(VAR) became "*" in _substitute; match it as a
        # pattern against the granted values too (iCloud.$(CFBundleIdentifier) vs iCloud.com.acme.app).
        return any(isinstance(g, str) and (g == "*" or fnmatch.fnmatchcase(requested, g)
                                           or ("*" in requested and fnmatch.fnmatchcase(g, requested)))
                   for g in options)
    if isinstance(requested, list):
        return all(_granted(r, granted) for r in requested)
    return requested == granted


def compare(entitlements: str, profile: Optional[str] = None, app: Optional[str] = None,
            bundle_id: Optional[str] = None) -> Dict[str, Any]:
    if bool(profile) == bool(app):
        raise c.UsageError("pass exactly one of --profile or --app")
    if entitlements == "-signed-" and not app:
        raise c.UsageError("--entitlements -signed- needs --app")
    if app:
        profile = os.path.join(app, "embedded.mobileprovision")
        if not os.path.isfile(profile):
            raise c.UsageError(f"{app} has no embedded.mobileprovision (simulator builds are not provisioned)")
        if not bundle_id:
            info_plist = os.path.join(app, "Info.plist")
            if os.path.isfile(info_plist):
                bundle_id = c.load_plist(info_plist).get("CFBundleIdentifier")
    prof = decode_profile(profile)
    team = prof["team_id"]
    if entitlements == "-signed-":
        code, out, err = c.run_command(["codesign", "-d", "--entitlements", "-", "--xml", app], timeout=30)
        if code != 0 or "<plist" not in out:
            raise c.UsageError(f"codesign could not read entitlements: {err.strip()}")
        requested = plistlib.loads(out[out.find("<?xml"):].encode())
        source = f"{app} (signed)"
    else:
        requested = c.load_plist(entitlements)
        source = entitlements
    granted = prof["entitlements"]
    findings: List[c.Finding] = []
    if prof["expired"]:
        findings.append(c.Finding("SIG001", "error", f"profile {prof['name']} expired on {prof['expires']}", profile))
    if bundle_id and not bundle_matches(prof["bundle_id_pattern"], bundle_id):
        findings.append(c.Finding("SIG004", RULES["SIG004"][0], f"bundle id {bundle_id} does not match the profile's App ID "
                                  f"{prof['application_identifier']}", profile,
                                  fix="Use a profile created for this bundle id (or a matching wildcard)."))
    for key, value in requested.items():
        value = _substitute(value, team, bundle_id)
        capability = CAPABILITY.get(key, key)
        if key == "get-task-allow":
            if value is True and prof["type"] in ("app-store", "ad-hoc", "enterprise"):
                findings.append(c.Finding("SIG005", RULES["SIG005"][0], f"get-task-allow = true with a {prof['type']} profile",
                                          source, fix="Remove get-task-allow from the entitlements file; Xcode adds "
                                                      "it for development signing only."))
            continue
        if key not in granted:
            findings.append(c.Finding("SIG006", RULES["SIG006"][0], f"{key} ({capability}) is requested but the profile does "
                                      "not grant it", source,
                                      fix=f"Enable {capability} for the App ID and regenerate the profile, or remove "
                                          "the entitlement."))
        elif not _granted(value, granted[key]):
            hint = ""
            if key == "aps-environment":
                hint = ("Xcode rewrites aps-environment for the profile type; a mismatch usually means manual "
                        "signing with the wrong (development vs distribution) profile.")
            findings.append(c.Finding("SIG007", RULES["SIG007"][0], f"{key}: requested {value!r} but profile grants "
                                      f"{granted[key]!r}", source, fix=hint or "Align the entitlements file with "
                                      "the identifiers registered for the App ID, then regenerate the profile."))
    prof.pop("devices", None)
    return c.make_result(findings, profile=prof, entitlements_source=source, requested=c.to_jsonable(requested),
                         bundle_id=bundle_id)


def _print_profile(info: Dict[str, Any]) -> None:
    status = "EXPIRED" if info["expired"] else f"{info['days_left']} days left"
    print(f"Name:        {info['name']}\nUUID:        {info['uuid']}\nTeam:        {info['team_id']} ({info['team_name']})")
    print(f"App ID:      {info['application_identifier']}  [{info['app_id_name']}]")
    print(f"Type:        {info['type']}   Devices: {info['device_count']}   Platforms: {', '.join(info['platforms'])}")
    print(f"Expires:     {info['expires']} ({status})")
    print("Certificates:")
    for cert in info["certificates"]:
        print(f"  {cert['sha1']}  {cert.get('subject_cn', '?')}  until {cert.get('not_after', '?')}")
    print("Entitlements:")
    for k, v in info["entitlements"].items():
        print(f"  {k} = {v}")


@c.cli
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("profile", help="decode one provisioning profile")
    sp.add_argument("path")
    sp.add_argument("--check-keychain", action="store_true", help="also verify a matching identity is installed")
    sl = sub.add_parser("list", help="list installed provisioning profiles")
    sl.add_argument("--bundle-id", help="only profiles whose App ID matches this bundle id")
    sl.add_argument("--dir", action="append", help="profile directory (repeatable; default: Xcode's locations)")
    sc = sub.add_parser("compare", help="check entitlements against a profile")
    sc.add_argument("--entitlements", required=True,
                    help="path to .entitlements, or -signed- to read them from the signed --app")
    sc.add_argument("--profile")
    sc.add_argument("--app", help="built .app bundle (uses its embedded.mobileprovision)")
    sc.add_argument("--bundle-id")
    sub.add_parser("identities", help="list code-signing identities in the keychain")
    sub.add_parser("rules", help="print rule ids and exit")
    for s in (sp, sl, sc):
        s.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = p.parse_args(argv)

    if args.cmd == "rules":
        c.print_rules(RULES)
        return 0
    if args.cmd == "identities":
        ids = list_identities()
        for i in ids:
            print(f"{i['sha1']}  {i['name']}  [{i['status']}]")
        print(f"{len(ids)} identit{'y' if len(ids) == 1 else 'ies'}")
        return 0
    if args.cmd == "list":
        result = list_profiles(args.bundle_id, args.dir)
        if args.json:
            c.print_json(result)
        else:
            for i in result["profiles"]:
                flag = "EXPIRED " if i["expired"] else ""
                print(f"{flag}{i['name']}  [{i['type']}]  {i['application_identifier']}  expires {i['expires']}  {i['uuid']}")
            print(f"{result['count']} profile(s) in: {', '.join(result['searched'])}")
        return 0
    if args.cmd == "profile":
        result = inspect_profile(args.path, args.check_keychain)
        if args.json:
            c.print_json(result)
        else:
            _print_profile(result["profile"])
            print(c.format_findings(result))
        return c.exit_status(result, "error")
    result = compare(args.entitlements, args.profile, args.app, args.bundle_id)
    if args.json:
        c.print_json(result)
    else:
        print(f"Profile: {result['profile']['name']} ({result['profile']['type']}, {result['profile']['application_identifier']})")
        print(f"Entitlements: {result['entitlements_source']}")
        print(c.format_findings(result))
    return c.exit_status(result, "error")


if __name__ == "__main__":
    sys.exit(main())
