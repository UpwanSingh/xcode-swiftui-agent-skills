# Info.plist keys: reference

## Contents
1. Source vs generated vs built Info.plist
2. Required bundle keys
3. Privacy usage descriptions by API
4. Other keys that cause runtime bugs
5. Checks (check_plist.py rule ids)

## 1. Source vs generated vs built

| Setup | Where keys come from | Check with |
|---|---|---|
| `INFOPLIST_FILE = App/Info.plist`, `GENERATE_INFOPLIST_FILE = NO` | Only the file (`INFOPLIST_KEY_*` settings are **ignored**, PL010) | `check_plist.py App/Info.plist --project App.xcodeproj` |
| `GENERATE_INFOPLIST_FILE = YES` and no file | `INFOPLIST_KEY_*` build settings + Xcode defaults | `check_plist.py --project App.xcodeproj` |
| `GENERATE_INFOPLIST_FILE = YES` + `INFOPLIST_FILE` | The file merged with the `INFOPLIST_KEY_*` settings | both arguments |
| Built product | `App.app/Info.plist` with `$(…)` expanded | `check_plist.py App.app --built` |

`$(VAR)` references are expanded from the target's build settings at build time. An undefined variable
expands to an **empty string** (PL003). An empty `CFBundleVersion` fails upload. With `--project`,
`check_plist.py` reads both `project.pbxproj` and every `.xcconfig` next to the project, because many
projects keep `MARKETING_VERSION` and similar settings in xcconfig files.

## 2. Required bundle keys

| Key | Usually | Notes |
|---|---|---|
| `CFBundleIdentifier` | `$(PRODUCT_BUNDLE_IDENTIFIER)` | Must match the provisioning profile and Firebase/Apple services configuration |
| `CFBundleExecutable` | `$(EXECUTABLE_NAME)` | |
| `CFBundleShortVersionString` | `$(MARKETING_VERSION)` | 1–3 period-separated integers (`1.4.2`) |
| `CFBundleVersion` | `$(CURRENT_PROJECT_VERSION)` | Must increase for each upload of the same marketing version |
| `UILaunchScreen` / `UILaunchStoryboardName` | `{}` | Without it the app runs letterboxed at legacy resolution (PL009) |

## 3. Privacy usage descriptions by API

The app is **terminated** (TCC) the first time it calls one of these APIs without the key. Upload
validation also rejects binaries that link the API without the key (ITMS-90683).

| API (examples) | Key |
|---|---|
| `CLLocationManager.requestWhenInUseAuthorization`, `CLLocationUpdate`, `CLServiceSession` | `NSLocationWhenInUseUsageDescription` |
| `requestAlwaysAuthorization` | `NSLocationAlwaysAndWhenInUseUsageDescription` (+ WhenInUse) |
| `AVCaptureDevice`, `AVCaptureSession`, `UIImagePickerController` camera, `DataScannerViewController` | `NSCameraUsageDescription` |
| `AVAudioRecorder`, `AVAudioSession` record categories, `AVAudioEngine.inputNode` | `NSMicrophoneUsageDescription` |
| `PHPhotoLibrary` read access, `PHAsset.fetch…` | `NSPhotoLibraryUsageDescription` |
| Saving only (`UIImageWriteToSavedPhotosAlbum`, `.addOnly`) | `NSPhotoLibraryAddUsageDescription` |
| `PhotosPicker` / `PHPickerViewController` | *none* (runs out of process) |
| `CNContactStore` | `NSContactsUsageDescription` |
| `EKEventStore` | `NSCalendarsFullAccessUsageDescription` / `NSCalendarsWriteOnlyAccessUsageDescription` (iOS 17+), `NSRemindersFullAccessUsageDescription` |
| `CBCentralManager`, `CBPeripheralManager` | `NSBluetoothAlwaysUsageDescription` |
| `SFSpeechRecognizer` | `NSSpeechRecognitionUsageDescription` (+ microphone) |
| `HKHealthStore` | `NSHealthShareUsageDescription` / `NSHealthUpdateUsageDescription` |
| `LAContext` with biometrics | `NSFaceIDUsageDescription` |
| `ATTrackingManager` | `NSUserTrackingUsageDescription` |
| `NFCNDEFReaderSession`, `NFCTagReaderSession` | `NFCReaderUsageDescription` |
| `CMPedometer`, `CMMotionActivityManager` | `NSMotionUsageDescription` |
| Bonjour/local network (`NWBrowser`, `NWListener`, Multipeer) | `NSLocalNetworkUsageDescription` (+ `NSBonjourServices`) |
| `MPMediaLibrary`, `MPMediaQuery` | `NSAppleMusicUsageDescription` |
| `HMHomeManager` | `NSHomeKitUsageDescription` |

Write specific strings ("Scan receipts to attach them to expenses"). Generic strings get rejected under
guideline 5.1.1 (PL005).

Also required since 2024 for apps and SDKs that use "required reason" APIs (UserDefaults, file timestamps,
disk space, system boot time): a `PrivacyInfo.xcprivacy` manifest. It is a separate file, not an Info.plist key.

## 4. Other keys that cause runtime bugs

| Key | Bug when missing or wrong |
|---|---|
| `LSApplicationQueriesSchemes` | `canOpenURL` returns false for unlisted schemes (PL007) |
| `CFBundleURLTypes` | Custom URL schemes / OAuth redirects (Google Sign-In `REVERSED_CLIENT_ID`) never reach the app |
| `UIBackgroundModes` | `allowsBackgroundLocationUpdates = true` without `location` crashes; push without `remote-notification` gives no background fetch |
| `NSAppTransportSecurity` | http:// blocked; `NSAllowsArbitraryLoads` needs justification (PL006) |
| `UIAppFonts` | Custom fonts silently fall back to the system font |
| `UIApplicationSceneManifest` | Wrong delegate class name gives a black screen at launch (UIKit lifecycle) |
| `UIRequiredDeviceCapabilities` | Over-restrictive values hide the app from devices in the store |
| `ITSAppUsesNonExemptEncryption` | Missing: every TestFlight build waits for an export-compliance answer |

## 5. Checks

| Rule | Severity | Meaning |
|---|---|---|
| PL001 | error | Cannot parse |
| PL002 | error/warning | Required key missing |
| PL003 | error/warning | `$(VAR)` unexpanded (built) or undefined (source) |
| PL004 | error | Privacy-gated API used without its usage key |
| PL005 | warning | Empty/placeholder usage string |
| PL006 | warning | `NSAllowsArbitraryLoads` |
| PL007 | warning | `canOpenURL` scheme not declared |
| PL008 | warning | Malformed version strings |
| PL009 | warning | No launch screen |
| PL010 | warning | `INFOPLIST_KEY_*` ignored (generation off) |

Resource checks (`check_bundle_resources.py`):

| Rule | Severity | Meaning |
|---|---|---|
| BR001 | error | File referenced in code missing from sources |
| BR002 | error | File missing from the built .app (target membership) |
| BR003 | error | File bundled in a sub-folder the lookup doesn't pass |
| BR004 | warning | `Bundle.main` inside a Swift package |
| BR005 | warning | `Bundle.module` without `resources:` |
| BR006 | warning | Custom font without `UIAppFonts` |
| BR007 | error | Asset name not found in any `.xcassets` |
