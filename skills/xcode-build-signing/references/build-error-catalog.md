# Build error catalog

`extract_build_errors.py` tags each error with one of these ids. Patterns are matched
case-insensitively against the message, and the first match wins.

## Contents
- Signing: SIGN001–SIGN006
- Provisioning: PROV001–PROV007
- Info.plist: PLIST001–PLIST004
- Resources: RES001–RES003
- Script phases: SCRIPT001–SCRIPT002
- Dependencies: POD001, SPM001–SPM002, MOD001–MOD003, DEP001
- Linker: LINK001–LINK003
- Destinations & schemes: DEST001, SCHEME001
- Swift: SWIFT001–SWIFT003
- Export: EXPORT001

## Signing

| Id | Message contains | Cause | Fix |
|---|---|---|---|
| SIGN001 | `Signing for "X" requires a development team` | `DEVELOPMENT_TEAM` empty for this target/config | Set the team; for CI pass `DEVELOPMENT_TEAM=…` |
| SIGN002 | `No signing certificate "…" found` / `No certificate for team` | No identity of that type in the keychain | Download it (Xcode > Settings > Accounts) or import the `.p12` |
| SIGN003 | `errSecInternalComponent`, `User interaction is not allowed` | Keychain locked or key ACL prompts (CI) | `security unlock-keychain`; `security set-key-partition-list -S apple-tool:,apple: …` |
| SIGN004 | `identity … is no longer valid`, certificate expired/revoked | Certificate expired or revoked | New certificate, then regenerate profiles |
| SIGN005 | `is automatically signed, but provisioning profile … has been manually specified` | Specifier set while automatic signing is on | Clear `PROVISIONING_PROFILE_SPECIFIER`, or go manual |
| SIGN006 | `Command CodeSign failed with a nonzero exit code` | Summary line: read the codesign output above | xattr junk (`resource fork, Finder information, or similar detritus not allowed`), unsigned embedded framework, locked keychain |

## Provisioning

| Id | Message contains | Fix |
|---|---|---|
| PROV001 | `No profiles for 'id' were found` | Automatic signing + `-allowProvisioningUpdates`, or create a profile for the exact id and type |
| PROV002 | `doesn't include signing certificate` | Regenerate the profile including your cert, or install a cert it lists (`inspect_signing.py profile --check-keychain`) |
| PROV003 | `doesn't support/include the X capability/entitlement` | Enable the capability on the App ID and regenerate the profile, or remove the entitlement |
| PROV004 | `doesn't match the entitlements file's value for the X entitlement` | `inspect_signing.py compare`, then align the `.entitlements` file |
| PROV005 | `Provisioning profile … has expired` | Regenerate/download |
| PROV006 | `doesn't include the currently selected device` | Register the UDID and regenerate the development/ad hoc profile |
| PROV007 | `Embedded binary's bundle identifier is not prefixed with the parent app's` | Extension ids must be `<app id>.<suffix>` |

## Info.plist

| Id | Message contains | Fix |
|---|---|---|
| PLIST001 | `Cannot code sign because the target does not have an Info.plist`, `Info.plist … not found` | Fix `INFOPLIST_FILE` (relative to `SRCROOT`) or set `GENERATE_INFOPLIST_FILE = YES` |
| PLIST002 | `Multiple commands produce '…/Info.plist'` | Info.plist is in Copy Bundle Resources or in a synchronized folder; remove it from membership |
| PLIST003 | `ITMS-90683`, `Missing purpose string` | Add the named `NS…UsageDescription` (see info-plist-keys.md) |
| PLIST004 | Version string invalid, `ITMS-90060/90061` | Use `1.2.3` for the marketing version and an increasing build number |

## Resources

| Id | Message contains | Fix |
|---|---|---|
| RES001 | `Multiple commands produce '…'` | Two files have the same name in the bundle, or a file is both copied and compiled; rename or remove one |
| RES002 | `Build input file cannot be found` | The file was moved or deleted; fix the reference (red file in the navigator) |
| RES003 | `None of the input catalogs contained a matching … app icon set named` | Fix `ASSETCATALOG_COMPILER_APPICON_NAME` or add the icon set |

