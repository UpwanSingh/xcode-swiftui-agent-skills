# Security policy

## Design guarantees

- **Read-only.** Scripts and MCP tools only read files and run allow-listed inspection commands
  (`shared/_common.py: ALLOWED_COMMANDS`: `simctl list`, `simctl spawn … log show`, `devicectl list devices`,
  `xcodebuild -list/-showBuildSettings/-version`, `codesign -d`, `security find-identity`, `sw_vers`,
  `xcode-select -p`, `swift --version`). Nothing builds, signs, installs, erases, uploads or deploys.
  Incidental writes are limited to the system temp directory: `devicectl`'s JSON output (deleted right away)
  and the result bundle `xcodebuild` itself saves there when a query fails.
- **No shell.** Commands run as argument vectors with timeouts. Log predicates are assembled from
  validated values; raw predicates are rejected.
- **Path policy (MCP).** Tools read only inside the project roots (`IOS_DEBUG_MCP_ROOTS`, MCP client roots,
  or the working directory) and Xcode's diagnostic folders (DerivedData, Archives, DiagnosticReports,
  provisioning profiles). Disable the latter with `IOS_DEBUG_MCP_SYSTEM_DIRS=0`. Symlinks are resolved
  before the check.
- **Credential files are refused:** `.p12/.pfx/.p8/.pem/.key`, keychains, `.env*`, `.netrc`, SSH keys,
  `AuthKey_*`, `credentials.json`, service-account and `*-firebase-adminsdk-*.json`, and anything under
  `.ssh`, `.gnupg`, `.aws`, `Keychains` or `.git`.
- **Redaction.** Tool output is scrubbed for private keys, Google API keys, GitHub/Slack/AWS/OpenAI-style
  tokens and JWTs. Plist values under secret-looking keys are masked. Provisioning profiles are reduced
  to certificate fingerprints.
- **Bounded output.** Responses are truncated (`IOS_DEBUG_MCP_MAX_OUTPUT`, default 60 000 characters).

Redaction is best effort. Crash reports and logs can still contain personal data such as user names
in paths or device names. Treat tool output as sensitive and don't paste it into public issues unedited.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private vulnerability reporting (repository
**Security** tab → *Report a vulnerability*). Include the version/commit, the tool or script, and a minimal
reproduction. Expect an acknowledgement within 7 days.

In scope: path-policy bypasses, a command that escapes the allow-list, secret leakage through tool output,
and argument injection into subprocesses.
