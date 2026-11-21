"""Shared helpers for the skill scripts (stdlib only, Python 3.9+).

Every skill ships an identical copy of this module so that each skill folder works
when copied on its own. Do not edit the copies under skills/*/scripts/ directly:
edit shared/_common.py and run `python3 tools/sync_common.py`.
"""
from __future__ import annotations

import bisect
import datetime as _dt
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

SEVERITIES = ("error", "warning", "info")
SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}

# Directories that never contain first-party sources worth scanning.
SKIP_DIRS = frozenset({
    ".git", ".build", ".swiftpm", "build", "DerivedData", "Pods", "Carthage",
    "SourcePackages", "checkouts", "xcuserdata", "node_modules", "__pycache__",
    ".venv", "venv", "fastlane",
})

MAX_FILE_BYTES = 8 * 1024 * 1024


class UsageError(Exception):
    """Bad input (missing file, wrong flag combination). CLIs exit with status 2."""


# --------------------------------------------------------------------------- findings

@dataclass
class Finding:
    rule: str
    severity: str
    message: str
    file: Optional[str] = None
    line: Optional[int] = None
    fix: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def make_result(findings: Iterable[Finding], **extra: Any) -> Dict[str, Any]:
    """Sort findings, de-duplicate them and attach severity counts."""
    unique: Dict[Tuple, Finding] = {}
    for f in findings:
        unique.setdefault((f.rule, f.file, f.line, f.message), f)
    ordered = sorted(unique.values(), key=lambda f: (SEVERITY_RANK[f.severity], f.file or "", f.line or 0, f.rule))
    counts = {s: 0 for s in SEVERITIES}
    for f in ordered:
        counts[f.severity] += 1
    result: Dict[str, Any] = {"findings": [f.to_dict() for f in ordered], "summary": counts}
    result.update(extra)
    return result


def exit_status(result: Dict[str, Any], fail_on: str) -> int:
    if fail_on == "never":
        return 0
    limit = SEVERITY_RANK[fail_on]
    return 1 if any(SEVERITY_RANK[f["severity"]] <= limit for f in result.get("findings", [])) else 0


def format_findings(result: Dict[str, Any]) -> str:
    """Compiler-style output (`path:line: severity [RULE] message`) so editors make it clickable."""
    lines = []
    for f in result.get("findings", []):
        loc = ""
        if f.get("file"):
            loc = f"{f['file']}:{f['line']}: " if f.get("line") else f"{f['file']}: "
        lines.append(f"{loc}{f['severity']} [{f['rule']}] {f['message']}")
        if f.get("fix"):
            lines.append(f"    fix: {f['fix']}")
    s = result.get("summary", {})
    lines.append(f"{s.get('error', 0)} error(s), {s.get('warning', 0)} warning(s), {s.get('info', 0)} info")
    return "\n".join(lines)


def add_output_args(parser, default_fail: str = "error") -> None:
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON instead of text")
    parser.add_argument("--fail-on", choices=("error", "warning", "info", "never"), default=default_fail,
                        help="exit 1 when a finding at or above this severity exists (default: %(default)s)")


def print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def print_rules(rules: Dict[str, Tuple[str, str]]) -> None:
    for rule, (severity, description) in rules.items():
        print(f"{rule}  {severity:<7}  {description}")


def cli(main: Callable[[Optional[Sequence[str]]], int]) -> Callable[[Optional[Sequence[str]]], int]:
    """Wrap a CLI entry point: UsageError -> exit 2 with a one-line message."""
    def wrapper(argv: Optional[Sequence[str]] = None) -> int:
        try:
            return main(argv)
        except UsageError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            return 130
    wrapper.__name__ = main.__name__
    wrapper.__doc__ = main.__doc__
    return wrapper


# --------------------------------------------------------------------------- files

# Optional read guard installed by a host such as the MCP server. When set, it receives the
# symlink-resolved path of every file a script walks to or opens, and anything it rejects is
# skipped while walking (or refused when opened). This is what stops a symlink committed inside
# a scanned project from pulling in ~/.ssh/... or other files outside the allowed roots.
PATH_GUARD: Optional[Callable[[str], bool]] = None


def path_allowed(path: str) -> bool:
    return PATH_GUARD is None or bool(PATH_GUARD(os.path.realpath(path)))


def require_allowed(path: str) -> str:
    if not path_allowed(path):
        raise UsageError(f"refusing to read {path}: outside the allowed roots or a protected file")
    return path


