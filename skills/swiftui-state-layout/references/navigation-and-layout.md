# Navigation, toolbars and layout: reference

## Contents
1. NavigationStack rules
2. TabView + navigation
3. Titles, toolbars and bars
4. Programmatic navigation and deep links
5. Layout debugging
6. Geometry without feedback loops

## 1. NavigationStack rules

- **One stack per navigation hierarchy.** Do not put a `NavigationStack` inside a pushed view.
  The result is a nested bar, and back buttons disappear or double up.
- Navigation modifiers (`navigationTitle`, `toolbar`, `navigationBarTitleDisplayMode`,
  `navigationDestination`, `searchable`) go **on content inside** the stack, not on the stack (SUI010).
- `navigationDestination(for:)` must be visible from the link: declare it on the root content or on a
  non-lazy ancestor. Inside `List`/`LazyVStack` rows it is ignored until the row is realised (SUI012),
  and runtime logs say *"Do not put a navigation destination modifier inside a lazy container"*.
- Register each value type **once** per stack. Duplicate registrations log
  *"Only root-level navigation destinations are effective…"*.
- `NavigationLink(value:)` without a matching destination logs *"A NavigationLink is presenting a value of
  type … but there is no matching navigationDestination declaration"* and does nothing.
- `NavigationLink(destination:)` builds the destination view **eagerly** for each row. Keep destination
  initialisers cheap, or switch to value-based links.

## 2. TabView + navigation

```swift
TabView(selection: $tab) {
    Tab("Feed", systemImage: "house", value: .feed) {           // iOS 18 API; use .tabItem on older targets
        NavigationStack(path: $feedPath) { FeedView() }
    }
    Tab("Settings", systemImage: "gear", value: .settings) {
        NavigationStack { SettingsView() }
    }
}
```

- A `TabView` inside a `NavigationStack` (SUI011) shares one bar across tabs: titles flicker, show the
  previous tab's title, or the toolbar belongs to the wrong tab.
- Keep a separate path per tab if you need "tap the tab again to pop to root": observe re-selection and clear that tab's path.
- The tab bar hides on pushed views only if you ask for it: `.toolbar(.hidden, for: .tabBar)` on the destination.

## 3. Titles, toolbars and bars

| Symptom | Cause | Fix |
|---|---|---|
| Title missing | Modifier on the stack, or on a view that isn't the stack's content | Move it onto the root content |
| Title flashes the previous screen's | Title computed from async state that is empty at first render | Provide a placeholder; set the title from non-optional state |
| Large title never collapses | Scroll view isn't the first child (e.g. a `VStack { Header(); List }`) | Make `List`/`ScrollView` the root; put the header inside it |
| Toolbar items duplicated | `.toolbar` on several levels of the same hierarchy | Declare items where the screen is defined |
| Inline/large title jumps on push | Mixed display modes | Set `.navigationBarTitleDisplayMode` on each destination |
| Search bar appears on the wrong screen | `.searchable` on the stack instead of the content | Put it on the content view |
| Bar background wrong after push | `toolbarBackground` set on one screen only | `.toolbarBackground(.visible, for: .navigationBar)` on each screen that needs it |

## 4. Programmatic navigation and deep links

```swift
enum Route: Hashable { case post(Post.ID), profile(User.ID) }

@MainActor @Observable final class Router { var path: [Route] = [] }

NavigationStack(path: $router.path) {
    FeedView()
        .navigationDestination(for: Route.self) { route in
            switch route {
            case .post(let id): PostView(id: id)
            case .profile(let id): ProfileView(id: id)
            }
        }
}
.onOpenURL { url in router.path = Route.parse(url) }    // replace the path; don't append inside body
```

- Mutate `path` from actions and `.onOpenURL`/`.task`, never during `body`.
- Use `[Route]` rather than `NavigationPath` when you need to inspect or persist the path (`Codable`).
- Sheets: `.sheet(item:)` with an `Identifiable` item avoids "sheet shows stale content" bugs that
  come from `.sheet(isPresented:)` plus a separate selection.

## 5. Layout debugging

1. `.border(.red)` at each level, or `debugBorder()` from `assets/DebugHelpers.swift`.
2. Xcode **Debug View Hierarchy** (works with SwiftUI). Inspect frames and constraints of hosted views.
3. Read sizes with `onGeometryChange` (see section 6) and print them.
4. Layout contract: the parent proposes a size, the child picks its size, and the parent positions the child.
   - `frame(width:height:)` proposes a fixed size and then centres the child.
   - `frame(maxWidth: .infinity)` makes the frame *flexible*; the child inside may still be small.
   - `fixedSize()` makes the child ignore the proposal. This causes text overflow and truncation bugs.
   - `layoutPriority(1)` decides who gets space first in stacks.
5. Prefer `ViewThatFits`, `Grid`, `containerRelativeFrame` and custom `Layout` to manual geometry maths.

## 6. Geometry without feedback loops

```swift
// Measure without affecting layout
content
    .onGeometryChange(for: CGFloat.self) { proxy in proxy.size.width } action: { width = $0 }
```

- If `width` feeds back into `content`'s size, you have built a loop (SUI008, "AttributeGraph: cycle
  detected"). Break it: derive the layout from the *container's* size (measured on a parent that does
  not depend on the value), or quantise the value (`(width / 10).rounded() * 10`) so small changes stop propagating.
- Never call `GeometryReader { … }` as the layout container of scrolling content. It takes all the offered
  space and reports 0 inside some lazy containers before the first layout pass.
- Guard divisions: `let ratio = total > 0 ? part / total : 0`, to avoid "Invalid frame dimension
  (negative or non-finite)".
