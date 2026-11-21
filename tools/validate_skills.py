#!/usr/bin/env python3
"""Validate every skill under skills/ against the Agent Skills format and this repo's conventions.

Checks per skill:
  * SKILL.md exists, has YAML frontmatter with only known keys
  * name: lowercase-hyphenated, <= 64 chars, matches the directory, no reserved words
  * description: 1..1024 chars, no angle brackets; compatibility <= 500 chars
  * SKILL.md is under 500 lines; every relative link resolves; references are one level deep
  * scripts/*.py compile and `--help` exits 0; shell assets pass `bash -n`
  * every rule id declared in a script (RULES / CATALOG / RUNTIME_WARNINGS) is documented in the skill

  python3 tools/validate_skills.py            # all skills
  python3 tools/validate_skills.py skills/x   # one skill
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from typing import Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
RESERVED = ("anthropic", "claude")
MAX_LINES = 500


def parse_frontmatter(text: str) -> Tuple[Dict[str, object], int]:
    """Parse the small YAML subset used in SKILL.md frontmatter (scalars + one nested map)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md must start with '---' frontmatter")
    data: Dict[str, object] = {}
    current_map = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return data, i + 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            if current_map is None:
                raise ValueError(f"line {i + 1}: unexpected indentation")
            k, _, v = line.strip().partition(":")
            current_map[k.strip()] = v.strip().strip('"\'')
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"line {i + 1}: expected 'key: value'")
        key, value = key.strip(), value.strip()
        if value:
            data[key] = value.strip('"\'')
            current_map = None
        else:
            current_map = {}
            data[key] = current_map
    raise ValueError("frontmatter is not closed with '---'")


def declared_rule_ids(script: str) -> List[str]:
    tree = ast.parse(open(script, encoding="utf-8").read(), script)
    ids: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ("RULES", "CATALOG", "RUNTIME_WARNINGS")
                                                for t in node.targets):
            if isinstance(node.value, ast.Dict):
                ids += [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
            elif isinstance(node.value, ast.List):
                ids += [e.elts[0].value for e in node.value.elts
                        if isinstance(e, ast.Tuple) and isinstance(e.elts[0], ast.Constant)]
    return ids


def validate(skill_dir: str) -> List[str]:
    errors: List[str] = []
    name = os.path.basename(os.path.normpath(skill_dir))
    path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(path):
        return [f"{name}: SKILL.md missing"]
    text = open(path, encoding="utf-8").read()
    try:
        meta, body_start = parse_frontmatter(text)
    except ValueError as exc:
        return [f"{name}: {exc}"]

    unknown = set(meta) - ALLOWED_KEYS
    if unknown:
        errors.append(f"{name}: unknown frontmatter keys: {', '.join(sorted(unknown))}")
    skill_name = str(meta.get("name", ""))
    if not NAME_RE.match(skill_name) or len(skill_name) > 64:
        errors.append(f"{name}: invalid name {skill_name!r} (lowercase letters, digits, single hyphens, <= 64)")
    if skill_name != name:
        errors.append(f"{name}: frontmatter name {skill_name!r} must match directory name")
    if any(r in skill_name for r in RESERVED):
        errors.append(f"{name}: name must not contain reserved words {RESERVED}")
    desc = str(meta.get("description", ""))
    if not desc or len(desc) > 1024:
        errors.append(f"{name}: description must be 1-1024 characters (got {len(desc)})")
    if "<" in desc or ">" in desc:
        errors.append(f"{name}: description must not contain angle brackets")
    if len(str(meta.get("compatibility", ""))) > 500:
        errors.append(f"{name}: compatibility must be <= 500 characters")
    if "metadata" in meta and not isinstance(meta["metadata"], dict):
        errors.append(f"{name}: metadata must be a mapping")

    n_lines = text.count("\n") + 1
    if n_lines >= MAX_LINES:
        errors.append(f"{name}: SKILL.md has {n_lines} lines (keep it under {MAX_LINES}; move detail to references/)")
    body = "\n".join(text.splitlines()[body_start:])
    for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", body):
        if re.match(r"^[a-z]+://", target):
            continue
        if not os.path.exists(os.path.join(skill_dir, target)):
            errors.append(f"{name}: broken link {target}")
        if target.startswith("references/") and target.count("/") > 1:
            errors.append(f"{name}: reference {target} is nested; keep references one level deep")

    docs = text
    for sub in ("references",):
        d = os.path.join(skill_dir, sub)
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.endswith(".md"):
                    docs += open(os.path.join(d, f), encoding="utf-8").read()

    scripts = os.path.join(skill_dir, "scripts")
    for f in sorted(os.listdir(scripts)) if os.path.isdir(scripts) else []:
        full = os.path.join(scripts, f)
        if f.endswith(".py"):
            try:
                compile(open(full, encoding="utf-8").read(), full, "exec")
            except SyntaxError as exc:
                errors.append(f"{name}: {f} does not compile: {exc}")
                continue
            if f.startswith("_"):
                continue
            if not os.access(full, os.X_OK):
                errors.append(f"{name}: {f} is not executable (chmod +x)")
            proc = subprocess.run([sys.executable, full, "--help"], capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                errors.append(f"{name}: `{f} --help` exited {proc.returncode}: {proc.stderr.strip()[:200]}")
            for rid in declared_rule_ids(full):
                if rid not in docs:
                    errors.append(f"{name}: rule {rid} from {f} is not documented in SKILL.md or references/")
    assets = os.path.join(skill_dir, "assets")
    for f in sorted(os.listdir(assets)) if os.path.isdir(assets) else []:
        if f.endswith(".sh"):
            proc = subprocess.run(["bash", "-n", os.path.join(assets, f)], capture_output=True, text=True)
            if proc.returncode != 0:
                errors.append(f"{name}: assets/{f} has shell syntax errors: {proc.stderr.strip()[:200]}")
    for required in ("scripts", "references"):
        if not os.path.isdir(os.path.join(skill_dir, required)):
            errors.append(f"{name}: missing {required}/ directory")
    return errors


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    dirs = args or sorted(os.path.join(ROOT, "skills", d) for d in os.listdir(os.path.join(ROOT, "skills"))
                          if os.path.isdir(os.path.join(ROOT, "skills", d)))
    failures = 0
    for d in dirs:
        errs = validate(d)
        status = "ok" if not errs else "FAIL"
        lines = open(os.path.join(d, "SKILL.md")).read().count("\n") + 1 if os.path.isfile(os.path.join(d, "SKILL.md")) else 0
        print(f"[{status}] {os.path.basename(os.path.normpath(d))} ({lines} lines)")
        for e in errs:
            print(f"    - {e}")
        failures += bool(errs)
    print(f"{len(dirs) - failures}/{len(dirs)} skills valid")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
