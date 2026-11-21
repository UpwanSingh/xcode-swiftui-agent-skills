# iOS Debug Pack: Agent Skills + MCP server for SwiftUI & Xcode

[![CI](https://github.com/UpwanSingh/xcode-swiftui-agent-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/UpwanSingh/xcode-swiftui-agent-skills/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-informational)
![Dependencies: none](https://img.shields.io/badge/dependencies-none-success)

Four focused [Agent Skills](https://agentskills.io) (`SKILL.md` + `scripts/` + `references/` + `assets/`)
and a read-only [MCP](https://modelcontextprotocol.io) server. Together they teach coding agents (Claude
Code, Cursor, Codex, VS Code/Copilot, Gemini CLI, and any MCP client) to debug iOS apps **from evidence
instead of guesswork**.

| Skill | Debugs |
|---|---|
| [`swiftui-state-layout`](skills/swiftui-state-layout/SKILL.md) | Re-render loops, lost state, `@State`/`@Binding`/`@Observable` misuse, Swift 6 actor isolation, NavigationStack/TabView title glitches, GeometryReader loops |
| [`xcode-build-signing`](skills/xcode-build-signing/SKILL.md) | `xcodebuild`/archive errors, provisioning & certificate mismatches, entitlements, Info.plist & privacy keys, nil bundle resources |
| [`ios-map-firebase-diagnostics`](skills/ios-map-firebase-diagnostics/SKILL.md) | MapKit regions/annotations/permissions, Core Location, Firebase init order, GoogleService-Info, Auth URL schemes, Firestore/Storage rules |
| [`ios-crash-log-triage`](skills/ios-crash-log-triage/SKILL.md) | `.ips`/`.crash` summaries and symbolication, watchdog/jetsam, simulator logs, runtime-warning classification |

---

## The problem

iOS failures are noisy and indirect. An archive log is 20 000 lines, and the real error is line 14 812.
"Doesn't include signing certificate" depends on four artefacts in three places. A SwiftUI loop shows up as a
hot CPU with no stack trace. Firestore says only "Missing or insufficient permissions". Agents without
domain tooling tend to pattern-match: they toggle signing settings, delete DerivedData, add `.id(UUID())`,
or loosen security rules to `if true`.

This pack gives the agent **deterministic extractors and checkers** (which return the same output every run
and cite rule ids and file:line) plus **step-by-step workflows** that say what to collect, how to read it and
what not to do.

### Before / after

> *"The archive fails. Fix it."* (with a 2 000-line `xcodebuild archive` log)

**Before (no skill):** the agent skims the tail of the log, sees `linker command failed` and
`PhaseScriptExecution failed`, then flips `ENABLE_USER_SCRIPT_SANDBOXING`, cleans DerivedData, retries, and
asks for your Apple ID password. Three rounds later the signing errors are still there.

**After (with `xcode-build-signing`):** the agent runs the extractor and reads the root causes first:

```text
$ python3 skills/xcode-build-signing/scripts/extract_build_errors.py build.log
Build status: ARCHIVE FAILED
Errors: 10 unique   Warnings: 1 (hidden; use --warnings)
Categories: concurrency=1, info-plist=1, linker=2, module=1, provisioning=2, script-phase=2, swiftui=1

/Users/dev/BuggyApp/App/Services.swift:10: error: [concurrency/SWIFT001] static property 'shared' is not concurrency-safe … (x2)
    hint: Swift 6 data-race diagnostic. See the swiftui-state-layout skill …
error: [provisioning/PROV002] Provisioning profile "BuggyApp Dev" doesn't include signing certificate "Apple Development: Jane Dev (XYZ9876543)".
    hint: The profile was generated for a different certificate. Regenerate the profile including your cert, or sign with an identity the profile lists …
error: [script-phase/SCRIPT001] Sandbox: rsync(12345) deny(1) file-write-create …/BuggyApp.app/Frameworks/Lib.framework
    hint: ENABLE_USER_SCRIPT_SANDBOXING blocked a Run Script phase. Declare its input/output files …
…
```

It then checks the signing chain and stops at the actual mismatch:

```text
$ python3 skills/xcode-build-signing/scripts/inspect_signing.py compare \
      --entitlements App/App.entitlements --profile BuggyApp_Dev.mobileprovision --bundle-id com.example.buggy
App/App.entitlements: error [SIG006] com.apple.security.application-groups (App Groups) is requested but the profile does not grant it
    fix: Enable App Groups for the App ID and regenerate the profile, or remove the entitlement.
App/App.entitlements: error [SIG007] aps-environment: requested 'production' but profile grants 'development'
    fix: Xcode rewrites aps-environment for the profile type; a mismatch usually means manual signing with the wrong … profile.
```

Result: three precise fixes (enable App Groups on the App ID, use the distribution profile, declare the
script phase outputs), no credentials requested, and nothing deleted.

The same pattern applies elsewhere. `swiftui_lint.py` points at the `.id(UUID())` that resets a list.
`lint_firebase_rules.py` finds the test-mode rule that expired this morning. `crash_report.py summary`
turns an `.ips` into "force-unwrap at ContentView.swift:42" plus a ready-to-run `atos` line.

---

## Quickstart

**Requirements:** Python 3.9+ (the macOS system `python3` is fine) and nothing else. Static checks run on any
OS. Live tools (`xcodebuild`, `simctl`, `devicectl`, `codesign`, keychain) need macOS with Xcode 15+.

> If `python3` prints *"You have not agreed to the Xcode license agreements"*, run
> `sudo xcodebuild -license accept` once. The system Python shim refuses to run until you do.

```bash
git clone https://github.com/UpwanSingh/xcode-swiftui-agent-skills.git
cd xcode-swiftui-agent-skills
python3 -m unittest discover -s tests      # 58 tests, ~2 s: confirms everything works on your machine
```

### 1. Install the skills

**Claude Code (plugin: skills and MCP server in one step)**
```text
/plugin marketplace add UpwanSingh/xcode-swiftui-agent-skills
/plugin install ios-debug-pack@xcode-swiftui-agent-skills
```

**Any skills-compatible agent (installer)**
```bash
./install.sh                                   # Claude Code, user scope  → ~/.claude/skills
./install.sh --agent codex                     # OpenAI Codex             → ~/.codex/skills
./install.sh --agent cursor --scope project --project-dir ~/src/MyApp   # → MyApp/.cursor/skills
./install.sh --agent vscode --scope project --project-dir ~/src/MyApp   # → MyApp/.github/skills
./install.sh --agent gemini                    # Gemini CLI               → ~/.gemini/skills
./install.sh --dest /any/skills/dir --link     # anything else; symlink keeps it updated via git pull
```
Skill directories differ between agents and versions. If yours uses another location, pass `--dest`.
Each skill folder is self-contained, so copying `skills/<name>/` by hand also works.

### 2. Add the MCP server (optional, recommended)

```bash
claude mcp add ios-debug -- python3 "$PWD/mcp-server/server.py"      # Claude Code (not needed with the plugin)
```

```jsonc
// Cursor (.cursor/mcp.json), Claude Desktop, Gemini CLI (settings.json): "mcpServers"
{ "mcpServers": { "ios-debug": { "command": "python3",
  "args": ["/ABS/PATH/mcp-server/server.py"], "env": { "IOS_DEBUG_MCP_ROOTS": "/path/to/MyApp" } } } }

// VS Code (.vscode/mcp.json): "servers"
{ "servers": { "ios-debug": { "type": "stdio", "command": "python3", "args": ["/ABS/PATH/mcp-server/server.py"] } } }
```

```toml
# OpenAI Codex (~/.codex/config.toml)
[mcp_servers.ios-debug]
command = "python3"
args = ["/ABS/PATH/mcp-server/server.py"]
env = { IOS_DEBUG_MCP_ROOTS = "/path/to/MyApp" }
```

Full per-client details, environment variables and the tool list are in [mcp-server/README.md](mcp-server/README.md).

### 3. Ask your agent

- "The archive fails; here's `build.log`. What's the root cause?"
- "My list resets its scroll position every time I type in the search field."
- "Since today every Firestore read returns PERMISSION_DENIED."
- "The app was killed at launch on TestFlight. Summarise the newest crash report."

You can also run any script directly. Each one has `--help`, `--json`, `--list-rules` and compiler-style output:

```bash
python3 skills/swiftui-state-layout/scripts/swiftui_lint.py MyApp/ --fail-on warning   # CI gate
python3 skills/ios-map-firebase-diagnostics/scripts/check_firebase_config.py --root MyApp
python3 skills/ios-crash-log-triage/scripts/crash_report.py list --app MyApp
```

---

## What's inside

| Skill | Scripts | Rules |
|---|---|---|
| swiftui-state-layout | `swiftui_lint.py`, `concurrency_check.py` | SUI001–013, SC001–010 |
| xcode-build-signing | `extract_build_errors.py`, `check_plist.py`, `inspect_signing.py`, `check_bundle_resources.py`, `xcode_env.py` | 37 build patterns, PL001–010, SIG001–007, BR001–007 |
| ios-map-firebase-diagnostics | `mapkit_check.py`, `check_firebase_config.py`, `lint_firebase_rules.py` | MK001–011, FB001–012, FR001–008 |
| ios-crash-log-triage | `crash_report.py`, `sim_logs.py` | 18 crash diagnoses, RW001–025 |

**MCP server:** 24 read-only tools (`extract_build_errors`, `check_info_plist`, `compare_entitlements`,
`swiftui_lint`, `swift_concurrency_check`, `check_firebase_config`, `lint_firebase_rules`, `mapkit_check`,
`simulator_logs`, `summarize_crash_report`, …), each wrapping the same skill scripts.

### Repository layout

```text
.
├── skills/
│   ├── swiftui-state-layout/
│   │   ├── SKILL.md                 # workflows, decision tables, worked examples (<500 lines)
│   │   ├── scripts/                 # swiftui_lint.py, concurrency_check.py, _common.py
│   │   ├── references/              # state-and-observation, swift6-concurrency, navigation-and-layout
│   │   └── assets/                  # DebugHelpers.swift
│   ├── xcode-build-signing/         # build-error-catalog, signing, Info.plist refs; ExportOptions.plist, ci-signing.sh
│   ├── ios-map-firebase-diagnostics/# mapkit/firebase refs; rules templates, FirebaseBootstrap.swift, LocationModel.swift
│   └── ios-crash-log-triage/        # crash-types ref; bug-report template
├── mcp-server/
│   ├── server.py                    # entry point (stdio)
│   └── ios_debug_mcp/               # protocol (server.py), tool registry (tools.py), guardrails (safety.py)
├── shared/_common.py                # canonical helper module, copied into each skill
├── tools/                           # validate_skills.py, sync_common.py
├── tests/                           # unittest suite + fixtures/BuggyApp (a deliberately broken app)
├── evals/scenarios.json             # agent-behaviour scenarios
├── .claude-plugin/                  # Claude Code plugin + marketplace manifests
├── install.sh                       # multi-agent skill installer
└── .github/                         # CI (Linux + macOS, Python 3.9 & 3.12), templates
```

---

## Safety and guardrails

- **Read-only by construction.** External commands go through one allow-list
  (`simctl list`, `simctl spawn … log show`, `devicectl list devices`, `xcodebuild -list/-showBuildSettings/-version`,
  `codesign -d`, `security find-identity`, …). Anything else is refused, even when called from code. Commands run
  without a shell, with timeouts.
- **No hardcoded secrets, and none printed.** Google API keys, private keys, tokens and JWTs are redacted from
  all output. Plist values under secret-looking keys are masked. Provisioning profiles are reduced to certificate
  SHA-1s.
- **MCP path policy.** Reads are limited to project roots (env or MCP client roots) and Xcode's diagnostic
  folders. Symlinks are resolved first. Credential files (`.p12`, `.p8`, keychains, `.env`, service-account
  JSON, SSH keys…) are always refused.
- **Validated inputs.** Unknown tool arguments are rejected. Log predicates are built from validated values;
  raw predicates are never accepted.
- **Skills tell agents what *not* to do.** Don't delete DerivedData or reset simulators without asking, never
  loosen Firebase rules to `if true`, never ask for `.p12` passwords, and don't silence Swift 6 errors with
  `@unchecked Sendable`.
- **Findings are heuristics.** Static checks are regex/structure based and can miss things or misfire. Every
  finding carries a rule id and a fix, and the skills tell the agent to confirm in code before editing.

Details: [SECURITY.md](SECURITY.md).

---

## Testing and validation

```bash
python3 tools/validate_skills.py            # Agent Skills format: frontmatter, name/dir match, <500 lines,
                                            # links resolve, scripts --help, every rule id documented
python3 tools/sync_common.py --check        # helper copies identical across skills
python3 -m unittest discover -s tests -v    # 58 tests: every rule fires on the fixture, negatives stay quiet,
                                            # MCP JSON-RPC end-to-end (roots, path policy, redaction, errors)
python3 mcp-server/server.py --list-tools   # server smoke test
npx @modelcontextprotocol/inspector python3 mcp-server/server.py   # interactive MCP inspector (Node)
```

Try it on the fixture app, a deliberately broken project whose planted bugs are marked with `// RULEID` comments (the remaining rules are covered by synthetic cases in `tests/`):

```bash
python3 skills/swiftui-state-layout/scripts/swiftui_lint.py tests/fixtures/BuggyApp/App
python3 skills/ios-map-firebase-diagnostics/scripts/lint_firebase_rules.py --root tests/fixtures/BuggyApp --sources tests/fixtures/BuggyApp/App
python3 skills/ios-crash-log-triage/scripts/crash_report.py summary tests/fixtures/crashes/BuggyApp-2026-11-20-101500.ips
```

To check agent behaviour (does the right skill trigger, and does the agent follow the workflow and
guardrails?), run the scenarios in [evals/](evals/README.md) with your agent.

---

## Contributing

PRs are welcome, especially new rules backed by real-world bugs. The short version:

1. Keep everything **read-only, stdlib-only and secret-free**.
2. Edit `shared/_common.py`, never the per-skill copies, then run `python3 tools/sync_common.py`.
3. Every new rule needs a severity, a fix text, a fixture line, positive and negative tests, and a row in the
   skill's reference docs (CI enforces the docs part).
4. Keep `SKILL.md` under 500 lines and move depth into `references/`.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full checklist and [CHANGELOG.md](CHANGELOG.md) for releases.

## License

[MIT](LICENSE) © 2026 UpwanSingh