## Script phases

| Id | Message | Fix |
|---|---|---|
| SCRIPT001 | `Sandbox: tool(pid) deny(1) file-read/write …` | `ENABLE_USER_SCRIPT_SANDBOXING = YES` blocks undeclared paths. Add input/output file lists, or set NO for the target (CocoaPods' embed scripts often need this) |
| SCRIPT002 | `Command PhaseScriptExecution failed with a nonzero exit code` | The script's own error is printed above (SwiftLint, Crashlytics upload, `pod` embed, …) |

## Dependencies

| Id | Message | Fix |
|---|---|---|
| POD001 | `The sandbox is not in sync with the Podfile.lock` | `pod install`; open the `.xcworkspace` |
| SPM001 | `Could not resolve package dependencies`, tools-version mismatch | Resolve again; check network/credentials for private repos, Package.resolved and Xcode version |
| SPM002 | `Missing package product 'X'` | File > Packages > Reset Package Caches; re-add the product to the target |
| MOD001 | `No such module 'X'` | Link the product to *this* target; resolve packages; build the scheme that includes the dependency; workspace for CocoaPods |
| MOD002 | `has a minimum deployment target of` | Raise `IPHONEOS_DEPLOYMENT_TARGET`, or pin an older dependency |
| MOD003 | `Could not build module`, `module … was built with`, `compiled module was created by a newer compiler` | Clean the build folder and delete the project's DerivedData (ask first) |
| DEP001 | `Cycle in dependencies between targets` / `Cycle inside` | Reorder phases: put "Embed" and output-producing scripts after Copy Bundle Resources, and declare script outputs |

## Linker

| Id | Message | Fix |
|---|---|---|
| LINK001 | `Undefined symbol(s) for architecture` | Add the framework/library to Link Binary With Libraries; check target membership of the implementing file; match slices (simulator vs device, arm64 vs x86_64) |
| LINK002 | `duplicate symbol` | The same code is linked twice (a static library in two targets, or `-all_load`) |
| LINK003 | `linker command failed with exit code 1` | Summary only; read the `ld:` lines above |

## Destinations and schemes

| Id | Message | Fix |
|---|---|---|
| DEST001 | `Unable to find a destination matching …` | Install the runtime (Xcode > Settings > Components); list with `xcrun simctl list devices` |
| SCHEME001 | `does not contain a scheme named`, `not currently configured for the X action` | Share the scheme (Manage Schemes > Shared); enable the action (Test/Archive) in the scheme |

## Swift

| Id | Message | Fix |
|---|---|---|
| SWIFT001 | `not concurrency-safe`, `non-sendable`, `risks causing data races`, `main actor-isolated … nonisolated` | Use the swiftui-state-layout skill → `references/swift6-concurrency.md` |
| SWIFT002 | `requires that 'X' conform to 'ObservableObject'` | An `@Observable` class used with `@StateObject`/`@ObservedObject`/`@EnvironmentObject` (SUI004) |
| SWIFT003 | `invalid redeclaration of synthesized property '_x'`, `ReferenceWritableKeyPath<…, Published<…>>` | `@Published` (or another wrapper) inside an `@Observable` class (SUI005). The error points into the macro expansion, not your line |

## Export

| Id | Message | Fix |
|---|---|---|
| EXPORT001 | `exportArchive: …`, `IDEDistribution…` | `ExportOptions.plist` disagrees with the archive: `method` (`app-store-connect`, `release-testing`, `debugging`, `enterprise`; older Xcode used `app-store`, `ad-hoc`, `development`), `teamID`, `signingStyle`, `provisioningProfiles` mapping. See `assets/ExportOptions.plist` |

## Reading order for long logs

Both `xcodebuild` output (absolute paths) and plain `swiftc`/`swift build` output (relative paths,
`macro expansion @Macro:3:14` locations, ASCII-art source excerpts) are understood.

1. `** BUILD FAILED **` / `** ARCHIVE FAILED **` status.
2. `The following build commands failed:` tells you which target and phase.
3. The first `error:` *within that target's* output.
4. Ignore warnings until errors are fixed (`--warnings` shows them).
