#!/usr/bin/env python3
"""Static checks for SwiftUI state, identity, navigation and layout pitfalls.

Heuristic and read-only: it never compiles or modifies code. Every finding names a
rule (see --list-rules) so it can be looked up in references/.

Examples:
  swiftui_lint.py Sources/
  swiftui_lint.py App/ContentView.swift --json
  swiftui_lint.py . --fail-on warning      # use as a CI gate
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Dict, List, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

RULES = {
    "SUI001": ("warning", "@State/@StateObject property is not private"),
    "SUI002": ("warning", "@State seeded from an init parameter ignores later parent updates"),
    "SUI003": ("error", "@ObservedObject created inline is recreated on every parent re-render"),
    "SUI004": ("error", "@Observable type used with an ObservableObject property wrapper"),
    "SUI005": ("warning", "@Published inside an @Observable class"),
    "SUI006": ("warning", "Unstable view identity (.id(UUID()) or ForEach id: \\.offset)"),
    "SUI007": ("warning", "onChange handler mutates the value it observes (update loop)"),
    "SUI008": ("warning", "GeometryReader writes its size into state (layout feedback loop)"),
    "SUI009": ("info", "NavigationView is deprecated; use NavigationStack/NavigationSplitView"),
    "SUI010": ("warning", "Navigation modifiers attached outside a NavigationStack are ignored"),
    "SUI011": ("warning", "TabView nested inside a NavigationStack"),
    "SUI012": ("warning", "navigationDestination declared inside a lazy container"),
    "SUI013": ("info", "Self._printChanges()/_logChanges() left in code"),
}

_MODS = r"(?:(?:public|internal|package|open|private|fileprivate|final|nonisolated)\s+)*"
OBSERVABLE_CLASS = re.compile(r"@Observable\b(?:\s+@\w+(?:\([^)]*\))?)*\s+" + _MODS + r"class\s+(\w+)")
STATE_NOT_PRIVATE = re.compile(r"@(State|StateObject)\s+(?!private\b|fileprivate\b)(?:(?:public|internal|package|open)\s+)?var\s+(\w+)")
STATE_FROM_INIT = re.compile(r"\b_(\w+)\s*=\s*State\s*\(\s*(?:initialValue|wrappedValue)\s*:")
OBSERVED_INLINE = re.compile(r"@ObservedObject\s+" + _MODS + r"var\s+(\w+)\s*(?::\s*[\w.<>?]+\s*)?=\s*([A-Z][\w.]*)\s*\(")
OBJECT_WRAPPER = re.compile(r"@(StateObject|ObservedObject|EnvironmentObject)\s+" + _MODS +
                            r"var\s+(\w+)\s*(?::\s*([A-Z]\w*))?\s*(?:=\s*([A-Z]\w*)\s*\()?")
UNSTABLE_ID = re.compile(r"\.id\(\s*(UUID|Date)\s*\(\s*\)\s*\)")
OFFSET_ID = re.compile(r"\bForEach\s*\([^{}]*?enumerated\(\)[^{}]*?id:\s*\\\.offset")
ON_CHANGE = re.compile(r"\.onChange\s*\(\s*of:\s*(?:self\.)?\$?([A-Za-z_][\w.]*)")
GEOMETRY = re.compile(r"\bGeometryReader\s*\{\s*(\w+)\s+in\b")
NAV_VIEW = re.compile(r"\bNavigationView\s*[{(]")
NAV_STACK = re.compile(r"\bNavigationStack\b")
NAV_MODIFIER = re.compile(r"\.(navigationTitle|navigationBarTitleDisplayMode|toolbar|toolbarBackground|"
                          r"navigationBarBackButtonHidden|navigationBarItems|toolbarRole)\b")
LAZY_CONTAINER = re.compile(r"\b(List|LazyVStack|LazyHStack|LazyVGrid|LazyHGrid|ForEach)\b")
PRINT_CHANGES = re.compile(r"\bSelf\._(printChanges|logChanges)\s*\(\s*\)")

WRAPPER_FIX = {
    "StateObject": "use `@State private var` for @Observable types",
    "ObservedObject": "use a plain `let`/`var` (or `@Bindable var` if you need bindings) for @Observable types",
    "EnvironmentObject": "use `@Environment(Type.self) private var` and inject with `.environment(object)`",
}


def _finding(rule: str, sf: c.SourceFile, idx: int, message: str, fix: str) -> c.Finding:
    return c.Finding(rule, RULES[rule][0], message, sf.path, sf.line_of(idx), fix)


def _init_params(sf: c.SourceFile, idx: int) -> List[str]:
    """Internal parameter names of the init enclosing `idx` (empty if not inside an init)."""
    brace = sf.enclosing_open(idx)
    header = sf.header_before(brace) if brace >= 0 else ""
    m = re.search(r"\binit\s*[?!]?\s*(?:<[^>]*>)?\s*\((.*)\)", header, re.S)
    if not m:
        return []
    names = []
    for part in m.group(1).split(","):
        label = part.split(":", 1)[0].split()
        if label:
            names.append(label[-1])
    return [n for n in names if n != "_"]


def observable_classes(files: Sequence[c.SourceFile]) -> Dict[str, tuple]:
    """Map @Observable class name -> (SourceFile, index of declaration)."""
    found = {}
    for sf in files:
        for m in OBSERVABLE_CLASS.finditer(sf.code):
            found[m.group(1)] = (sf, m.start(1))
    return found


def check_file(sf: c.SourceFile, observables: Dict[str, tuple]) -> List[c.Finding]:
    out: List[c.Finding] = []
    code = sf.code

    for m in STATE_NOT_PRIVATE.finditer(code):
        out.append(_finding("SUI001", sf, m.start(), f"@{m.group(1)} var {m.group(2)} is not private",
                            "Mark view-owned state `private`; callers that pass a value through the memberwise "
                            "init only set the *initial* value, which is a common 'my view does not update' bug."))

    for m in STATE_FROM_INIT.finditer(code):
        params = _init_params(sf, m.start())
        line_end = code.find("\n", m.end())
        expr = code[m.end():line_end if line_end >= 0 else len(code)]
        if not any(re.search(r"\b" + re.escape(p) + r"\b", expr) for p in params):
            continue  # seeded from a constant / fresh object, not from a parameter
        out.append(_finding("SUI002", sf, m.start(), f"_{m.group(1)} = State(...) seeds state from init",
                            "State keeps its first value for the view's lifetime. If the parent owns the value use "
                            "@Binding or a `let`; if you need a resettable local copy add `.id(input)` on the child "
                            "or sync it in `.onChange(of: input)`."))

    for m in OBSERVED_INLINE.finditer(code):
        out.append(_finding("SUI003", sf, m.start(), f"@ObservedObject var {m.group(1)} = {m.group(2)}(...) "
                            "is re-created whenever the parent re-renders",
                            "Own the object with @StateObject (ObservableObject) or `@State private var` "
                            "(@Observable), or inject it from the parent."))

    for m in OBJECT_WRAPPER.finditer(code):
        type_name = m.group(3) or m.group(4)
        if type_name in observables:
            out.append(_finding("SUI004", sf, m.start(), f"@{m.group(1)} used with @Observable class {type_name}",
                                WRAPPER_FIX[m.group(1)]))

    for m in UNSTABLE_ID.finditer(code):
        out.append(_finding("SUI006", sf, m.start(), f".id({m.group(1)}()) gives the view a new identity on every "
                            "render: state resets, animations restart and onAppear fires repeatedly",
                            "Use a stable identifier (model id) or remove .id(); to force a reset, change the id "
                            "only when the reset should happen."))
    for m in OFFSET_ID.finditer(code):
        out.append(_finding("SUI006", sf, m.start(), "ForEach identifies rows by enumerated offset; inserts/deletes "
                            "re-bind state and animations to the wrong rows",
                            "Iterate `items` (Identifiable) or use `id: \\.element.id`."))

    for m in ON_CHANGE.finditer(code):
        expr = m.group(1)
        paren = code.find("(", m.start())
        close = sf.match_close(paren) if paren >= 0 else -1
        if close < 0:
            continue
        inner_brace = code.find("{", m.end(), close)
        block = (inner_brace, sf.match_close(inner_brace)) if inner_brace >= 0 else sf.block_after(close + 1)
        if not block or block[1] < 0:
            continue
        body = code[block[0]:block[1]]
        mutation = re.compile(r"(?<![\w.])(?:self\.)?" + re.escape(expr) +
                              r"\s*(?:=(?!=)|\+=|-=|\*=|/=|\.append\(|\.toggle\(|\.insert\(|\.remove)")
        if mutation.search(body):
            out.append(_finding("SUI007", sf, m.start(), f"onChange(of: {expr}) mutates `{expr}` inside its own "
                                "handler", "Guard the write (`if new != normalized { ... }`), derive the value with "
                                "a computed property, or move the logic into the model's setter (didSet)."))

    for m in GEOMETRY.finditer(code):
        param = m.group(1)
        brace = code.find("{", m.start())
        close = sf.match_close(brace)
        if close < 0:
            continue
        if re.search(r"(?<![=!<>])=\s*" + re.escape(param) + r"\.(?:size\b|frame\s*\()", code[brace:close]):
            out.append(_finding("SUI008", sf, m.start(), "GeometryReader assigns its proxy size/frame to state; "
                                "if that state affects layout this re-lays out forever (AttributeGraph cycle)",
                                "Use `.onGeometryChange(for: CGSize.self) { $0.size } action: { size = $0 }` "
                                "(iOS 16+, Xcode 16 SDK) and make sure the measured view does not depend on `size`."))

    for m in NAV_VIEW.finditer(code):
        out.append(_finding("SUI009", sf, m.start(), "NavigationView is deprecated (iOS 16+) and behaves "
                            "differently on iPad (split view) and in TabView",
                            "Replace with NavigationStack (or NavigationSplitView for sidebars)."))

    for m in NAV_STACK.finditer(code):
        block = sf.block_after(m.end())
        if not block:
            continue
        open_, close = block
        tail = code[close + 1:close + 400]
        stripped = tail.lstrip()
        mod = NAV_MODIFIER.match(stripped)
        if mod:
            idx = close + 1 + (len(tail) - len(stripped))
            out.append(_finding("SUI010", sf, idx, f".{mod.group(1)} is attached to the NavigationStack itself, "
                                "so the bar never sees it (title/toolbar missing)",
                                "Move the modifier inside the stack onto the root content view."))
        tab = re.search(r"\bTabView\b", code[open_:close])
        if tab:
            out.append(_finding("SUI011", sf, open_ + tab.start(), "TabView is inside a NavigationStack: every tab "
                                "shares one navigation bar, so titles/toolbars glitch or show the wrong tab's values",
                                "Make TabView the root and give each tab its own NavigationStack."))

    reported = set()
    for m in LAZY_CONTAINER.finditer(code):
        block = sf.block_after(m.end())
        if not block:
            continue
        for d in re.finditer(r"\.navigationDestination\s*\(", code[block[0]:block[1]]):
            idx = block[0] + d.start()
            if idx in reported:
                continue
            reported.add(idx)
            out.append(_finding("SUI012", sf, idx, f".navigationDestination is inside a lazy container ({m.group(1)}); "
                                "SwiftUI ignores it until the row is realised, so links silently do nothing",
                                "Attach .navigationDestination(for:) to the List/ScrollView or the stack's root view."))

    for m in PRINT_CHANGES.finditer(code):
        out.append(_finding("SUI013", sf, m.start(), f"Self._{m.group(1)}() is a debugging aid",
                            "Remove before shipping (wrap in `#if DEBUG` if you want to keep it)."))
    return out


def analyze(paths: Sequence[str]) -> dict:
    files = c.load_swift_files(paths)
    observables = observable_classes(files)
    findings: List[c.Finding] = []
    for sf in files:
        findings.extend(check_file(sf, observables))
    for name, (sf, idx) in observables.items():
        brace = sf.code.find("{", idx)
        close = sf.match_close(brace) if brace >= 0 else -1
        if close > 0:
            for m in re.finditer(r"@Published\b", sf.code[brace:close]):
                findings.append(_finding("SUI005", sf, brace + m.start(), f"@Published inside @Observable class {name}",
                                         "Remove @Published; @Observable tracks stored properties automatically. "
                                         "Mixing both breaks observation (and fails to compile on recent SDKs)."))
    return c.make_result(findings, files_scanned=len(files), observable_classes=sorted(observables))


@c.cli
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="*", help="Swift files or directories to scan")
    p.add_argument("--list-rules", action="store_true", help="print rule ids and exit")
    c.add_output_args(p)
    args = p.parse_args(argv)
    if args.list_rules:
        c.print_rules(RULES)
        return 0
    if not args.paths:
        p.error("at least one path is required")
    result = analyze(args.paths)
    if args.json:
        c.print_json(result)
    else:
        print(c.format_findings(result))
        print(f"scanned {result['files_scanned']} Swift file(s)")
    return c.exit_status(result, args.fail_on)


if __name__ == "__main__":
    sys.exit(main())
