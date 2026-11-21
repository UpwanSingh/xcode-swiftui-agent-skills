---
name: xcode-build-signing
description: Diagnose Xcode and xcodebuild build, archive and export failures - compiler/linker errors buried in long logs, "No profiles for ... were found", "doesn't include signing certificate", entitlement/capability mismatches, expired or wrong provisioning profiles, missing development team, missing or wrong Info.plist keys and privacy usage descriptions, "Multiple commands produce", sandboxed Run Script failures, SPM/CocoaPods module errors, and bundle resources that are nil at runtime. Use when an Xcode build, archive, TestFlight upload or CI signing step fails, or when Bundle/asset lookups return nil.
license: MIT
compatibility: Scripts need Python 3.9+ (stdlib only). Log, plist, entitlement and resource checks run on any OS; keychain, codesign and xcodebuild queries need macOS with Xcode 15+.
metadata:
  version: "1.0.0"
  author: UpwanSingh
---

# Xcode build, signing and bundle debugging

Rules of thumb:
1. **The first error is the real one.** Later errors are usually cascades.
2. **Signing is a 4-way match:** bundle id ↔ App ID in the profile ↔ certificate in the profile ↔
   identity (cert + private key) in the keychain. Plus: requested entitlements ⊆ profile entitlements.
3. **Read the effective build settings**, not the ones you think you set. Configurations and xcconfig
   files override each other.

## Scripts (all read-only)

```bash
python3 scripts/extract_build_errors.py build.log            # classify + dedupe errors (also .xcactivitylog, or - for stdin)
python3 scripts/check_plist.py App/Info.plist --sources App --project App.xcodeproj
python3 scripts/inspect_signing.py profile X.mobileprovision --check-keychain
python3 scripts/inspect_signing.py list --bundle-id com.acme.app
python3 scripts/inspect_signing.py compare --entitlements App/App.entitlements --profile X.mobileprovision --bundle-id com.acme.app
python3 scripts/inspect_signing.py identities
python3 scripts/check_bundle_resources.py --sources App --app path/to/App.app
python3 scripts/xcode_env.py env | projects | schemes P | settings P --scheme S | logs --project App
```

Exit status: `0` OK, `1` errors found, `2` bad input or missing tool. Every finding has a rule id
and a `fix:` line.

## Workflow 1: a build or archive fails

1. **Capture a complete log.** Xcode's issue navigator truncates; the CLI does not:
   ```bash
   xcodebuild -scheme App -destination 'generic/platform=iOS Simulator' build 2>&1 | tee build.log
   ```
   Or reuse Xcode's last log: `scripts/xcode_env.py logs --project App`, then pass the `.xcactivitylog`.
2. **Extract.** `scripts/extract_build_errors.py build.log`. Read the status line, the categories and the
   **first** error. `Failed commands` tells you which target and phase failed.
3. **Use the category to choose the next step:**

| Category | Next step |
|---|---|
| `signing` / `provisioning` | Workflow 2 |
| `info-plist` | Workflow 3 |
| `resources` | Workflow 4 (or delete the duplicate file named in "Multiple commands produce") |
| `module` | Resolve packages; check target membership and the scheme's build order; open the `.xcworkspace` for CocoaPods; check the deployment target |
| `linker` | Read the `ld:` lines *above* "linker command failed": the missing symbol names the missing framework or file |
| `script-phase` | Read the script's own output above the failure; for `Sandbox: … deny`, declare input/output files or turn off `ENABLE_USER_SCRIPT_SANDBOXING` for that target |
| `concurrency` / `swiftui` | Hand over to the `swiftui-state-layout` skill |
| `spm` / `cocoapods` | `xcodebuild -resolvePackageDependencies` / `pod install`; check private-repo credentials and Package.resolved |
| `destination` / `scheme` | `xcodebuild -list`, `xcodebuild -showdestinations -scheme X`, share the scheme |

4. **Check the effective settings** for the failing target:
   `scripts/xcode_env.py settings App.xcodeproj --scheme App --configuration Release`.
5. **Stale state last.** Only if errors mention module caches or "has been modified since the module file
   was built": suggest deleting `~/Library/Developer/Xcode/DerivedData/<Project>-*`. Ask the user before
   deleting anything.

Error catalog with causes: [references/build-error-catalog.md](references/build-error-catalog.md).

## Workflow 2: signing and provisioning

1. **What does the target ask for?** Check `DEVELOPMENT_TEAM`, `CODE_SIGN_STYLE`, `CODE_SIGN_IDENTITY`,
   `PROVISIONING_PROFILE_SPECIFIER`, `PRODUCT_BUNDLE_IDENTIFIER` and `CODE_SIGN_ENTITLEMENTS` per
   configuration (`xcode_env.py settings`).
2. **Which identities exist?** `inspect_signing.py identities`. You need a *valid* identity (certificate +
   private key) of the right kind: Apple Development or Apple Distribution.
3. **Which profiles match?** `inspect_signing.py list --bundle-id <id>`. Check the type (development,
   ad-hoc, app-store, enterprise), the expiry and the team.
