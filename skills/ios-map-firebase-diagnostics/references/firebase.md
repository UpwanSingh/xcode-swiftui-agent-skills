# Firebase on iOS: reference

## Contents
1. Initialization
2. GoogleService-Info.plist and environments
3. Auth
4. Firestore
5. Storage
6. Security rules patterns
7. App Check and emulators
8. Rule index (check_firebase_config.py, lint_firebase_rules.py)

## 1. Initialization

```swift
@main
struct MyApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate   // optional: APNs, URL handling
    @State private var session: SessionModel

    init() {
        FirebaseApp.configure()                          // exactly once, first
        _session = State(initialValue: SessionModel())   // safe: created after configure()
    }
    var body: some Scene { WindowGroup { RootView().environment(session) } }
}
```

- `App.init()` runs **before** `application(_:didFinishLaunchingWithOptions:)`. If you configure in the
  AppDelegate, nothing in `App.init` or the App's stored properties may touch Firebase.
- Stored properties (`let db = Firestore.firestore()`, `@State private var m = Model()` whose init uses
  Firebase) initialise **before** the `init()` body runs (FB008).
- SwiftUI previews and unit tests may run the app's init repeatedly. Guard with
  `if FirebaseApp.app() == nil { FirebaseApp.configure() }`.
- Swift Package products: `FirebaseAuth`, `FirebaseFirestore`, `FirebaseStorage`, `FirebaseMessaging`, …
  Since Firebase 11 the Swift extensions (`FirebaseFirestoreSwift`, …) are merged into the main products.
- SDK logging: the launch argument `-FIRDebugEnabled` (and `-FIRDebugDisabled` to undo it, since it persists).

## 2. GoogleService-Info.plist and environments

| Key | Checked by | Notes |
|---|---|---|
| `GOOGLE_APP_ID` | FB005/FB006 | Format `1:<project number>:ios:<hex>`. `:android:`/`:web:` means the wrong file |
| `BUNDLE_ID` | FB004 | Must equal `PRODUCT_BUNDLE_IDENTIFIER` of the target that ships it |
| `PROJECT_ID`, `GCM_SENDER_ID`, `API_KEY` | FB003 | API keys identify the project; they are not secrets, but restrict them to your bundle ids in Google Cloud |
| `CLIENT_ID`, `REVERSED_CLIENT_ID` | FB009 | Only present if Google sign-in was enabled when the file was downloaded |
| `STORAGE_BUCKET` | | `<project>.appspot.com` (older projects) or `<project>.firebasestorage.app` (newer) |

Multiple environments (FB002):
- Keep `Config/Staging/GoogleService-Info.plist` and `Config/Prod/GoogleService-Info.plist` **out of** the
  target, and copy the right one in a Run Script phase that runs before Copy Bundle Resources (declare the
  input/output files so user-script sandboxing allows it), **or**
- `FirebaseApp.configure(options: FirebaseOptions(contentsOfFile: path)!)` with a per-configuration path.

## 3. Auth

| Symptom | Cause | Fix |
|---|---|---|
| Google sign-in returns to Safari and never back | `REVERSED_CLIENT_ID` URL scheme missing | Add a URL type (FB009) |
| Phone auth: `missing-app-token` / reCAPTCHA loops | APNs not configured, encoded-app-id URL scheme missing | Upload the APNs key in the console; add `app-1-…-ios-…` (FB010) |
| `app-not-authorized`, `invalid-api-key` | Wrong plist, or API key restricted to another bundle id | `check_firebase_config.py`; check key restrictions |
| `currentUser` is nil at launch | Auth restores state asynchronously | Use `addStateDidChangeListener` before querying user data |
| Sign in with Apple fails | Capability or Services ID missing | Enable Sign in with Apple on the App ID, set up the provider in the console |
| Swizzling disabled (`FirebaseAppDelegateProxyEnabled = NO`) | Push/URL callbacks not forwarded | Forward the APNs token and `canHandle(url)` manually (FB012) |

## 4. Firestore

- `PERMISSION_DENIED` / "Missing or insufficient permissions": rules (section 6), App Check enforcement,
  or `request.auth == null` because the query ran before sign-in finished.
