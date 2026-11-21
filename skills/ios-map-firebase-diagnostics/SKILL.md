---
name: ios-map-firebase-diagnostics
description: Diagnose MapKit and Core Location bugs (map not centering, invalid region crashes, swapped coordinates, annotations duplicating or replacing the user dot, location permission prompt never appearing, background location crashes, SwiftUI Map update loops) and Firebase iOS configuration problems (FirebaseApp.configure order and duplicates, wrong or multiple GoogleService-Info.plist, bundle id mismatch, Google/Phone sign-in URL schemes, Firestore/Storage "Missing or insufficient permissions", expired test-mode security rules, missing indexes, emulator settings). Use when a map, location feature, Firebase Auth, Firestore or Storage call misbehaves in an iOS app.
license: MIT
compatibility: Scripts need Python 3.9+ (stdlib only) and run on any OS. Guidance targets iOS 17+ SwiftUI MapKit and Firebase Apple SDK 10/11+ (Swift Package Manager).
metadata:
  version: "1.0.0"
  author: UpwanSingh
---

# MapKit, Core Location and Firebase diagnostics

These bugs rarely show up as compile errors. They show up as **silent nil, an empty map, a
missing prompt or PERMISSION_DENIED**. Always gather three things before you change code:
the configuration files, the runtime log, and the exact call site.

## Scripts (all read-only)

```bash
python3 scripts/mapkit_check.py App/ --plist App/Info.plist          # MK001–MK011
python3 scripts/check_firebase_config.py --root .                     # FB001–FB012 (API keys masked)
python3 scripts/lint_firebase_rules.py --root . --sources App/        # FR001–FR008
```

Add `--project App.xcodeproj` when the Info.plist is generated (`INFOPLIST_KEY_*`). Use `--json`
for machine output. Exit status is `1` when errors are found.

Runtime logs: `python3 ../ios-crash-log-triage/scripts/sim_logs.py scan console.log` (or the
`scan_runtime_log` / `simulator_logs` MCP tools) classifies Firebase and MapKit runtime messages.

## Workflow 1: location permission and the user's position

1. `mapkit_check.py <app dir> --plist <Info.plist>`.
2. Check the chain in order. Every link fails silently:
   1. **Usage key** present (`NSLocationWhenInUseUsageDescription`; Always also needs
      `NSLocationAlwaysAndWhenInUseUsageDescription`). Without it the prompt never shows (MK002).
   2. **Manager retained.** A `CLLocationManager` held in a local variable is deallocated, so the
      prompt disappears and the delegate is never called (MK001).
   3. **Delegate set** before requesting (MK010). React in `locationManagerDidChangeAuthorization(_:)`.
   4. **Authorization requested** before showing the user location (MK003), or use `CLServiceSession` (iOS 18+).
   5. **Background**: `allowsBackgroundLocationUpdates = true` needs `UIBackgroundModes: location`,
      otherwise it crashes (MK004).
3. **Simulator:** the simulator has no position until one is set (Features > Location, or the user runs
   `xcrun simctl location booted set 37.33,-122.03`). `kCLErrorDomain Code=0` means "location unknown",
   not a bug. Code=1 means denied.
4. **Status persists.** Once the user denies, the prompt never shows again. Detect `.denied` and link to
   Settings with `UIApplication.openSettingsURLString`. On the simulator, the user can reset with
   `xcrun simctl privacy booted reset location <bundle id>`. Ask before suggesting resets.

## Workflow 2: the map shows the wrong place, jitters or crashes

1. `mapkit_check.py`: MK005 (invalid coordinate/span, swapped lat/lon), MK008 (updateUIView loops),
   MK009 (deprecated `Map(coordinateRegion:)` binding loops).
2. Coordinates: **latitude −90…90, longitude −180…180**. San Francisco is `(37.77, -122.42)`. If the map
   shows Antarctica or the ocean, the values are probably swapped or still at `(0, 0)` (Null Island).
3. Region: span deltas must be > 0 (`latitudeDelta` ≤ 180). NaN from a division over an empty
   annotation list throws "Invalid Region".
4. SwiftUI (iOS 17+): drive the camera with `@State var position: MapCameraPosition` and read the
   user's panning in `.onMapCameraChange(frequency: .onEnd)`. Don't write the region back on every
   change.
5. UIKit `MKMapView` in `UIViewRepresentable`: create it once in `makeUIView`. In `updateUIView`,
   compare before `setRegion` and diff annotations (MK008). Return `nil` for `MKUserLocation` in
   `viewFor` (MK006). Register reusable views before dequeuing with `for:` (MK007).

Details: [references/mapkit.md](references/mapkit.md).

## Workflow 3: Firebase does not initialise or talks to the wrong project

