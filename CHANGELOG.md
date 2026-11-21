# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-21

### Added
- `swiftui-state-layout` skill: `swiftui_lint.py` (SUI001–SUI013) and `concurrency_check.py` (SC001–SC010,
  language-mode/default-isolation detection), references for Observation, Swift 6 concurrency, navigation and layout.
- `xcode-build-signing` skill: `extract_build_errors.py` (38 classified error patterns, .xcactivitylog support),
  `check_plist.py` (PL001–PL010, generated Info.plist aware), `inspect_signing.py` (profiles, identities,
  entitlement comparison), `check_bundle_resources.py` (BR001–BR007), `xcode_env.py`.
- `ios-map-firebase-diagnostics` skill: `mapkit_check.py` (MK001–MK011), `check_firebase_config.py`
  (FB001–FB012), `lint_firebase_rules.py` (FR001–FR008), secure rules templates and Swift reference code.
- `ios-crash-log-triage` skill: `crash_report.py` (.ips/.crash summaries, diagnosis, atos commands) and
  `sim_logs.py` (simulators, devices, safe log queries, RW001–RW025 runtime warning classifier).
- Read-only, stdlib-only MCP server with 24 tools, client-roots support, path policy, credential-file
  denylist and output redaction.
- Claude Code plugin/marketplace manifests, a multi-agent `install.sh`, CI on Linux and macOS, and evaluation scenarios.

### Fixed (pre-release review of PR #3)
- **MCP path policy:**
  - Files reached by walking a directory, or derived from another path, now pass the same symlink-resolved
    check as path arguments (`PATH_GUARD`).
  - The server refuses a working-directory root of `/`, your home folder or one of its ancestors.
  - Protected folder names are matched case-insensitively.
- **Read-only guarantee:**
  - `list_schemes` no longer resolves Swift packages.
  - The command allow-list matches exact command shapes instead of prefixes.
  - `show_build_settings` masks secrets by key name.
- **Build logs:**
  - `extract_build_errors` streams logs with no size cap, flags truncated or corrupt gzip input instead of
    reporting UNKNOWN, and clips pathological lines.
  - `EXPORT001` is now matched before the generic signing rules.
  - `find_build_logs` returns Build logs by default.
- **Crash reports:** `list_crash_reports` reads only report headers and skips unreadable files, and the
  near-null diagnosis matches 16-digit addresses.
- **False positives and misses:**
  - MK001 no longer fires on a `lazy var` closure.
  - MK002/MK004 read the target's Info.plist when it isn't generated.
  - Default actor isolation is resolved per Swift package.
  - Compiled (`.momd`, `.storyboardc`, `.nib`) and localized (`.lproj`) resources are recognised.
  - Unexpanded `$(VAR)` entitlement values match the profile.
  - `.xcconfig` settings count as defined.
  - Swift raw strings are stripped.
- **Robustness:**
  - The JSON-RPC loop survives deeply nested input, lone surrogates and non-object params.
  - A missing skills folder is reported clearly.
  - The installer only removes folders it installed.
  - `ci-signing.sh` can be re-run safely.
  - CI jobs have timeouts.
- **Tests:** 58 → 99. They now cover:
  - every build-error and runtime-warning classifier
  - each path-policy guard
  - symlink escape
  - tools leaving the tree unchanged
  - server-level redaction and truncation
  - the cases above