def walk(root: str, skip_dirs: Optional[Iterable[str]] = SKIP_DIRS,
         skip_hidden: bool = True) -> Iterator[Tuple[str, List[str], List[str]]]:
    """os.walk (never following directory symlinks) that prunes noise directories and drops
    entries PATH_GUARD rejects. Callers may prune `dirnames` further in place."""
    skip = set(skip_dirs or ())
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in skip and not (skip_hidden and d.startswith("."))
                             and path_allowed(os.path.join(dirpath, d)))
        filenames[:] = sorted(f for f in filenames if path_allowed(os.path.join(dirpath, f)))
        yield dirpath, dirnames, filenames


def iter_files(paths: Sequence[str], exts: Sequence[str], skip_dirs: Iterable[str] = SKIP_DIRS) -> Iterator[str]:
    """Yield files under `paths` with one of `exts`. Explicit file arguments are always yielded."""
    seen = set()
    for p in paths:
        if not os.path.exists(p):
            raise UsageError(f"no such file or directory: {p}")
        if os.path.isfile(p):
            candidates: Iterable[str] = [require_allowed(p)]
        else:
            candidates = (os.path.join(d, f) for d, _, files in walk(p, skip_dirs)
                          for f in files if f.endswith(tuple(exts)))
        for path in candidates:
            real = os.path.realpath(path)
            if real not in seen:
                seen.add(real)
                yield path


def read_text(path: str) -> str:
    require_allowed(path)
    try:
        size = os.path.getsize(path)
        if size > MAX_FILE_BYTES:
            raise UsageError(f"{path} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB; pass a smaller file")
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError as exc:
        raise UsageError(f"cannot read {path}: {exc.strerror}") from exc


def find_upwards(start: str, names: Sequence[str], stop: Optional[str] = None) -> Optional[str]:
    """Return the first ancestor directory of `start` containing one of `names`."""
    cur = os.path.abspath(start if os.path.isdir(start) else os.path.dirname(start))
    stop = os.path.abspath(stop) if stop else None
    while True:
        for n in names:
            if os.path.exists(os.path.join(cur, n)):
                return cur
        parent = os.path.dirname(cur)
        if parent == cur or (stop and cur == stop):
            return None
        cur = parent


# --------------------------------------------------------------------------- Swift source scanning

def strip_swift(src: str) -> str:
    """Blank out comments and string-literal contents, preserving length and newlines.

    Indices into the result line up with the original, so regexes can run on code only
    while line numbers still come from the original text.
    """
    out = list(src)
    n = len(src)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    def skip_string(i: int) -> int:
        """`i` points at an opening quote; return index just past the closing quote."""
        if src.startswith('"""', i):
            j = i + 3
            while j < n and not src.startswith('"""', j):
                j = skip_interp(j) if src.startswith("\\(", j) else j + (2 if src[j] == "\\" else 1)
            blank(i + 3, j)
            return min(j + 3, n)
        j = i + 1
        while j < n and src[j] not in '"\n':
            if src.startswith("\\(", j):
                j = skip_interp(j)
            elif src[j] == "\\" and j + 1 < n and src[j + 1] != "\n":
                j += 2
            else:
                j += 1
        blank(i + 1, j)
        return j + 1 if j < n and src[j] == '"' else j

    def skip_raw_string(i: int) -> int:
        """`i` points at the first `#` of a raw string such as #"..."#; backslashes are literal."""
        hashes = len(re.match(r"#+", src[i:]).group(0))
        start = i + hashes
        quote = '"""' if src.startswith('"""', start) else '"'
        close = quote + "#" * hashes
        end = src.find(close, start + len(quote))
        end = n if end == -1 else end
        blank(start + len(quote), end)
        return min(end + len(close), n)

    def skip_interp(j: int) -> int:
        """`j` points at a backslash of `\\(`; return index past the matching `)`."""
        depth, k = 1, j + 2
        while k < n and depth:
            ch = src[k]
            if ch == '"':
                k = skip_string(k)
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "\n" and depth:
                break
            k += 1
        return k

    i = 0
    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j == -1 else j
            blank(i, j)
            i = j
        elif src.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if src.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif src.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            blank(i, j)
            i = j
        elif src[i] == "#" and re.match(r'#+"', src[i:i + 16]):
            i = skip_raw_string(i)
        elif src[i] == '"':
            i = skip_string(i)
        else:
            i += 1
    return "".join(out)


_PAIRS = {"{": "}", "(": ")", "[": "]"}


