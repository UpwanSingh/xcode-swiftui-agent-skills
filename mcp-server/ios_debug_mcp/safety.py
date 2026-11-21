"""Guardrails: which paths tools may read, which files are never read, and output limits."""
from __future__ import annotations

import os
import re
from typing import Iterable, List, Optional, Sequence
from urllib.parse import unquote, urlparse

# Directories Xcode writes diagnostics to. They are readable in addition to the project roots
# unless IOS_DEBUG_MCP_SYSTEM_DIRS=0.
SYSTEM_DIRS = [
    "~/Library/Developer/Xcode/DerivedData",
    "~/Library/Developer/Xcode/Archives",
    "~/Library/Developer/Xcode/UserData/Provisioning Profiles",
    "~/Library/MobileDevice/Provisioning Profiles",
    "~/Library/Logs/DiagnosticReports",
    "~/Library/Logs/CrashReporter/MobileDevice",
]

# Files that may hold secrets are refused even inside an allowed root.
DENY_BASENAME = re.compile(
    r"(\.(p12|pfx|p8|pem|key|keychain|keychain-db|jks|keystore|ppk)$)|"
    r"(^\.env(\..+)?$)|(^\.netrc$)|(^id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$)|"
    r"(^AuthKey_.*)|(^credentials(\.json)?$)|(^service[-_]?account.*\.json$)|(^.*-firebase-adminsdk-.*\.json$)",
    re.I)
DENY_COMPONENT = {".ssh", ".gnupg", ".aws", "keychains", ".git"}  # compared case-insensitively (APFS)

MAX_TEXT_INPUT = 4 * 1024 * 1024


class PolicyError(Exception):
    """A request tried to read something outside the policy. Reported to the model as a tool error."""


def file_uri_to_path(uri: str) -> Optional[str]:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    return unquote(parsed.path)


class PathPolicy:
    """Resolves tool path arguments and enforces the read allow-list.

    Roots come from IOS_DEBUG_MCP_ROOTS (os.pathsep-separated) if set, else from the client's
    MCP `roots` (when it supports them), else the server's working directory.
    """

    def __init__(self, env: Optional[dict] = None, cwd: Optional[str] = None):
        env = os.environ if env is None else env
        self._cwd = cwd
        self._home = self._norm(env.get("HOME") or "~")
        raw = env.get("IOS_DEBUG_MCP_ROOTS", "")
        self.env_roots = [self._norm(p) for p in raw.split(os.pathsep) if p.strip()]
        self.client_roots: List[str] = []
        self.include_system = env.get("IOS_DEBUG_MCP_SYSTEM_DIRS", "1") not in ("0", "false", "no")

    @staticmethod
    def _norm(path: str) -> str:
        return os.path.realpath(os.path.abspath(os.path.expanduser(path)))

    def set_client_roots(self, uris: Iterable[str]) -> None:
        self.client_roots = [self._norm(p) for p in (file_uri_to_path(u) for u in uris) if p]

    @property
    def project_roots(self) -> List[str]:
        if self.env_roots or self.client_roots:
            return self.env_roots or self.client_roots
        cwd = self._norm(self._cwd or os.getcwd())
        if cwd == os.sep or cwd == self._home or self._home.startswith(cwd.rstrip(os.sep) + os.sep):
            # A host that launches the server from / or $HOME would otherwise expose that whole tree.
            raise PolicyError(f"no project root: the server was started in {cwd}, which is too broad to "
                              "expose. Ask the user to set IOS_DEBUG_MCP_ROOTS to the project folder or "
                              "to open the project as an MCP root in their client.")
        return [cwd]

    @property
    def allowed_roots(self) -> List[str]:
        roots = list(self.project_roots)
        if self.include_system:
            roots += [self._norm(d) for d in SYSTEM_DIRS]
        return roots

    def default_root(self) -> str:
        return self.project_roots[0]

    def _inside(self, path: str) -> bool:
        for root in self.allowed_roots:
            try:
                if os.path.commonpath([root, path]) == root:
                    return True
            except ValueError:
                continue
        return False

    @staticmethod
    def denied(real: str) -> Optional[str]:
        """The guard that protects `real`, or None. Exposed so tests can assert the exact guard."""
        if any(p.lower() in DENY_COMPONENT for p in real.split(os.sep)):
            return "protected directory"
        if DENY_BASENAME.search(os.path.basename(real)):
            return "credential file name"
        return None

    def allows(self, path: str) -> bool:
        """Read guard for files the skill scripts discover by walking a directory or derive from a
        resolved path. It applies the same rules as resolve(): inside a root, not protected."""
        real = self._norm(path)
        try:
            return self._inside(real) and self.denied(real) is None
        except PolicyError:
            return False

    def resolve(self, path: str, *, kind: str = "any", exts: Optional[Sequence[str]] = None,
                must_exist: bool = True) -> str:
        """Return an absolute, symlink-resolved path or raise PolicyError.

        kind: "file", "dir" or "any". Relative paths resolve against the first project root.
        """
        if not isinstance(path, str) or not path.strip():
            raise PolicyError("path must be a non-empty string")
        expanded = os.path.expanduser(path)
        if not os.path.isabs(expanded):
            expanded = os.path.join(self.default_root(), expanded)
        real = self._norm(expanded)
        if not self._inside(real):
            raise PolicyError(f"{path} is outside the allowed roots ({', '.join(self.project_roots)}). "
                              "Ask the user to set IOS_DEBUG_MCP_ROOTS or open the project as an MCP root.")
        guard = self.denied(real)
        if guard:
            raise PolicyError(f"refusing to read {os.path.basename(real)}: {guard}, it may contain credentials")
        if must_exist and not os.path.exists(real):
            raise PolicyError(f"no such file or directory: {path}")
        if kind == "file" and must_exist and not os.path.isfile(real):
            raise PolicyError(f"expected a file: {path}")
        if kind == "dir" and must_exist and not os.path.isdir(real):
            raise PolicyError(f"expected a directory: {path}")
        if exts and not real.rstrip("/").lower().endswith(tuple(e.lower() for e in exts)):
            raise PolicyError(f"{os.path.basename(real)} is not one of: {', '.join(exts)}")
        return real

    def resolve_many(self, paths: Sequence[str], **kw) -> List[str]:
        if not paths:
            raise PolicyError("at least one path is required")
        return [self.resolve(p, **kw) for p in paths]


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [truncated {len(text) - limit} characters; narrow the request]"