1. `check_firebase_config.py --root .` (add `--bundle-id` when there are several targets).
2. Fix in this order:
   - FB001/FB003/FB005: missing, incomplete or wrong-platform `GoogleService-Info.plist`. Re-download it from
     the console for the **iOS** app.
   - FB004: `BUNDLE_ID` differs from the target's bundle id. Auth, FCM and App Check fail. Register
     the id, or use the matching plist.
   - FB002: several plists. Only the file named `GoogleService-Info.plist` at the bundle root is read. For
     per-environment configs, copy the right one in a build phase, or load it with
     `FirebaseOptions(contentsOfFile:)` and `FirebaseApp.configure(options:)`.
   - FB007: `configure()` missing or called twice ("Default app has already been configured").
   - FB008: a stored property such as `let db = Firestore.firestore()` on the `App` struct runs
     **before** `init()`'s `FirebaseApp.configure()`, which crashes with "The default FirebaseApp instance
     must be configured". Create services after `configure()`.
3. Turn on SDK logging for one run with the launch argument `-FIRDebugEnabled` (scheme > Run > Arguments).
   Then scan the console log.

## Workflow 4: Auth, Firestore and Storage requests fail

1. Classify the runtime message (`sim_logs.py scan`):

| Message | Meaning | Next |
|---|---|---|
| `Missing or insufficient permissions` / `PERMISSION_DENIED` | Rules denied it (or App Check enforcement) | `lint_firebase_rules.py --sources App/` |
| `The query requires an index` (`FAILED_PRECONDITION`) | Composite index missing | Open the URL in the message, or add it to `firestore.indexes.json` |
| `User does not have permission to access gs://…` | Storage rules | `lint_firebase_rules.py storage.rules` |
| `Object … does not exist` | Wrong path or bucket | Compare `STORAGE_BUCKET`; check the path's case |
| `API key not valid`, `app-not-authorized`, `INVALID_APP_CREDENTIAL` | Key restrictions or bundle id | `check_firebase_config.py`; check the key's iOS restriction in Google Cloud |
| `APNS device token not set before retrieving FCM Token` | Messaging/Phone Auth before APNs registration | Register for remote notifications first; forward the token |
| `Firestore instance has already been started` | Settings changed after first use | Set `settings` right after `configure()` |

2. Rules: `lint_firebase_rules.py` finds world-open rules (FR001), **expired test-mode rules** (FR002, the
   classic "everything worked until this morning"), over-broad auth (FR004/FR005) and collections the
   app uses without a rule (FR007; the default is deny).
3. Remember that the **deployed** rules are what count. Ask the user to confirm the console matches the
   local file before concluding. Deploying is the user's decision (`firebase deploy --only firestore:rules`).
4. Auth state: read data inside `Auth.auth().addStateDidChangeListener`, not at launch. `request.auth`
   is `null` until sign-in completes, so the first query can fail even when the rules are correct.
5. Google Sign-In needs `REVERSED_CLIENT_ID` as a URL scheme (FB009). Phone Auth needs APNs or the encoded
   app id URL scheme (FB010). Emulators belong in `#if DEBUG` (FB011).

Details: [references/firebase.md](references/firebase.md).

## Failure modes to avoid

- Don't loosen rules to `allow read, write: if true` "to test". It exposes the data to anyone with the project id.
  Use the emulator or a scoped rule instead.
- Don't paste API keys, service-account JSON or ID tokens into chat or code. Scripts mask keys. Never
  read `*-firebase-adminsdk-*.json` files (the MCP server refuses them).
- Don't call `FirebaseApp.configure()` in multiple places "to be safe".
- Don't request `Always` location when `WhenInUse` is enough; App Review asks you to justify it.
- Don't reset simulator privacy, deploy rules or change console settings without asking.

## Worked examples

**"Everything returns Missing or insufficient permissions since today."**
`lint_firebase_rules.py --root .` reports `FR002 test-mode rule expired on 2025-06-01` for
`match /{document=**} { allow read, write: if request.time < timestamp.date(2025, 6, 1); }`.
Replace it with per-collection rules (`assets/firestore.rules.template`) and ask the user to deploy.

**Launch crash: "The default FirebaseApp instance must be configured before the default app is used".**
`check_firebase_config.py` reports FB008 at `let db = Firestore.firestore()` on the `@main` App struct.
Stored properties initialise before `init()` runs `FirebaseApp.configure()`. Move service creation
into `init()` after `configure()`, or into the model that needs it (`assets/FirebaseBootstrap.swift`).

**Map opens off the coast of Antarctica.**
`mapkit_check.py` reports `MK005 coordinate (-122.41, 37.77) is invalid – latitude and longitude look
swapped`. The API returned `[lng, lat]` (GeoJSON order) and the code built
`CLLocationCoordinate2D(latitude: p[0], longitude: p[1])`. Swap them when decoding.

## References

- [references/mapkit.md](references/mapkit.md): SwiftUI Map (iOS 17+) patterns, MKMapView representable, Core Location authorization, simulator tips
- [references/firebase.md](references/firebase.md): initialization, multi-environment configs, Auth/Firestore/Storage errors, rules patterns, App Check, emulators
- [assets/FirebaseBootstrap.swift](assets/FirebaseBootstrap.swift): correct initialization order for SwiftUI apps
- [assets/LocationModel.swift](assets/LocationModel.swift): retained manager + authorization + SwiftUI Map camera
- [assets/firestore.rules.template](assets/firestore.rules.template) and [assets/storage.rules.template](assets/storage.rules.template): secure baselines
