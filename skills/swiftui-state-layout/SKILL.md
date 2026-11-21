---
name: swiftui-state-layout
description: Diagnose and fix SwiftUI runtime bugs - views that re-render in a loop or never update, state that resets, @State/@Binding/@StateObject/@ObservedObject/@Observable/@Bindable/@Environment misuse, Swift 6 strict-concurrency and actor-isolation errors (Sendable, @MainActor, "Publishing changes from background threads"), NavigationStack/TabView title and toolbar glitches, navigationDestination links that do nothing, and GeometryReader/layout feedback loops. Use when a SwiftUI view flickers, loops, loses state, shows the wrong title, or when Swift 6 concurrency diagnostics appear.
license: MIT
compatibility: Scripts need Python 3.9+ (stdlib only) and run on any OS. Guidance targets Xcode 16+/Swift 6 and iOS 17+ APIs, with notes for older targets.
metadata:
  version: "1.0.0"
  author: UpwanSingh
---

# SwiftUI state, identity, navigation and layout debugging

SwiftUI bugs are nearly always one of four things: **wrong ownership** (who creates and keeps
the state), **unstable identity** (the view is a "new" view on every render), **a feedback loop**
(rendering writes state that triggers rendering), or **isolation** (state mutated off the main
actor). Work out which one you have before you edit code.

## Quick triage

| Symptom | Most likely cause | Go to |
|---|---|---|
| CPU at 100%, body runs forever, "AttributeGraph: cycle detected" | Feedback loop (onChange / GeometryReader / onAppear writes state) | Workflow 1 |
| State resets when parent updates, text field loses input | Unstable identity (`.id(UUID())`, `ForEach` by offset, `if/else` branches) or `@ObservedObject` created inline | Workflow 1 |
| View never updates after a model change | Wrong wrapper, non-observed copy, `@State` seeded from init | Workflow 2 |
| "Publishing changes from background threads", Swift 6 errors | Isolation | Workflow 3 |
| Missing/wrong title or toolbar, double nav bars, link does nothing | Modifier placement, TabView/NavigationStack nesting | Workflow 4 |
| Jumping layout, zero sizes, infinite height | Geometry misuse, competing frames | Workflow 5 |

## Scripts

All scripts are read-only, accept files or directories, print compiler-style `path:line:`
findings and support `--json` and `--list-rules`.

```bash
python3 scripts/swiftui_lint.py Sources/                # SUI001–SUI013 state/identity/nav/layout rules
python3 scripts/concurrency_check.py Sources/ --settings App.xcodeproj   # SC001–SC010 + language mode
```

Exit status: `0` clean, `1` findings at or above `--fail-on` (default `error`), `2` bad input.
Treat findings as leads: confirm each one in the code before you change it. Through the `ios-debug`
MCP server the same checks are the `swiftui_lint` and `swift_concurrency_check` tools.

## Workflow 1: re-render loops and lost state

1. **Measure.** Add `let _ = Self._printChanges()` as the first line of the suspect `body`
   (Debug builds only). Run the app and read the console:
   - `@identity` changed: the view was re-created, so look at identity (step 3).
   - `_name changed` printed over and over with no user input: a feedback loop (step 4).
   - `@self changed`: the parent passed new values, so look at the parent.
2. **Scan.** Run `scripts/swiftui_lint.py` on the feature folder. Rules SUI006, SUI007 and SUI008
   point straight at loops and identity bugs.
3. **Identity fixes.**
   - Remove `.id(UUID())`/`.id(Date())`. Use a stable model id, or change the id only when you
     really want a reset.
   - Use `ForEach(items)` with `Identifiable` models, not `enumerated()` + `\.offset`.
   - Replace `if cond { A() } else { A().modifier() }` with one `A()` plus a conditional
     modifier value (for example `.opacity(cond ? 1 : 0)`). Each branch has its own identity.
4. **Loop fixes.**
   - `onChange(of: x)` that writes `x`: guard it (`if x != normalized { x = normalized }`) or
     normalise in the model's setter.
   - GeometryReader writing size into state that changes the same layout: measure with
     `.onGeometryChange(for: CGSize.self) { $0.size } action: { size = $0 }` (Xcode 16+,
     back-deploys to iOS 16). Make sure the measured view's size does not depend on `size`.
   - `onAppear`/`task` that mutates state the parent uses to rebuild the child: move the
     work up to the model, or key it with `.task(id:)`.
   - A computed `Binding(get:set:)` whose setter writes something the getter reads: bind
     straight to model storage instead.
