# ios-debug-mcp

A read-only [Model Context Protocol](https://modelcontextprotocol.io) server that gives coding agents
structured access to Xcode projects, build logs, signing state, simulators, crash reports and
Firebase/MapKit configuration. It is **stdlib-only Python 3.9+**, so there is nothing to install.

```bash
python3 mcp-server/server.py               # speaks MCP over stdio
python3 mcp-server/server.py --list-tools  # sanity check
python3 mcp-server/server.py --call extract_build_errors --args '{"path": "build.log"}'   # one-shot, no client
```

Every tool is a thin, path-checked wrapper over a script in `skills/*/scripts/`, so the CLI, the skills
and the MCP server always produce the same results.

## Tools

| Tool | What it returns | Runs |
|---|---|---|
| `xcode_environment` | macOS/Xcode/Swift versions, developer dir, setup hints | `sw_vers`, `xcode-select -p`, `xcodebuild -version`, `swift --version` |
| `find_xcode_projects` | projects, workspaces, Package.swift, Info.plist, entitlements, Firebase files | file walk |
| `list_schemes` | schemes, targets, configurations | `xcodebuild -list -json` |
| `show_build_settings` | signing/bundle/Info.plist/Swift settings per target | `xcodebuild -showBuildSettings -json -disableAutomaticPackageResolution` |
| `find_build_logs` | newest `.xcactivitylog` files | DerivedData walk |
| `extract_build_errors` | classified, de-duplicated errors with fix hints | parse |
| `read_plist` | Info.plist / entitlements / xcprivacy / profile as JSON (secrets masked) | parse |
| `check_info_plist` | PL001–PL010 findings | parse + source scan |
| `check_bundle_resources` | BR001–BR007 findings | source scan (+ .app walk) |
| `inspect_provisioning_profile` | team, App ID, type, expiry, entitlements, cert SHA-1s | parse (+ `security find-identity`) |
| `list_provisioning_profiles` | installed profiles (optionally matching a bundle id) | parse |
| `list_signing_identities` | keychain code-signing identities (name, SHA-1, status) | `security find-identity -p codesigning` |
| `compare_entitlements` | SIG001–SIG007 (requested vs granted entitlements) | parse (+ `codesign -d`) |
| `swiftui_lint` | SUI001–SUI013 | source scan |
| `swift_concurrency_check` | SC001–SC010 + language mode / default isolation | source + project scan |
| `check_firebase_config` | FB001–FB012 (API keys masked) | parse + source scan |
| `lint_firebase_rules` | FR001–FR008 | parse + source scan |
| `mapkit_check` | MK001–MK011 | source scan |
| `list_simulators` | simulators with state/runtime/UDID | `xcrun simctl list devices --json` |
| `list_devices` | paired devices | `xcrun devicectl list devices` |
| `simulator_logs` | filtered unified-log lines + classified runtime warnings | `xcrun simctl spawn <udid> log show` |
| `scan_runtime_log` | RW001–RW025 matches in a saved log | parse |
| `list_crash_reports` | newest .ips/.crash reports | file walk |
| `summarize_crash_report` | exception, frames, diagnosis, atos commands | parse |

All tools are annotated `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `IOS_DEBUG_MCP_ROOTS` | *(client roots, else cwd)* | `:`-separated directories tools may read |
| `IOS_DEBUG_MCP_SYSTEM_DIRS` | `1` | Also allow Xcode's DerivedData, Archives, DiagnosticReports, provisioning-profile folders. Set `0` to disable |
| `IOS_DEBUG_MCP_MAX_OUTPUT` | `60000` | Max characters per tool response |
| `IOS_DEBUG_MCP_LOG` | `WARNING` | stderr log level (`INFO` shows initialize/roots) |
| `IOS_DEBUG_SKILLS_DIR` | `../skills` | Where the skill scripts live (only needed if you move the server) |

**Roots.** If the client supports MCP roots (VS Code, Claude Code and others), the server asks for them
after initialization and follows `roots/list_changed`. `IOS_DEBUG_MCP_ROOTS` overrides the client.
Relative tool paths resolve against the first root.

## Client setup

Use an **absolute** path to `server.py`. GUI clients often have a minimal `PATH`, so use an absolute
interpreter path (`/usr/bin/python3`, or `/opt/homebrew/bin/python3`) if `python3` isn't found.

<details><summary>Claude Code</summary>

```bash
claude mcp add ios-debug -- python3 /ABS/PATH/xcode-swiftui-agent-skills/mcp-server/server.py
# or pin the readable project:
claude mcp add ios-debug -e IOS_DEBUG_MCP_ROOTS=/path/to/MyApp -- python3 /ABS/PATH/mcp-server/server.py
```
Installing the repo as a Claude Code plugin registers the server automatically (see the root README).
</details>

<details><summary>Claude Desktop</summary>

`~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{ "mcpServers": { "ios-debug": {
    "command": "/usr/bin/python3",
    "args": ["/ABS/PATH/mcp-server/server.py"],
    "env": { "IOS_DEBUG_MCP_ROOTS": "/path/to/MyApp" } } } }
```
</details>

<details><summary>Cursor</summary>

`.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global):
```json
{ "mcpServers": { "ios-debug": {
    "command": "python3",
    "args": ["/ABS/PATH/mcp-server/server.py"],
    "env": { "IOS_DEBUG_MCP_ROOTS": "/path/to/MyApp" } } } }
```
</details>

