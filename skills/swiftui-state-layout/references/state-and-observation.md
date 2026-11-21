# State, identity and Observation: reference

## Contents
1. How SwiftUI decides to re-render
2. Identity: structural vs explicit
3. Observation (`@Observable`) tracking rules
4. `ObservableObject` rules (iOS 14–16)
5. Initialising state from parameters
6. Migration: ObservableObject → @Observable
7. Rule index (swiftui_lint.py)

## 1. How SwiftUI decides to re-render

A view's `body` runs again when **a dependency it read during the last `body` evaluation
changes**. Dependencies are:
- its stored inputs (properties passed by the parent; SwiftUI compares them),
- `@State` / `@Binding` / `@Environment` values it read,
- properties of `@Observable` objects that it **read in `body`**,
- *any* `@Published` change of an `ObservableObject` held by `@StateObject`/`@ObservedObject`/`@EnvironmentObject`
  (the whole object invalidates, not just the property).

`Self._printChanges()` (Debug only) prints what triggered the evaluation:

```
FeedRow: @self changed.            // parent passed different inputs
FeedRow: @identity changed.        // view was re-created (new identity)
FeedRow: _isExpanded changed.      // a @State it owns changed
FeedRow: _model changed.           // a wrapped dependency changed
```

`Self._logChanges()` (iOS 17+) writes the same information to the unified log (subsystem
`com.apple.SwiftUI`, category `Changed Body Properties`), so you can read it with `sim_logs.py logs`.

## 2. Identity

- **Structural identity**: the view's position in the view tree. `if/else` creates *two* identities. Switching
  branches destroys one subtree and its `@State`, and creates the other.
- **Explicit identity**: `.id(x)` and `ForEach` ids. When the id changes, the old view is destroyed.

| Pattern | Problem | Fix |
|---|---|---|
| `.id(UUID())` | New identity on each render: state resets, onAppear loops | Stable id, or change it only to reset deliberately |
| `ForEach(Array(a.enumerated()), id: \.offset)` | Insert/delete shifts identity to other rows | `ForEach(a)` with `Identifiable`, or `id: \.element.id` |
| `ForEach(names, id: \.self)` with duplicates | Duplicate ids give undefined behaviour and runtime warnings | Unique ids |
| `if loading { Spinner() } else { Content() }` around stateful content | Content state lost when toggling | Keep `Content()` mounted; overlay the spinner |
| `AnyView` everywhere | Defeats structural diffing and costs performance | `@ViewBuilder`, `some View`, `Group` |

## 3. Observation (`@Observable`, iOS 17+)

- Tracking is **per property, per read**. If `body` reads `model.title`, only `title` changes invalidate it.
- Reads inside closures that run later (`Button` actions, `.task`, `onAppear`) are **not** tracked.
- Reads in a child view are tracked by the child. Passing `model` (the reference) down is cheap and precise.
- Copying a value out (`let title = model.title`) and passing `title` down works, but the child only
  updates when the parent re-renders.
- Use `@ObservationIgnored` for caches and dependencies that must not trigger UI updates.
- Computed properties are tracked through the stored properties they read.
- Collections: mutating an element of an `@Observable` array property invalidates readers of the array.
  For large lists, make the element type `@Observable` itself and pass elements to rows.
- `@Bindable var model` (or `@Bindable var model = model` inside `body`) turns a plain reference into
  `$model.field` bindings.
- Environment: `.environment(model)` / `@Environment(Model.self) private var model`. Crashes at runtime if
  missing. Use `@Environment(Model.self) private var model: Model?` for optional injection.

## 4. `ObservableObject` (iOS 14–16 or legacy code)

- `@StateObject` creates the object once per view identity (its initializer is an autoclosure).
- `@ObservedObject` does **not** own the object. Never initialise it inline (SUI003).
- Every `@Published` change invalidates **all** views observing the object. Split large models.
- `objectWillChange` must fire on the main thread, so mark the class `@MainActor` (concurrency_check SC006).
- Nested `ObservableObject`s don't propagate: a child object's `@Published` change does not invalidate
  views observing the parent. Observe the child directly, or forward `objectWillChange`.

## 5. Initialising state from parameters

`@State` ignores later values passed to `init`. These are the right patterns:

```swift
// Parent owns the value -> binding
struct Editor: View { @Binding var text: String }

// Local draft that resets when the input changes
struct Editor: View {
    let original: String
    @State private var draft = ""
    var body: some View {
        TextField("Text", text: $draft)
            .onAppear { draft = original }
            .onChange(of: original) { _, new in draft = new }
    }
}

// Or force a fresh view per input
Editor(original: item.text).id(item.id)
```

`_draft = State(initialValue: original)` inside `init` works **only** for the first creation (SUI002).

## 6. Migration: ObservableObject → @Observable

| Before | After |
|---|---|
| `final class M: ObservableObject { @Published var x = 0 }` | `@Observable final class M { var x = 0 }` |
| `@StateObject private var m = M()` | `@State private var m = M()` |
| `@ObservedObject var m: M` | `var m: M` (or `@Bindable var m: M` for bindings) |
| `@EnvironmentObject var m: M` | `@Environment(M.self) private var m` |
| `.environmentObject(m)` | `.environment(m)` |
| Combine `$x.sink` on a published property | `withObservationTracking`, `Observations` (iOS 26), or explicit callbacks |

Caveat: `@State private var m = M()` evaluates `M()` every time the *parent* re-creates the view
struct. The instance is discarded after first install, but the initializer still runs, so keep model
initialisers cheap and side-effect free. Move work to `.task`.

## 7. Rule index

| Rule | Severity | Meaning |
|---|---|---|
| SUI001 | warning | `@State`/`@StateObject` not private |
| SUI002 | warning | `_x = State(initialValue:)` in init |
| SUI003 | error | `@ObservedObject var x = T()` |
| SUI004 | error | `@Observable` type with StateObject/ObservedObject/EnvironmentObject |
| SUI005 | warning | `@Published` inside `@Observable` |
| SUI006 | warning | `.id(UUID())`, ForEach by offset |
| SUI007 | warning | onChange writes its own observed value |
| SUI008 | warning | GeometryReader writes size into state |
| SUI009 | info | NavigationView |
| SUI010 | warning | `.navigationTitle`/`.toolbar` on the NavigationStack |
| SUI011 | warning | TabView inside NavigationStack |
| SUI012 | warning | navigationDestination inside a lazy container |
| SUI013 | info | `_printChanges` left in code |