class SourceFile:
    """A Swift source file with a comment/string-stripped twin for structural regexes."""

    def __init__(self, path: str, text: Optional[str] = None):
        self.path = path
        self.text = read_text(path) if text is None else text
        self.code = strip_swift(self.text)
        self._starts = [0] + [m.end() for m in re.finditer("\n", self.text)]

    def line_of(self, idx: int) -> int:
        return bisect.bisect_right(self._starts, idx)

    def is_code(self, idx: int) -> bool:
        return 0 <= idx < len(self.code) and self.code[idx] == self.text[idx]

    def match_close(self, open_idx: int) -> int:
        """Index of the bracket matching the one at `open_idx` (-1 if unbalanced)."""
        code = self.code
        opener = code[open_idx]
        closer = _PAIRS[opener]
        depth = 0
        for j in range(open_idx, len(code)):
            ch = code[j]
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return j
        return -1

    def block_after(self, pos: int) -> Optional[Tuple[int, int]]:
        """For `Name(args) { ... }` with `pos` just after `Name`, return (open, close) of the braces."""
        code, n, i = self.code, len(self.code), pos
        while i < n and code[i].isspace():
            i += 1
        if i < n and code[i] == "(":
            j = self.match_close(i)
            if j < 0:
                return None
            i = j + 1
            while i < n and code[i].isspace():
                i += 1
        if i < n and code[i] == "{":
            j = self.match_close(i)
            if j > 0:
                return i, j
        return None

    def enclosing_open(self, idx: int) -> int:
        """Index of the `{` that encloses `idx` (-1 at file scope)."""
        depth = 0
        for j in range(idx - 1, -1, -1):
            ch = self.code[j]
            if ch == "}":
                depth += 1
            elif ch == "{":
                if depth == 0:
                    return j
                depth -= 1
        return -1

    def header_before(self, open_idx: int) -> str:
        """Declaration text preceding a `{` (e.g. `func load() async`), without the brace."""
        start = max(0, open_idx - 400)
        chunk = self.code[start:open_idx]
        cut = max(chunk.rfind("{"), chunk.rfind("}"), chunk.rfind(";"))
        return chunk[cut + 1:].strip()

    def depth_at(self, idx: int) -> int:
        code = self.code[:idx]
        return code.count("{") - code.count("}")


def load_swift_files(paths: Sequence[str]) -> List[SourceFile]:
    return [SourceFile(p) for p in iter_files(paths, (".swift",))]


# --------------------------------------------------------------------------- property lists

def extract_embedded_plist(data: bytes) -> Optional[bytes]:
    """Return the XML plist embedded in a CMS blob (e.g. a .mobileprovision), if any."""
    start = data.find(b"<?xml")
    end = data.find(b"</plist>", start)
    if start < 0 or end < 0:
        return None
    return data[start:end + len(b"</plist>")]


def load_plist(path: str) -> Any:
    require_allowed(path)
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_FILE_BYTES)
    except OSError as exc:
        raise UsageError(f"cannot read {path}: {exc.strerror}") from exc
    try:
        return plistlib.loads(data)
    except Exception as exc:  # plistlib raises several unrelated exception types
        inner = extract_embedded_plist(data)
        if inner is not None:
            try:
                return plistlib.loads(inner)
            except Exception:
                pass
        raise UsageError(f"cannot parse property list {path}: {exc}") from exc


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        return f"<{len(obj)} bytes>"
    if isinstance(obj, (_dt.datetime, _dt.date)):
        return obj.isoformat()
    return obj


def _der_strings(der: bytes, oid: bytes) -> List[str]:
    values = []
    i = der.find(oid)
    while i >= 0:
        tag = i + len(oid)
        if tag + 2 <= len(der) and der[tag] in (0x0C, 0x13, 0x14, 0x16) and der[tag + 1] < 0x80:
            ln = der[tag + 1]
            values.append(der[tag + 2:tag + 2 + ln].decode("utf-8", "replace"))
        i = der.find(oid, i + len(oid))
    return values


def cert_info(der: bytes) -> Dict[str, Any]:
    """Best-effort summary of a DER certificate without third-party ASN.1 libraries.

    The SHA-1 matches what `security find-identity -v -p codesigning` prints.
    """
    info: Dict[str, Any] = {"sha1": hashlib.sha1(der).hexdigest().upper()}
    cns = _der_strings(der, b"\x06\x03\x55\x04\x03")
    ous = _der_strings(der, b"\x06\x03\x55\x04\x0b")
    if cns:
        info["subject_cn"] = cns[-1]  # issuer precedes subject in TBSCertificate
    if ous:
        info["subject_ou"] = ous[-1]
    times = []
    for m in re.finditer(rb"\x17\x0d(\d{12})Z|\x18\x0f(\d{14})Z", der):
        raw = (m.group(1) or m.group(2)).decode()
        try:
            if len(raw) == 12:
                yy = int(raw[:2])
                raw = ("20" if yy < 50 else "19") + raw
            times.append(_dt.datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=_dt.timezone.utc))
        except ValueError:
            continue
    if len(times) >= 2:
        info["not_after"] = times[1].isoformat()
        info["expired"] = times[1] < _dt.datetime.now(_dt.timezone.utc)
    return info


