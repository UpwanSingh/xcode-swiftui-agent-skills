# Security policy

## Design guarantees

- **Read-only.** Scripts and MCP tools only read files and run allow-listed inspection commands.
  `shared/_common.py: ALLOWED_COMMANDS` lists each command's exact shape, not just a prefix, so an extra
  flag cannot be added: `sw_vers`, `xcode-select -p`, `swift --version`, `xcodebuild -version`,
  `xcodebuild -list`/`-showBuildSettings -json -disableAutomaticPackageResolution`, `simctl list devices --json`,
  `simctl spawn <device> log show`, `devicectl list devices`, `codesign -d --entitlements - --xml <app>` and
  `security find-identity -p codesigning`. Nothing builds, signs, installs, erases, uploads, deploys or
  resolves Swift packages. Incidental writes:
  - `devicectl` writes its JSON output to a temp file, which is deleted right away.
  - When an `xcodebuild` query fails, `xcodebuild` saves a result bundle in the system temp directory.
  - `xcodebuild` may create empty Xcode metadata folders (`xcshareddata/swiftpm`, `.swiftpm/xcode`) and a
    DerivedData entry for the project it inspects.
- **No shell.** Commands run as argument vectors with timeouts. Log predicates are assembled from
  validated values; raw predicates are rejected.
- **Path policy (MCP).** Tools read only inside the project roots and Xcode's diagnostic folders.
  - **Project roots:** `IOS_DEBUG_MCP_ROOTS`, else the client's MCP roots, else the working directory. The
    server refuses to fall back to `/`, your home folder, or a folder that contains your home folder.
  - **Diagnostic folders:** DerivedData, Archives, DiagnosticReports and provisioning profiles. These cover
    every project on the machine, not only the current one. Disable them with `IOS_DEBUG_MCP_SYSTEM_DIRS=0`.
  - **Symlinks:** every path is symlink-resolved before the check. That includes path arguments, every file
    a tool reaches by walking a directory, and every file it derives from another (for example
    `App.app/Info.plist`). A symlink committed inside a project therefore cannot pull in a file outside the
    roots. Run directly from the command line, outside the MCP server, the scripts follow symlinks like any
    other local tool.
- **Credential files are refused:** `.p12/.pfx/.p8/.pem/.key/.jks`, keychains, `.env*`, `.netrc`, SSH keys,
  `AuthKey_*`, `credentials.json`, service-account and `*-firebase-adminsdk-*.json`, and anything under
  `.ssh`, `.gnupg`, `.aws`, `Keychains` or `.git`. Folder names are matched case-insensitively, as on APFS.
- **Redaction.** Tool output is scrubbed for private keys, Google API keys, GitHub, Slack, AWS, OpenAI,
  Anthropic, Stripe and Sentry tokens, and JWTs. Plist and build-setting values under secret-looking keys
  (`*TOKEN*`, `*SECRET*`, `*API_KEY*`, ...) are masked. Provisioning profiles are reduced to certificate
  fingerprints.
- **Bounded output.** Responses are truncated (`IOS_DEBUG_MCP_MAX_OUTPUT`, default 60 000 characters).

Redaction is best effort. Crash reports and logs can still contain personal data such as user names
in paths or device names. Treat tool output as sensitive and don't paste it into public issues unedited.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private vulnerability reporting (repository
**Security** tab → *Report a vulnerability*). Include the version/commit, the tool or script, and a minimal
reproduction. Expect an acknowledgement within 7 days.

In scope: path-policy bypasses, a command that escapes the allow-list, secret leakage through tool output,
and argument injection into subprocesses.
