#!/usr/bin/env python3
"""Find bundle/resource lookups that will return nil at runtime.

Scans Swift for literal resource lookups (Bundle.url/path(forResource:), UIImage(named:),
Image("..."), Color("..."), NSDataAsset, custom fonts) and checks that:
  * the file / asset-catalog entry exists in the source tree,
  * it is actually inside a built .app (with --app), at the expected sub-path,
  * Swift packages use Bundle.module (not Bundle.main) and declare resources.

Examples:
  check_bundle_resources.py --sources App/
  check_bundle_resources.py --sources App/ --app ~/Library/Developer/Xcode/DerivedData/App-*/Build/Products/Debug-iphonesimulator/App.app
  check_bundle_resources.py --sources Packages/Feature --json
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
    "BR001": ("error", "Resource referenced in code does not exist in the source tree"),
    "BR002": ("error", "Resource exists in sources but is missing from the built .app"),
    "BR003": ("error", "Resource is in the .app under a sub-folder the lookup does not pass"),
    "BR004": ("warning", "Bundle.main used inside a Swift package (resources live in Bundle.module)"),
    "BR005": ("warning", "Bundle.module used but Package.swift declares no resources"),
    "BR006": ("warning", "Custom font used but UIAppFonts is not set"),
    "BR007": ("error", "Asset-catalog name not found in any .xcassets"),
}

FILE_LOOKUP = re.compile(
    r"\b(?:url|path)\s*\(\s*forResource:\s*\"([^\"]+)\"\s*,\s*(?:withExtension|ofType):\s*(?:\"([^\"]*)\"|nil)"
    r"(?:\s*,\s*(?:subdirectory|inDirectory):\s*\"([^\"]*)\")?")
ASSET_LOOKUP = re.compile(
    r"\b(?:UIImage\s*\(\s*named:|NSImage\s*\(\s*named:|Image\s*\(\s*(?!systemName)|UIColor\s*\(\s*named:|"
    r"Color\s*\(\s*(?!red|white|hue|uiColor|cgColor|\.)|NSDataAsset\s*\(\s*name:)\s*\"([^\"\\]+)\"")
FONT_USE = re.compile(r"\bUIFont\s*\(\s*name:\s*\"|\bFont\.custom\s*\(\s*\"|\.custom\s*\(\s*\"")
ASSET_DIRS = (".imageset", ".colorset", ".dataset", ".appiconset", ".symbolset", ".imagestack")
SOURCE_SKIP = c.SKIP_DIRS | {".xcodeproj", ".xcworkspace"}


def index_tree(roots: Sequence[str]):
    """Return (files by basename -> [relpaths], asset names)."""
    files: Dict[str, List[str]] = {}
    assets: Set[str] = set()
    for root in roots:
        if os.path.isfile(root):
            root = os.path.dirname(root) or "."
        for dirpath, dirnames, filenames in os.walk(root):
            keep = []
            for d in dirnames:
                if d.endswith(ASSET_DIRS):
                    assets.add(os.path.splitext(d)[0])
                    # namespaced folders ("Icons/back") are addressed with their path prefix
                    rel = os.path.relpath(os.path.join(dirpath, d), root)
                    parts = rel.split(os.sep)
                    for i, part in enumerate(parts):
                        if part.endswith(".xcassets"):
                            assets.add("/".join(parts[i + 1:])[: -len(os.path.splitext(d)[1])])
                elif d not in SOURCE_SKIP and not d.startswith("."):
                    keep.append(d)
                    if d.endswith((".bundle",)):
                        files.setdefault(d, []).append(os.path.join(dirpath, d))
            dirnames[:] = keep
            for f in filenames:
                files.setdefault(f, []).append(os.path.join(dirpath, f))
                stem, ext = os.path.splitext(f)
                if ext.lower() in (".png", ".jpg", ".jpeg", ".pdf", ".heic", ".gif", ".svg"):
                    assets.add(re.sub(r"@[23]x$", "", stem))
    return files, assets


def index_app(app: str) -> Set[str]:
    rels = set()
    for dirpath, dirnames, filenames in os.walk(app):
        for name in filenames + [d for d in dirnames if d.endswith(".bundle")]:
            rels.add(os.path.relpath(os.path.join(dirpath, name), app).replace(os.sep, "/"))
    return rels


def package_root(path: str) -> Optional[str]:
    root = c.find_upwards(path, ["Package.swift"])
    if root and os.sep + "Sources" + os.sep in os.path.abspath(path)[len(root):] + os.sep:
        return root
    return None


def analyze(sources: Sequence[str], app: Optional[str] = None, plist: Optional[str] = None) -> dict:
    if app and not os.path.isdir(app):
        raise c.UsageError(f"not a .app bundle directory: {app}")
    files, assets = index_tree(sources)
    app_files = index_app(app) if app else set()
    if app and not plist and os.path.isfile(os.path.join(app, "Info.plist")):
        plist = os.path.join(app, "Info.plist")
    info = c.load_plist(plist) if plist else None
    findings: List[c.Finding] = []
    lookups = 0
    font_hit = None
    pkg_cache: Dict[str, Optional[str]] = {}

    for sf in (c.SourceFile(p) for p in c.iter_files(sources, (".swift",), SOURCE_SKIP)):
        for m in FILE_LOOKUP.finditer(sf.text):
            if not sf.is_code(m.start()):
                continue
            lookups += 1
            name, ext, subdir = m.group(1), m.group(2) or "", m.group(3) or ""
            filename = f"{name}.{ext}" if ext else name
            line = sf.line_of(m.start())
            if filename not in files:
                findings.append(c.Finding("BR001", "error", f"'{filename}' not found anywhere under the sources",
                                          sf.path, line, "Check spelling/case (device file systems are case-"
                                          "sensitive), the extension, and that the file was added to the repo."))
                continue
            if app:
                expected = f"{subdir}/{filename}" if subdir else filename
                if expected in app_files:
                    continue
                nested = sorted(r for r in app_files if r.endswith("/" + filename) or r == filename)
                if nested:
                    findings.append(c.Finding("BR003", "error", f"'{filename}' is bundled at {nested[0]} but the "
                                              f"lookup expects {expected}", sf.path, line,
                                              f"Pass subdirectory: \"{os.path.dirname(nested[0])}\" (folder "
                                              "reference) or add the file as a group member so it lands at the "
                                              "bundle root."))
                else:
                    findings.append(c.Finding("BR002", "error", f"'{filename}' exists in sources but is not in "
                                              f"{os.path.basename(app)}", sf.path, line,
                                              "Tick the target under File Inspector > Target Membership (or add it "
                                              "to Copy Bundle Resources)."))

        for m in ASSET_LOOKUP.finditer(sf.text):
            if not sf.is_code(m.start()):
                continue
            lookups += 1
            name = m.group(1)
            if name in assets or name in files:
                continue
            findings.append(c.Finding("BR007", "error", f"asset '{name}' not found in any .xcassets or loose image",
                                      sf.path, sf.line_of(m.start()),
                                      "Names are case-sensitive and exclude the extension; namespaced folders need "
                                      "their prefix ('Icons/back')."))

        fm = FONT_USE.search(sf.code) if font_hit is None else None
        if fm:
            font_hit = (sf.path, sf.line_of(fm.start()))

        directory = os.path.dirname(os.path.abspath(sf.path))
        if directory not in pkg_cache:
            pkg_cache[directory] = package_root(sf.path)
        pkg = pkg_cache[directory]
        if pkg:
            for m in re.finditer(r"\bBundle\.main\b", sf.code):
                findings.append(c.Finding("BR004", "warning", "Bundle.main inside a Swift package points at the "
                                          "host app, not the package's resources", sf.path, sf.line_of(m.start()),
                                          "Use Bundle.module and declare `resources: [.process(\"Resources\")]`."))
            manifest = c.read_text(os.path.join(pkg, "Package.swift"))
            if re.search(r"\bBundle\.module\b", sf.code) and not re.search(r"resources\s*:", manifest):
                m = re.search(r"\bBundle\.module\b", sf.code)
                findings.append(c.Finding("BR005", "warning", "Bundle.module is only synthesized for targets with "
                                          "resources", sf.path, sf.line_of(m.start()),
                                          "Add `resources: [.process(\"Resources\")]` to the target in Package.swift."))

    if font_hit and info is not None and not info.get("UIAppFonts"):
        findings.append(c.Finding("BR006", "warning", "custom font requested but Info.plist has no UIAppFonts; "
                                  "UIFont(name:) returns nil and SwiftUI silently falls back to the system font",
                                  font_hit[0], font_hit[1],
                                  "List the font files (e.g. Inter-Regular.ttf) under UIAppFonts and add them to the "
                                  "target. Note: the PostScript name, not the file name, goes in UIFont(name:)."))
    return c.make_result(findings, lookups_checked=lookups, app=app)


@c.cli
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sources", nargs="+", metavar="DIR", help="source directories to scan (required)")
    p.add_argument("--app", help="built .app bundle to verify resources were copied")
    p.add_argument("--plist", help="Info.plist (for UIAppFonts); defaults to the .app's Info.plist")
    p.add_argument("--list-rules", action="store_true", help="print rule ids and exit")
    c.add_output_args(p)
    args = p.parse_args(argv)
    if args.list_rules:
        c.print_rules(RULES)
        return 0
    if not args.sources:
        p.error("--sources is required")
    result = analyze(args.sources, args.app, args.plist)
    if args.json:
        c.print_json(result)
    else:
        print(c.format_findings(result))
        print(f"checked {result['lookups_checked']} literal resource lookup(s)")
    return c.exit_status(result, args.fail_on)


if __name__ == "__main__":
    sys.exit(main())