# --------------------------------------------------------------------------- redaction

_SECRET_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"),
     "[REDACTED PRIVATE KEY]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}"), "AIza…[REDACTED GOOGLE API KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"), "[REDACTED GITHUB TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "[REDACTED GITHUB TOKEN]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "[REDACTED SLACK TOKEN]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED AWS KEY]"),
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}"), "[REDACTED API KEY]"),
    (re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}"), "[REDACTED STRIPE KEY]"),
    (re.compile(r"\bsntry[su]_[A-Za-z0-9_=+/-]{20,}"), "[REDACTED SENTRY TOKEN]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[REDACTED JWT]"),
]

SENSITIVE_KEY_RE = re.compile(
    r"(secret|passw(or)?d|token|private[_-]?key|api[_-]?key|client[_-]?secret|auth[_-]?key|credential)", re.I)


def redact_text(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def mask(value: str) -> str:
    """Keep enough of a value to recognise it, hide the rest."""
    if len(value) <= 8:
        return "[REDACTED]"
    return f"{value[:4]}…[REDACTED, {len(value)} chars]"


def redact_obj(obj: Any, key: Optional[str] = None) -> Any:
    if isinstance(obj, dict):
        return {k: redact_obj(v, str(k)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v, key) for v in obj]
    if isinstance(obj, str):
        if key and SENSITIVE_KEY_RE.search(key):
            return mask(obj)
        return redact_text(obj)
    return obj


# --------------------------------------------------------------------------- read-only subprocesses

@dataclass(frozen=True)
class AllowedCommand:
    """One read-only command shape. `prefix` items are literal, or "*" for one non-flag value.
    After the prefix, only `options` (each followed by one non-flag value), bare `flags`, and up
    to `positionals` non-flag arguments may appear, so no other flag can be smuggled in."""
    prefix: Tuple[str, ...]
    options: frozenset = frozenset()
    flags: frozenset = frozenset()
    positionals: int = 0


# Every external command any script may run. Nothing here builds, signs, installs, erases,
# resolves packages or writes project files.
ALLOWED_COMMANDS: Tuple[AllowedCommand, ...] = (
    AllowedCommand(("sw_vers",)),
    AllowedCommand(("xcode-select", "-p")),
    AllowedCommand(("xcodebuild", "-version")),
    AllowedCommand(("swift", "--version")),
    AllowedCommand(("xcodebuild", "-list", "-json", "-disableAutomaticPackageResolution"),
                   options=frozenset({"-project", "-workspace"})),
    AllowedCommand(("xcodebuild", "-showBuildSettings", "-json", "-disableAutomaticPackageResolution"),
                   options=frozenset({"-project", "-workspace", "-scheme", "-target", "-configuration"})),
    AllowedCommand(("xcrun", "simctl", "list", "devices", "--json")),
    AllowedCommand(("xcrun", "simctl", "spawn", "*", "log", "show"),
                   options=frozenset({"--style", "--last", "--predicate"}), flags=frozenset({"--info", "--debug"})),
    AllowedCommand(("xcrun", "devicectl", "list", "devices"), options=frozenset({"--json-output"})),
    AllowedCommand(("codesign", "-d", "--entitlements", "-", "--xml"), positionals=1),
    AllowedCommand(("security", "find-identity", "-p", "codesigning")),
)


def _is_value(arg: str) -> bool:
    return bool(arg) and not arg.startswith("-")


def command_allowed(argv: Sequence[str]) -> bool:
    argv = list(argv)
    for cmd in ALLOWED_COMMANDS:
        n = len(cmd.prefix)
        if len(argv) < n or not all(p == a if p != "*" else _is_value(a) for p, a in zip(cmd.prefix, argv)):
            continue
        rest, positionals, ok = argv[n:], 0, True
        i = 0
        while i < len(rest):
            arg = rest[i]
            if arg in cmd.flags:
                i += 1
            elif arg in cmd.options and i + 1 < len(rest) and _is_value(rest[i + 1]):
                i += 2
            elif _is_value(arg) and positionals < cmd.positionals:
                positionals += 1
                i += 1
            else:
                ok = False
                break
        if ok:
            return True
    return False


def run_command(argv: Sequence[str], timeout: int = 60) -> Tuple[int, str, str]:
    """Run an allow-listed, read-only command without a shell. Returns (status, stdout, stderr)."""
    argv = list(argv)
    if not command_allowed(argv):
        raise UsageError(f"refusing to run non-allow-listed command: {' '.join(argv[:4])}")
    if shutil.which(argv[0]) is None:
        raise UsageError(f"`{argv[0]}` not found; this check needs macOS with Xcode installed")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, errors="replace")
    except subprocess.TimeoutExpired as exc:
        raise UsageError(f"`{' '.join(argv[:3])}` timed out after {timeout}s") from exc
    except OSError as exc:
        raise UsageError(f"cannot run {argv[0]}: {exc.strerror}") from exc
    return proc.returncode, proc.stdout, proc.stderr


