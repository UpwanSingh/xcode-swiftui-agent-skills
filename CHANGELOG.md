# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-21

### Added
- `swiftui-state-layout` skill: `swiftui_lint.py` (SUI001–SUI013) and `concurrency_check.py` (SC001–SC010,
  language-mode/default-isolation detection), references for Observation, Swift 6 concurrency, navigation and layout.
- `xcode-build-signing` skill: `extract_build_errors.py` (37 classified error patterns, .xcactivitylog support),
  `check_plist.py` (PL001–PL010, generated Info.plist aware), `inspect_signing.py` (profiles, identities,
  entitlement comparison), `check_bundle_resources.py` (BR001–BR007), `xcode_env.py`.
- `ios-map-firebase-diagnostics` skill: `mapkit_check.py` (MK001–MK011), `check_firebase_config.py`
  (FB001–FB012), `lint_firebase_rules.py` (FR001–FR008), secure rules templates and Swift reference code.
- `ios-crash-log-triage` skill: `crash_report.py` (.ips/.crash summaries, diagnosis, atos commands) and
  `sim_logs.py` (simulators, devices, safe log queries, RW001–RW025 runtime warning classifier).
- Read-only, stdlib-only MCP server with 24 tools, client-roots support, path policy, credential-file
  denylist and output redaction.
- Claude Code plugin/marketplace manifests, a multi-agent `install.sh`, CI on Linux and macOS, and evaluation scenarios.