5. **Verify.** `_printChanges` goes quiet once input stops. For a hard case, profile with the
   Instruments **SwiftUI** template and check that body counts stay flat while idle.

## Workflow 2: state ownership

Choose the wrapper by **who creates the value** and **what kind of value it is**:

| Situation | iOS 17+ (`@Observable`) | iOS 14–16 (`ObservableObject`) |
|---|---|---|
| View creates and owns a model | `@State private var model = Model()` | `@StateObject private var model = Model()` |
| Parent passes a model in | `let model: Model` (or `var`) | `@ObservedObject var model: Model` |
| Child needs `$model.field` bindings | `@Bindable var model: Model` | `@ObservedObject` gives `$model.field` |
| App-wide / subtree dependency | `.environment(model)` + `@Environment(Model.self) private var model` | `.environmentObject(model)` + `@EnvironmentObject var model: Model` |
| Value type owned by the view | `@State private var value` | same |
| Value owned by the parent and edited by the child | `@Binding var value` | same |

Rules that prevent most bugs:
- `@State` and `@StateObject` are **always `private`**. A value passed through the
  initializer only sets the *initial* value (SUI001, SUI002).
- Never write `@ObservedObject var x = Model()`. It is re-created on every parent render (SUI003).
- Never mix systems: an `@Observable` class with `@StateObject`/`@ObservedObject`/`@Published`
  fails to compile or stops updating (SUI004, SUI005).
- Observation tracks **properties read in `body`**. Reading a model only inside a closure or a
  `Task` does not subscribe the view to it.
- Passing a struct copy of model data into a child cuts the link. Pass the model, or a `Binding`.

Details and edge cases: [references/state-and-observation.md](references/state-and-observation.md).

## Workflow 3: Swift 6 concurrency and actor isolation

1. **Know the mode.** Run `scripts/concurrency_check.py <dir>`. It prints `SWIFT_VERSION`,
   `SWIFT_STRICT_CONCURRENCY`, `SWIFT_DEFAULT_ACTOR_ISOLATION` and "approachable concurrency".
   In Swift 6 mode a data race is a compile error. In Swift 5 mode with `complete` checking it
   is a warning.
2. **Read the first diagnostic only.** Later ones often cascade from it. Map the message:

| Diagnostic (abridged) | Meaning | Usual fix |
|---|---|---|
| `Main actor-isolated property 'x' can not be mutated from a nonisolated context` | Background code touches UI state | Make the caller `@MainActor`, or `await MainActor.run {}` |
| `Call to main actor-isolated … in a synchronous nonisolated context` | Sync code calls a `@MainActor` API | Make the caller `@MainActor` or `async` and `await` it |
| `Static property 'x' is not concurrency-safe …` | Mutable global state | `static let`, `@MainActor static var`, or move it into an actor |
| `Sending 'x' risks causing data races` | A non-Sendable value crosses isolation | Make it `Sendable`, or create it on the destination side |
| `Capture of 'self' with non-sendable type … in a @Sendable closure` | Closure runs elsewhere | Isolate the type to `@MainActor`, or capture only Sendable values |
| `Type 'X' does not conform to the 'Sendable' protocol` | Value shared across tasks | Prefer value types; `final class` with `let` Sendable fields; an actor |
| Runtime: `Incorrect actor executor assumption` / `dispatch_assert_queue` crash | Non-isolated callback reached `@MainActor` code | Mark the delegate method `nonisolated` and hop with `Task { @MainActor in … }` |

3. **UI models belong on the main actor.** Annotate `ObservableObject`/`@Observable` view models
   with `@MainActor` (SC006/SC007), or turn on default MainActor isolation for the app target.
4. **Do background work explicitly.** Use a `nonisolated` async function, an actor, or (Swift
   6.2+) `@concurrent`. Avoid `Task.detached` (SC005) and `DispatchQueue.main.sync` (SC002).
5. **Escape hatches need a justification comment.** `@unchecked Sendable`,
   `nonisolated(unsafe)`, `@preconcurrency import` and `MainActor.assumeIsolated` are acceptable
   only with a lock or an invariant you can state.

Full catalog and migration order: [references/swift6-concurrency.md](references/swift6-concurrency.md).

## Workflow 4: NavigationStack, TabView, titles and toolbars

1. Run `scripts/swiftui_lint.py`. SUI009–SUI012 cover the common structural mistakes.
2. Check the structure against this shape:
   ```swift
   TabView {                       // root
       NavigationStack {           // one stack per tab
           FeedList()
               .navigationTitle("Feed")           // inside the stack, on the content
               .toolbar { … }
               .navigationDestination(for: Post.ID.self) { PostView(id: $0) }  // outside List rows
       }
       .tabItem { Label("Feed", systemImage: "house") }
   }
   ```
