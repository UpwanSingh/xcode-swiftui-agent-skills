"""Shared test helpers: import skill scripts by path and build synthetic fixtures."""
from __future__ import annotations

import datetime as dt
import importlib.util
import os
import plistlib
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
APP = os.path.join(FIXTURES, "BuggyApp")


def load_script(skill: str, name: str):
    path = os.path.join(ROOT, "skills", skill, "scripts", f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"test_{skill.replace('-', '_')}_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def rules(result) -> list:
    return [f["rule"] for f in result["findings"]]


def fake_certificate(cn: str = "Apple Development: Jane Dev (XYZ9876543)", not_after: str = "300101000000Z") -> bytes:
    """Bytes shaped enough like a DER certificate for cert_info(): issuer CN, validity, subject OU/CN."""
    def tlv(tag: int, value: bytes) -> bytes:
        return bytes([tag, len(value)]) + value
    return (b"\x30\x82\x01\x00"
            + b"\x06\x03\x55\x04\x03" + tlv(0x0C, b"Apple Worldwide Developer Relations")
            + tlv(0x17, b"250101000000Z") + tlv(0x17, not_after.encode())
            + b"\x06\x03\x55\x04\x0b" + tlv(0x0C, b"ABCDE12345")
            + b"\x06\x03\x55\x04\x03" + tlv(0x0C, cn.encode()))


def write_profile(path: str, *, devices=True, get_task_allow=True, expires=None, entitlements=None,
                  app_id="ABCDE12345.com.example.*") -> str:
    """Write a CMS-wrapped plist that decodes like a real .mobileprovision."""
    ents = {"application-identifier": app_id, "com.apple.developer.team-identifier": "ABCDE12345",
            "keychain-access-groups": ["ABCDE12345.*"], "aps-environment": "development"}
    if get_task_allow:
        ents["get-task-allow"] = True
    ents.update(entitlements or {})
    profile = {
        "Name": "BuggyApp Dev", "UUID": "00000000-1111-2222-3333-444444444444", "TeamIdentifier": ["ABCDE12345"],
        "TeamName": "Example Team", "AppIDName": "Buggy", "Platform": ["iOS"],
        "CreationDate": dt.datetime(2026, 1, 1), "ExpirationDate": expires or dt.datetime(2099, 1, 1),
        "DeveloperCertificates": [fake_certificate()], "Entitlements": ents,
    }
    if devices:
        profile["ProvisionedDevices"] = ["00008110-000000000000001E"]
    with open(path, "wb") as fh:
        fh.write(b"\x30\x80\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x07\x02" + plistlib.dumps(profile) + b"\x00\x00")
    return path