4. **Does the profile fit?** `inspect_signing.py profile X.mobileprovision --check-keychain` shows
   whether you hold a private key for one of its certificates. `compare --entitlements …` shows every
   entitlement the app requests that the profile does not grant.
5. **Fix at the source:**
   - "No profiles for 'id'": enable automatic signing (pass `-allowProvisioningUpdates`; on CI add an App
     Store Connect API key with `-authenticationKeyPath/-authenticationKeyID/-authenticationKeyIssuerID`),
     or create a profile for that exact bundle id.
   - "doesn't include signing certificate": regenerate the profile with your certificate, or install the
     `.p12` for a certificate it lists.
   - Capability missing: enable it on the App ID (developer portal or Signing & Capabilities), then regenerate.
   - `aps-environment` mismatch: a development profile grants `development`, distribution grants
     `production`. Use the right profile type and let Xcode set the value.
   - Automatic/manual conflict: clear `PROVISIONING_PROFILE_SPECIFIER` when using automatic signing.
   - CI `errSecInternalComponent`: unlock the keychain and set the key partition list before `xcodebuild`.

Signing model, profile types and CI recipes: [references/signing-and-provisioning.md](references/signing-and-provisioning.md).

## Workflow 3: Info.plist keys

1. `check_plist.py <Info.plist> --sources <app dir> --project <xcodeproj>`. With generated plists
   (`GENERATE_INFOPLIST_FILE = YES`) pass `--project` only.
2. Fix by severity:
   - **PL004 errors are launch-time crashes**: the API is used without its `NS…UsageDescription` key, and
     the app is killed the first time it touches the API.
   - PL003: `$(VAR)` with no definition becomes an empty string (empty bundle version, so the upload is rejected).
   - PL010: `INFOPLIST_KEY_*` settings are ignored unless `GENERATE_INFOPLIST_FILE = YES`.
   - PL005: placeholder usage strings get rejected in App Review.
3. For a built product, run `check_plist.py path/App.app --built`. That is the plist the OS actually reads.

Key reference: [references/info-plist-keys.md](references/info-plist-keys.md).

## Workflow 4: resources that are nil at runtime

1. `check_bundle_resources.py --sources <dir>` finds literal lookups whose file or asset doesn't exist (BR001/BR007).
2. Build once, then add `--app <path to .app>` (from `xcode_env.py settings … --key BUILT_PRODUCTS_DIR`).
   This finds files that weren't copied (BR002, target membership) or landed in a sub-folder (BR003, folder reference).
3. In Swift packages, resources live in `Bundle.module`, and only if the target declares `resources:` (BR004/BR005).
4. Custom fonts need `UIAppFonts` and the PostScript name (BR006).
5. Remember that device file systems are **case-sensitive**, while the simulator on macOS usually is not.

## Failure modes to avoid

- Don't toggle signing settings at random. Change one thing and re-run `compare`.
- Never commit `.p12`, `.p8`, `.mobileprovision` files or passwords. Use CI secrets. This skill's scripts never print keys.
- Don't hard-code `PROVISIONING_PROFILE` UUIDs in project files; use specifiers or automatic signing.
- Don't delete DerivedData, profiles or keychain items without asking; deletion is never the first step.
- Don't "fix" a missing usage description by removing the API call without telling the user.

## Worked examples

**Archive fails with two signing errors.**
`extract_build_errors.py` reports `PROV002 Provisioning profile "App Dist" doesn't include signing
certificate "Apple Distribution: Acme (TEAM)"`. `inspect_signing.py profile App_Dist.mobileprovision
--check-keychain` shows SIG003: none of the profile's certificate SHA-1s match an identity in the keychain.
The certificate was renewed on another Mac. Fix: regenerate the profile with the new certificate, or
export and install the `.p12` from the other Mac.

**App crashes the first time the user opens the camera screen.**
`check_plist.py --project App.xcodeproj --sources App` reports PL010: `INFOPLIST_KEY_NSCameraUsageDescription`
is set but `GENERATE_INFOPLIST_FILE = NO`, so the key never reaches the bundle. It also reports PL004
for `AVCaptureSession`. Fix: add the key to `App/Info.plist` (or enable generation), then confirm with
`check_plist.py App.app --built`.

**`Bundle.main.url(forResource: "seed", withExtension: "json")` returns nil on device only.**
`check_bundle_resources.py --sources App --app …/App.app` reports BR003: the file is bundled at
`Data/seed.json` because `Data` was added as a folder reference. Fix: pass `subdirectory: "Data"`
or re-add the files as a group.

## References

- [references/build-error-catalog.md](references/build-error-catalog.md): every classifier id with cause and fix
- [references/signing-and-provisioning.md](references/signing-and-provisioning.md): signing model, profile types, entitlements, CI keychains
- [references/info-plist-keys.md](references/info-plist-keys.md): required keys, privacy usage keys by API, generated Info.plist rules
- [assets/ExportOptions.plist](assets/ExportOptions.plist): annotated `-exportArchive` template
- [assets/ci-signing.sh](assets/ci-signing.sh): reference CI keychain setup (reads secrets from env; review before use)