3. Typical fixes:
   - Title or toolbar missing: the modifier is on the `NavigationStack` (SUI010). Move it onto the content.
   - Wrong title or double bars in tabs: a `TabView` inside a `NavigationStack` (SUI011), or
     nested stacks. Use one stack per tab and none inside pushed views.
   - A link does nothing: `.navigationDestination` sits inside a `List`/`LazyVStack` (SUI012),
     or no destination matches the value's type. The runtime log says so; check it with the
     `ios-crash-log-triage` skill's `sim_logs.py scan`.
   - Large title doesn't collapse: the scroll view must be the first scrollable child of the content.
   - Programmatic navigation: own `@State private var path = NavigationPath()` (or `[Route]`)
     where the stack lives, and append values. Don't push from inside `body`.

More patterns: [references/navigation-and-layout.md](references/navigation-and-layout.md).

## Workflow 5: layout and geometry

1. Make the frames visible: `.border(.red)` on each level (see `assets/DebugHelpers.swift`
   for `debugBorder()` and `debugRenderCount()`).
2. Remember the layout contract: **the parent proposes, the child chooses, the parent places**.
   Most "wrong size" bugs come from a child that ignores the proposal (`fixedSize`, an
   unbounded `GeometryReader`, or `.frame(maxWidth: .infinity)` in the wrong place).
3. Use `GeometryReader` only as an overlay/background to *measure*, never as a layout
   container for content (it takes all the space offered).
4. Prefer `Layout`, `ViewThatFits`, `containerRelativeFrame` and `.onGeometryChange` to
   preference-key plumbing.
5. NaN/negative frames ("Invalid frame dimension") come from dividing by a zero size before
   the first layout pass. Clamp the value and guard the division.

## Failure modes to avoid while fixing

- Do not "fix" a loop with `DispatchQueue.main.async { state = … }`. That hides the cycle and
  makes it non-deterministic.
- Do not add `.id(...)` to force refreshes. Fix the data flow instead.
- Do not sprinkle `@MainActor` on every function. Isolate the *type* that owns UI state.
- Do not silence Swift 6 errors with `@unchecked Sendable` without a lock.
- Leave `Self._printChanges()` only in `#if DEBUG` (SUI013).

## Worked examples

**A temperature converter pins the CPU.**
`_printChanges` alternates `_celsius changed` / `_fahrenheit changed` without end, and the console
shows `onChange(of: Double) action tried to update multiple times per frame` (`sim_logs.py scan`
reports RW005). Two `onChange` handlers keep two `@State` values in sync. Floating-point
round-off means each conversion changes the other value slightly, so they ping-pong forever.
Fix: keep one source of truth (`celsius`) and expose Fahrenheit as a computed
`Binding(get: { c * 9/5 + 32 }, set: { celsius = ($0 - 32) * 5/9 })`. SUI007 flags the simpler
form, where a handler writes the value it observes.

**Counter resets whenever the parent refreshes.**
`RowView(count: item.count)` holds `@State var count` (SUI001/SUI002). The parent re-renders and
the row keeps its first value until its identity changes, after which it resets. Fix: the row
takes `@Binding var count`, or reads `item` directly.

**Swift 6: `sending 'self.model' risks causing data races` on `.task { await model.load() }`.**
`concurrency_check.py` reports language mode 6 and SC007 on `@Observable final class FeedModel`.
The view is main-actor isolated, but `model` is a non-Sendable, non-isolated class. Calling its
async method moves it across isolation domains. Fix: annotate `FeedModel` with `@MainActor`
(or set default MainActor isolation for the app target). `try await api.fetch()` inside `load()`
still runs off the main actor when `fetch` is nonisolated, and `items = …` is assigned back on
the main actor.

## References

- [references/state-and-observation.md](references/state-and-observation.md): ownership, identity, Observation tracking rules, migration from ObservableObject
- [references/swift6-concurrency.md](references/swift6-concurrency.md): diagnostics catalog, build settings, migration order, runtime checks
- [references/navigation-and-layout.md](references/navigation-and-layout.md): NavigationStack/TabView patterns, toolbar/title rules, layout debugging
- [assets/DebugHelpers.swift](assets/DebugHelpers.swift): drop-in `#if DEBUG` view modifiers for render counts, borders and size probes