# --------------------------------------------------------------------------- Xcode project files

def find_xcodeprojs(root: str, max_depth: int = 4) -> List[str]:
    found = []
    root = os.path.abspath(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, _ in os.walk(root):
        if dirpath.count(os.sep) - base_depth >= max_depth:
            dirnames[:] = []
        keep = []
        for d in sorted(dirnames):
            if d.endswith(".xcodeproj"):
                if os.path.isfile(os.path.join(dirpath, d, "project.pbxproj")) and \
                        path_allowed(os.path.join(dirpath, d, "project.pbxproj")):
                    found.append(os.path.join(dirpath, d))
            elif d not in SKIP_DIRS and not d.startswith("."):
                keep.append(d)
        dirnames[:] = keep
    return found


def resolve_pbxproj(path: str) -> str:
    if os.path.isdir(path):
        path = os.path.join(path, "project.pbxproj")
    if not os.path.isfile(path):
        raise UsageError(f"not an Xcode project (expected .xcodeproj or project.pbxproj): {path}")
    return path


_SETTING_LINE = re.compile(r'^\s*"?([A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?)"?\s*=\s*(.*?)\s*;\s*$')
_ARRAY_START = re.compile(r'^\s*"?([A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?)"?\s*=\s*\(\s*$')


def _unquote(value: str) -> str:
    value = value.strip().rstrip(",").strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1].replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
    return value


def read_build_settings(path: str) -> Dict[str, List[str]]:
    """Flatten every `buildSettings` block of a project.pbxproj into {KEY: [distinct values]}.

    Values from all targets/configurations are merged; conditional keys such as
    `CODE_SIGN_IDENTITY[sdk=iphoneos*]` are kept verbatim.
    """
    text = read_text(resolve_pbxproj(path))
    settings: Dict[str, List[str]] = {}

    def add(key: str, value: str) -> None:
        bucket = settings.setdefault(key, [])
        if value not in bucket:
            bucket.append(value)

    for m in re.finditer(r"buildSettings\s*=\s*\{", text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        array_key: Optional[str] = None
        array_vals: List[str] = []
        for line in text[m.end():i - 1].splitlines():
            if array_key is not None:
                if line.strip().startswith(");"):
                    add(array_key, " ".join(array_vals))
                    array_key, array_vals = None, []
                elif line.strip():
                    array_vals.append(_unquote(line))
                continue
            am = _ARRAY_START.match(line)
            if am:
                array_key = am.group(1)
                continue
            sm = _SETTING_LINE.match(line)
            if sm:
                add(sm.group(1), _unquote(sm.group(2)))
    return settings


_XCCONFIG_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?)\s*=\s*(.*?)\s*;?\s*$")


def read_xcconfig_settings(root: str) -> Dict[str, List[str]]:
    """Settings from every .xcconfig under `root`. Comments and #include lines are skipped; like
    read_build_settings, values from all files are merged into {KEY: [distinct values]}."""
    settings: Dict[str, List[str]] = {}
    for dirpath, _, files in walk(root):
        for f in files:
            if not f.endswith(".xcconfig"):
                continue
            for line in read_text(os.path.join(dirpath, f)).splitlines():
                line = line.split("//", 1)[0]
                m = _XCCONFIG_LINE.match(line)
                if m and not line.lstrip().startswith("#"):
                    bucket = settings.setdefault(m.group(1), [])
                    if m.group(2) not in bucket:
                        bucket.append(m.group(2))
    return settings


def project_dir(path: str) -> str:
    """Directory containing the .xcodeproj for a .xcodeproj or project.pbxproj path."""
    pbx = resolve_pbxproj(path)
    return os.path.dirname(os.path.dirname(os.path.abspath(pbx)))


def setting_values(settings: Dict[str, List[str]], key: str) -> List[str]:
    """Values for `key` including conditional variants like `KEY[sdk=iphoneos*]`."""
    values: List[str] = []
    for k, vs in settings.items():
        if k == key or k.startswith(key + "["):
            values.extend(v for v in vs if v not in values)
    return values