<details><summary>VS Code (GitHub Copilot agent mode)</summary>

`.vscode/mcp.json`:
```json
{ "servers": { "ios-debug": {
    "type": "stdio",
    "command": "python3",
    "args": ["/ABS/PATH/mcp-server/server.py"] } } }
```
VS Code provides workspace roots, so no `IOS_DEBUG_MCP_ROOTS` is needed.
</details>

<details><summary>OpenAI Codex CLI</summary>

`~/.codex/config.toml`:
```toml
[mcp_servers.ios-debug]
command = "python3"
args = ["/ABS/PATH/mcp-server/server.py"]
env = { IOS_DEBUG_MCP_ROOTS = "/path/to/MyApp" }
```
</details>

<details><summary>Gemini CLI</summary>

`~/.gemini/settings.json` or `.gemini/settings.json`:
```json
{ "mcpServers": { "ios-debug": {
    "command": "python3",
    "args": ["/ABS/PATH/mcp-server/server.py"],
    "env": { "IOS_DEBUG_MCP_ROOTS": "/path/to/MyApp" } } } }
```
</details>

## Testing

```bash
python3 -m unittest tests.test_mcp_server -v                       # spawns the server, speaks JSON-RPC
npx @modelcontextprotocol/inspector python3 mcp-server/server.py   # interactive inspector (needs Node)
```

Hand-rolled session:

```bash
cd tests/fixtures/BuggyApp
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{}}}' \
 '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"lint_firebase_rules","arguments":{}}}' \
 | python3 ../../../mcp-server/server.py
```

## Protocol notes

- Transport: stdio, newline-delimited JSON-RPC 2.0. Logs go to stderr only.
- Protocol versions: `2025-11-25`, `2025-06-18`, `2025-03-26`, `2024-11-05`. The server echoes the client's
  version when supported, otherwise it answers with the newest.
- Capabilities: `tools` (static list). `resources/list` and `prompts/list` return empty lists for lenient clients.
- Errors: unknown tool → JSON-RPC `-32602`. Invalid arguments, policy violations and missing Xcode →
  `isError: true` results with a readable message, so the model can correct itself.

## Security model

See [SECURITY.md](../SECURITY.md): an allow-listed read-only command set, no shell, a path policy with symlink
resolution, a credential-file denylist, output redaction and size limits.