- `FAILED_PRECONDITION: The query requires an index`: open the link in the error, or deploy `firestore.indexes.json`.
- "Firestore instance has already been started and its settings can no longer be changed": configure
  `Firestore.firestore().settings` (cache, host) immediately after `configure()`.
- Listeners: keep the `ListenerRegistration` and call `remove()` in `onDisappear`/`deinit`. A leaked
  listener keeps updating stale models and costs reads.
- Offline cache: reads can be served from cache with `metadata.isFromCache == true`. Missing data isn't
  always a rules problem.
- Codable: decoding errors appear as `DecodingError` in the `getDocument(as:)` catch. Check `@DocumentID`
  and optional fields.

## 5. Storage

- `User does not have permission to access gs://bucket/path` (403): storage rules.
- `Object does not exist` (404): wrong path (case-sensitive), wrong bucket (`Storage.storage(url:)`), or
  the upload hasn't finished.
- Large uploads: use `putFile(from:)` (resumable) instead of `putData`, and observe the task's progress.
  Keep a strong reference to the task.

## 6. Security rules patterns

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    function signedIn() { return request.auth != null; }
    function isOwner(uid) { return signedIn() && request.auth.uid == uid; }

    match /users/{uid} {
      allow read, update, delete: if isOwner(uid);
      allow create: if isOwner(uid) && request.resource.data.keys().hasOnly(['name', 'createdAt']);
    }
    match /posts/{postId} {
      allow read: if signedIn();
      allow create: if signedIn() && request.resource.data.authorId == request.auth.uid;
      allow update, delete: if signedIn() && resource.data.authorId == request.auth.uid;
    }
    match /{path=**}/comments/{commentId} {          // collection-group queries (needs rules_version 2)
      allow read: if signedIn();
    }
  }
}
```

- Rules are **not filters**. A query must be provably constrained by the rule (e.g. `where authorId ==
  uid`), or the whole query is denied.
- `match /{document=**}` at the root applies to every collection. With `if request.auth != null` it
  grants every signed-in user access to all data (FR004).
- Test-mode rules (`request.time < timestamp.date(…)`) expire, and then everything is denied (FR002).
- Rules are tested with the Firebase emulator suite or the console's Rules Playground. Deploying is the
  owner's decision.

## 7. App Check and emulators

- App Check enforcement makes requests from unattested clients fail like permission errors. In debug,
  use `AppCheckDebugProviderFactory` and register the debug token printed in the console.
- Emulators: `Auth.auth().useEmulator(withHost: "localhost", port: 9099)`,
  `Firestore.firestore().useEmulator(withHost:port:)` **before** any other use, and only in `#if DEBUG` (FB011).
  On a physical device use the Mac's LAN IP, not `localhost`.

## 8. Rule index

| Rule | Severity | Meaning |
|---|---|---|
| FB001 | error | No GoogleService-Info.plist |
| FB002 | warning | Several config plists |
| FB003 | error | Required keys missing |
| FB004 | error | BUNDLE_ID mismatch |
| FB005 | error | Not an iOS GOOGLE_APP_ID |
| FB006 | warning | GOOGLE_APP_ID / GCM_SENDER_ID from different projects |
| FB007 | error | configure() missing or duplicated |
| FB008 | error/warning | Firebase used before configure() |
| FB009 | error | Google Sign-In URL scheme missing |
| FB010 | warning | Phone Auth URL scheme missing |
| FB011 | warning | Emulator outside #if DEBUG |
| FB012 | info | App delegate proxy disabled |
| FR001 | error/warning | Unconditional allow |
| FR002 | error | Expired test-mode rule |
| FR003 | warning | Test-mode rule will expire |
| FR004 | warning | Root recursive wildcard with only `request.auth != null` |
| FR005 | info | Writes allowed for any signed-in user |
| FR006 | warning | Recursive wildcards without `rules_version = '2'` |
| FR007 | warning | Collection used in code without a matching rule |
| FR008 | warning | collectionGroup without a `{path=**}` rule |
