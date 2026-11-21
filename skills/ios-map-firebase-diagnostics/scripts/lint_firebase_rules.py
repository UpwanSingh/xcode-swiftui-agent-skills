#!/usr/bin/env python3
"""Lint Firestore / Cloud Storage security rules and cross-check them against app code.

Finds world-open rules, expired "test mode" rules (the classic cause of sudden
PERMISSION_DENIED / "Missing or insufficient permissions"), over-broad auth checks and
collections the app queries that no rule matches (default deny).

Examples:
  lint_firebase_rules.py firestore.rules
  lint_firebase_rules.py --root . --sources App/
  lint_firebase_rules.py storage.rules --today 2026-01-01 --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from typing import List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

RULES = {
    "FR001": ("error", "Rule allows access unconditionally (if true / no condition)"),
    "FR002": ("error", "Test-mode time limit has passed – every request is now denied"),
    "FR003": ("warning", "Test-mode time limit will expire"),
    "FR004": ("warning", "Recursive wildcard grants any signed-in user access to everything"),
    "FR005": ("info", "Write allowed for any signed-in user without an ownership check"),
    "FR006": ("warning", "rules_version = '2' missing while using recursive wildcards"),
    "FR007": ("warning", "Collection used in code has no matching rule (default deny)"),
    "FR008": ("warning", "collectionGroup query needs a {path=**} rule"),
}
ALLOW = re.compile(r"\ballow\s+([\w\s,]+?)\s*(?::\s*if\s+([^;]+))?;", re.S)
MATCH = re.compile(r"\bmatch\s+(/\S*?)\s*\{(?=\s)")
TIME_LIMIT = re.compile(r"request\.time\s*<\s*timestamp\.date\(\s*(\d{4})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})\s*\)")
COLLECTION_USE = re.compile(r"\.collection\s*\(\s*\"([^\"/]+)\"|\bcollection\s*\(\s*\"([^\"/]+)\"")
GROUP_USE = re.compile(r"\bcollectionGroup\s*\(\s*\"([^\"]+)\"")


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _line(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def _match_paths(text: str) -> List[tuple]:
    """Return [(full_path, start, end)] for each match block, joining nested paths."""
    blocks = []

    def walk(start: int, end: int, prefix: str) -> None:
        pos = start
        while True:
            m = MATCH.search(text, pos, end)
            if not m:
                return
            depth, i = 1, m.end()
            while i < end and depth:
                depth += {"{": 1, "}": -1}.get(text[i], 0)
                i += 1
            path = (prefix.rstrip("/") + m.group(1)) if prefix else m.group(1)
            blocks.append((path, m.start(), i))
            walk(m.end(), i - 1, path)
            pos = i

    walk(0, len(text), "")
    return blocks


def lint_rules(path: str, today: dt.date) -> tuple:
    raw = c.read_text(path)
    text = _strip_comments(raw)
    findings: List[c.Finding] = []
    service = "storage" if "firebase.storage" in text else "firestore"
    blocks = _match_paths(text)
    uses_recursive = "=**}" in text

    def enclosing(idx: int) -> str:
        best = ""
        for p, s, e in blocks:
            if s <= idx < e and len(p) >= len(best):
                best = p
        return best

    for m in ALLOW.finditer(text):
        ops = [o.strip() for o in m.group(1).split(",") if o.strip()]
        cond = (m.group(2) or "").strip()
        where = enclosing(m.start())
        line = _line(text, m.start())
        writes = [o for o in ops if o in ("write", "create", "update", "delete")]
        if not cond or cond == "true":
            sev = "error" if writes else "warning"  # public read-only data can be intentional
            findings.append(c.Finding("FR001", sev, f"allow {', '.join(ops)} on {where or '/'} with no condition: "
                                      "anyone with your project id can do this", path, line,
                                      fix="Require auth and ownership, e.g. `if request.auth != null && "
                                          "request.auth.uid == userId`."))
            continue
        tm = TIME_LIMIT.search(cond)
        if tm:
            limit = dt.date(int(tm.group(1)), int(tm.group(2)), int(tm.group(3)))
            if limit <= today:
                findings.append(c.Finding("FR002", "error", f"test-mode rule expired on {limit.isoformat()}: all "
                                          f"{', '.join(ops)} requests on {where or '/'} now fail with "
                                          "PERMISSION_DENIED", path, line,
                                          fix="Replace the time check with real auth rules and deploy "
                                              "(`firebase deploy --only firestore:rules`)."))
            else:
                findings.append(c.Finding("FR003", "warning", f"test-mode rule expires on {limit.isoformat()} "
                                          f"({(limit - today).days} days)", path, line,
                                          fix="Write production rules before the date; until then the data is open."))
            continue
        normalized = re.sub(r"\s+", " ", cond)
        segments = _doc_segments(where)
        if normalized == "request.auth != null" and len(segments) == 1 and segments[0].endswith("=**}"):
            findings.append(c.Finding("FR004", "warning", f"any signed-in user can {', '.join(ops)} every document "
                                      f"({where})", path, line,
                                      fix="Scope rules per collection and check request.auth.uid against the "
                                          "document owner."))
        elif normalized == "request.auth != null" and writes:
            findings.append(c.Finding("FR005", "info", f"any signed-in user can {', '.join(writes)} {where}", path,
                                      line, fix="Add an ownership check (request.auth.uid == resource.data.ownerId)."))

    if uses_recursive and not re.search(r"rules_version\s*=\s*['\"]2['\"]", text):
        findings.append(c.Finding("FR006", "warning", "recursive wildcards behave differently in rules version 1 "
                                  "and collection-group queries are denied", path, 1,
                                  fix="Add `rules_version = '2';` as the first line."))
    return findings, blocks, service


def _doc_segments(rule_path: str) -> List[str]:
    """Path segments below /databases/{database}/documents (or the storage bucket root)."""
    segs = [s for s in rule_path.split("/") if s]
    if len(segs) >= 3 and segs[0] == "databases" and segs[2] == "documents":
        return segs[3:]
    if len(segs) >= 3 and segs[0] == "b" and segs[2] == "o":
        return segs[3:]
    return segs


def _path_matches(rule_path: str, collection: str) -> bool:
    segs = _doc_segments(rule_path)
    # Top-level wildcard ({coll} / {document=**}) or the name appearing as a literal segment
    # (sub-collections such as /users/{uid}/posts/{id}).
    return bool(segs) and (segs[0].startswith("{") or collection in segs)


def analyze(paths: Optional[Sequence[str]] = None, root: Optional[str] = None,
            sources: Optional[Sequence[str]] = None, today: Optional[str] = None) -> dict:
    day = dt.date.fromisoformat(today) if today else dt.date.today()
    files = list(paths or [])
    if not files:
        if not root:
            raise c.UsageError("pass rules files or --root")
        for dirpath, _, filenames in c.walk(root):
            files += [os.path.join(dirpath, f) for f in filenames if f.endswith(".rules")]
        if not files:
            raise c.UsageError(f"no *.rules files under {root}")
    findings: List[c.Finding] = []
    firestore_blocks: List[tuple] = []
    firestore_file = None
    for f in files:
        fnd, blocks, service = lint_rules(f, day)
        findings += fnd
        if service == "firestore":
            firestore_blocks += blocks
            firestore_file = f

    used = {}
    if sources and firestore_file:
        for sf in (c.SourceFile(p) for p in c.iter_files(sources, (".swift",))):
            for m in COLLECTION_USE.finditer(sf.text):
                if sf.is_code(m.start()):
                    used.setdefault(m.group(1) or m.group(2), (sf.path, sf.line_of(m.start())))
            for m in GROUP_USE.finditer(sf.text):
                if sf.is_code(m.start()):
                    name = m.group(1)
                    ok = any("=**}" in p and p.rstrip("/").split("/")[-2:-1] == [name] for p, _, _ in firestore_blocks)
                    if not ok:
                        findings.append(c.Finding("FR008", "warning", f"collectionGroup(\"{name}\") has no rule like "
                                                  f"match /{{path=**}}/{name}/{{id}}", sf.path, sf.line_of(m.start()),
                                                  fix="Add the recursive-wildcard rule (requires rules_version 2)."))
        for name, (fpath, line) in sorted(used.items()):
            if not any(_path_matches(p, name) for p, _, _ in firestore_blocks):
                findings.append(c.Finding("FR007", "warning", f"collection \"{name}\" is used in code but no rule "
                                          f"in {os.path.basename(firestore_file)} matches it, so requests are denied",
                                          fpath, line, fix=f"Add `match /{name}/{{docId}} {{ allow read: if ...; }}`."))
    return c.make_result(findings, rules_files=files, collections_in_code=sorted(used), today=day.isoformat())


@c.cli
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("rules", nargs="*", help="firestore.rules / storage.rules files")
    p.add_argument("--root", help="search this directory for *.rules when no files are given")
    p.add_argument("--sources", nargs="+", help="Swift sources to cross-check collection names")
    p.add_argument("--today", help="override today's date (YYYY-MM-DD) for reproducible results")
    p.add_argument("--list-rules", action="store_true", help="print rule ids and exit")
    c.add_output_args(p)
    args = p.parse_args(argv)
    if args.list_rules:
        c.print_rules(RULES)
        return 0
    try:
        result = analyze(args.rules, args.root, args.sources, args.today)
    except ValueError as exc:
        raise c.UsageError(str(exc)) from exc
    if args.json:
        c.print_json(result)
    else:
        print(c.format_findings(result))
    return c.exit_status(result, args.fail_on)


if __name__ == "__main__":
    sys.exit(main())
