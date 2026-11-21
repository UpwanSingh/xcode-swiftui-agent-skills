# Signing and provisioning: reference

## Contents
1. The model
2. Profile types
3. Entitlements: where they come from
4. Automatic vs manual signing
5. CI signing recipe
6. Checks (inspect_signing.py rule ids)

## 1. The model

```
Bundle ID (PRODUCT_BUNDLE_IDENTIFIER)
   │ must match (wildcards allowed)
App ID in profile  (Entitlements.application-identifier = TEAMID.com.acme.app or TEAMID.com.acme.*)
   │ profile embeds
Certificates (DeveloperCertificates: SHA-1 fingerprints)
   │ one of them must be
Identity in keychain (certificate + PRIVATE KEY)  ← `security find-identity -v -p codesigning`
   │ and
Requested entitlements (.entitlements, after $(…) expansion) ⊆ profile Entitlements
```

When any link breaks, Xcode reports it in a different way. `inspect_signing.py` checks each link separately.

Profiles live in:
- `~/Library/Developer/Xcode/UserData/Provisioning Profiles` (Xcode 16+)
- `~/Library/MobileDevice/Provisioning Profiles` (Xcode 15 and earlier)

## 2. Profile types

| Type (`inspect_signing.py`) | How it is detected | Use | Certificate |
|---|---|---|---|
| development | `ProvisionedDevices` + `get-task-allow = true` | Debug on registered devices | Apple Development |
| ad-hoc | `ProvisionedDevices`, no `get-task-allow` | Install outside the store on registered devices | Apple Distribution |
| app-store | no device list | TestFlight / App Store | Apple Distribution |
| enterprise | `ProvisionsAllDevices = true` | In-house (Enterprise program) | Enterprise distribution |

`get-task-allow = true` (debugger attach) is only valid in development builds. A distribution build
that requests it fails validation (SIG005).

## 3. Entitlements

- **Requested**: the `.entitlements` file set by `CODE_SIGN_ENTITLEMENTS`, expanded at build time
  (`$(AppIdentifierPrefix)`, `$(TeamIdentifierPrefix)`, `$(PRODUCT_BUNDLE_IDENTIFIER)`).
- **Granted**: the profile's `Entitlements` dictionary, generated from the App ID's capabilities.
- **Signed**: what ends up in the binary. Read it with `codesign -d --entitlements - --xml App.app`
  (`inspect_signing.py compare --entitlements -signed- --app App.app`).

Common mismatches:

| Entitlement | Typical issue |
|---|---|
| `aps-environment` | `development` vs `production`. Xcode rewrites it for the profile type with automatic signing; manual signing with the wrong profile fails |
| `com.apple.security.application-groups` | The group exists in the file but isn't assigned to the App ID (also needed for **each** extension) |
| `keychain-access-groups` | Profile grants `TEAMID.*`; a file listing another team's prefix fails |
| `com.apple.developer.associated-domains` | Development profiles often grant `*`; distribution needs the capability enabled |
| `com.apple.developer.icloud-container-identifiers` | Container not linked to the App ID |
| `com.apple.developer.healthkit`, `…applesignin`, `…in-app-payments` | Capability not enabled on the App ID |

After enabling a capability in the portal, **existing profiles do not change**. Regenerate them, or
let automatic signing refresh them.

## 4. Automatic vs manual signing

| | Automatic (`CODE_SIGN_STYLE = Automatic`) | Manual |
|---|---|---|
| Profile choice | Xcode creates or selects one | `PROVISIONING_PROFILE_SPECIFIER` (name) per configuration |
| CLI | needs `-allowProvisioningUpdates` to create or update profiles, plus an account or API key | needs the profile installed and the identity in the keychain |
| Common failure | No account in Xcode on CI | Specifier names a profile of the wrong type or bundle id |

Don't mix them: a specifier on an automatically signed target causes SIGN005. Check **every
configuration**. Release is often set up differently from Debug.

## 5. CI signing recipe (outline)

1. Secrets in the CI store: a base64 `.p12` and its password, the profile(s), or an App Store Connect API
   key (`.p8` contents, key id, issuer id). **Never commit them.**
2. Create a temporary keychain, import the `.p12`, allow codesign access, put the keychain in the search
   list, and unlock it. `assets/ci-signing.sh` shows the commands. It reads everything from environment
   variables.
3. Install profiles into the profile folder shown above, or use automatic signing with
   `-allowProvisioningUpdates -authenticationKeyPath "$KEY_PATH" -authenticationKeyID "$KEY_ID" -authenticationKeyIssuerID "$ISSUER_ID"`.
4. Archive: `xcodebuild -scheme App -configuration Release -archivePath build/App.xcarchive archive`.
5. Export: `xcodebuild -exportArchive -archivePath build/App.xcarchive -exportOptionsPlist ExportOptions.plist -exportPath build/export`.
6. Delete the temporary keychain in an always-run cleanup step.

## 6. Checks

| Rule | Severity | Meaning |
|---|---|---|
| SIG001 | error/warning | Profile expired, or expires within 14 days |
| SIG002 | warning | A certificate inside the profile has expired |
| SIG003 | error | No valid keychain identity matches any certificate in the profile |
| SIG004 | error | Bundle id does not match the profile's App ID |
| SIG005 | error | `get-task-allow` requested with a distribution profile |
| SIG006 | error | Entitlement requested but not granted (capability missing) |
| SIG007 | error | Entitlement value not allowed by the profile (e.g. `aps-environment`, app group ids) |
