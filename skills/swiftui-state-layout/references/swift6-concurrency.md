# Swift 6 concurrency and actor isolation: reference

## Contents
1. Build settings that change the rules
2. Diagnostics catalog
3. Runtime isolation crashes
4. Patterns for SwiftUI apps
5. Migration order
6. Rule index (concurrency_check.py)

## 1. Build settings that change the rules

| Setting (Xcode) / SwiftPM | Effect |
|---|---|
| `SWIFT_VERSION = 6` / `swift-tools-version: 6.0` | Swift 6 language mode: data-race safety violations are **errors** |
| `SWIFT_STRICT_CONCURRENCY = minimal / targeted / complete` | Swift 5 mode only: how many checks run (as warnings) |
| `SWIFT_DEFAULT_ACTOR_ISOLATION = MainActor` / `.defaultIsolation(MainActor.self)` (Swift 6.2) | Unannotated declarations in the module are `@MainActor`. New Xcode 26 app templates use it |
| `SWIFT_APPROACHABLE_CONCURRENCY = YES` (Swift 6.2) | Enables upcoming features such as `NonisolatedNonsendingByDefault` (async functions run on the caller's actor unless marked `@concurrent`) and `InferIsolatedConformances` |
| `SWIFT_UPCOMING_FEATURE_*` / `.enableUpcomingFeature("…")` | Individual opt-ins |

`concurrency_check.py` prints what it found. **Diagnose with the settings of the target that fails**: an app
target and its packages often use different modes.

## 2. Diagnostics catalog

| Message (abridged) | Root cause | Fix options (best first) |
|---|---|---|
| `Main actor-isolated property 'x' can not be mutated from a nonisolated context` | Code outside the main actor writes UI state | Make the enclosing type/function `@MainActor`; `await MainActor.run { … }` for a one-off hop |
| `Main actor-isolated instance method 'f()' cannot be called from outside of the actor` / `Call to main actor-isolated … in a synchronous nonisolated context` | Sync, non-isolated code calls a `@MainActor` API | Isolate the caller; make the caller `async` and `await` |
| `Static property 'shared' is not concurrency-safe because it is nonisolated global shared mutable state` | `static var` / global `var` | `static let` with a Sendable value; `@MainActor static var`; move it into an actor; `nonisolated(unsafe)` only if it is immutable after launch |
| `Sending 'x' risks causing data races` / `Passing argument of non-sendable type` | A non-Sendable value crosses isolation (into a `Task`, an actor or `@MainActor`) | Make it `Sendable`; don't use it after sending; build it on the destination side; isolate the class |
| `Capture of 'self' with non-sendable type 'X' in a '@Sendable' closure` | `Task.detached`, `DispatchQueue.async` or a `@Sendable` callback captures self | Isolate `X` to `@MainActor` (it becomes Sendable); use `Task { }` instead of detached |
| `Non-sendable type 'X' returned by implicitly asynchronous call to main actor-isolated function cannot cross actor boundary` | Returning a class instance out of `@MainActor` | Return value types or Sendable types |
| `Conformance of 'X' to protocol 'P' crosses into main actor-isolated code and can cause data races` | A `@MainActor` type conforms to a non-isolated protocol (e.g. a delegate) | `nonisolated func …` plus an explicit hop; `@MainActor P` conformance (isolated conformances, Swift 6.2) |
| `Task or actor isolated value cannot be sent` | Using a value after passing it to another task | Copy the value, or restructure ownership |
| `Reference to captured var 'x' in concurrently-executing code` | A mutable local is captured by a `@Sendable` closure | Capture a `let` copy: `[x]` |

## 3. Runtime isolation crashes

Swift 6 inserts **dynamic checks** where the compiler cannot prove isolation. For example, a
`@MainActor` closure passed to an Objective-C API that calls it on a background queue. The crash shows:

- `EXC_BREAKPOINT` with `_dispatch_assert_queue_fail`, `swift_task_isCurrentExecutor` or
  `Incorrect actor executor assumption` in the backtrace (see `ios-crash-log-triage`).

Fix: mark the callback/delegate method `nonisolated`, copy out the Sendable data, then hop:

```swift
nonisolated func locationManager(_ m: CLLocationManager, didUpdateLocations locs: [CLLocation]) {
    let coords = locs.map(\.coordinate)          // Sendable copy
    Task { @MainActor in self.apply(coords) }
}
```

For `NotificationCenter` closures, pass `queue: .main` **and** use `MainActor.assumeIsolated { }`
inside, or use the async sequence `for await _ in NotificationCenter.default.notifications(named:)`
from a `@MainActor` task.

## 4. Patterns for SwiftUI apps

```swift
@MainActor @Observable
final class FeedModel {
    private(set) var items: [Item] = []
    private let api: FeedAPI                       // Sendable (struct or actor)
    init(api: FeedAPI) { self.api = api }

    func load() async {
        do { items = try await api.fetch() }       // fetch runs off-main if nonisolated/@concurrent
        catch { /* surface error state */ }
    }
}

struct FeedAPI: Sendable {
    @concurrent func fetch() async throws -> [Item] { /* network + decode */ }  // Swift 6.2; else plain nonisolated async
}
```

- Views are `@MainActor`. Kick off work with `.task { await model.load() }`, which is cancelled automatically.
- Use `.task(id: query)` for "reload when input changes" instead of `onChange` + `Task {}`.
- Prefer `actor` for shared mutable caches; prefer `Mutex` (Synchronization, iOS 18+) for tiny sync state.
- Never block the main actor waiting for async work (`semaphore.wait()`, `DispatchQueue.main.sync`).

## 5. Migration order

1. Enable `SWIFT_STRICT_CONCURRENCY = complete` in Swift 5 mode and fix warnings module by module (leaf packages first).
2. Isolate UI types (`@MainActor`) or adopt default MainActor isolation for the app target.
3. Make model/value types `Sendable` (structs and enums usually only need the conformance).
4. Replace `DispatchQueue`/completion handlers with async APIs at the edges.
5. Audit every `@unchecked Sendable`, `nonisolated(unsafe)` and `@preconcurrency`, and leave a comment for each.
6. Switch `SWIFT_VERSION` to 6.

## 6. Rule index

| Rule | Severity | Flags |
|---|---|---|
| SC001 | info | `DispatchQueue.main.async` |
| SC002 | warning | `DispatchQueue.main.sync` |
| SC003 | warning | `@unchecked Sendable` |
| SC004 | warning | `nonisolated(unsafe)` |
| SC005 | warning | `Task.detached` |
| SC006 | warning | `ObservableObject` with `@Published`, not `@MainActor` |
| SC007 | info | `@Observable` class not `@MainActor` |
| SC008 | warning | mutable `static var` / global `var` |
| SC009 | info | `MainActor.assumeIsolated` |
| SC010 | info | `@preconcurrency import` |

When default isolation is MainActor, SC006–SC008 are skipped because unannotated declarations are already isolated.
