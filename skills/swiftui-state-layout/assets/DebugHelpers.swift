// DebugHelpers.swift: drop into an app target to visualise renders and layout.
// Everything compiles to a no-op outside DEBUG builds, so it is safe to leave in place.
//
// Usage:
//   FeedRow(item: item)
//       .debugRenderCount()          // background colour changes on every body evaluation
//       .debugBorder(.orange)        // shows the frame SwiftUI actually assigned
//       .debugSize("FeedRow")        // logs size changes without causing a layout loop
//
// In a view's body:  let _ = debugChanges(Self.self)   // wraps Self._printChanges()

import SwiftUI
import os

private let layoutLog = Logger(subsystem: Bundle.main.bundleIdentifier ?? "app", category: "layout-debug")

extension View {
    /// Tints the background with a random colour each time the view's body is evaluated.
    /// A view that flickers colour while idle is being re-rendered by a feedback loop.
    @ViewBuilder
    func debugRenderCount() -> some View {
        #if DEBUG
        background(Color(hue: .random(in: 0...1), saturation: 0.8, brightness: 1).opacity(0.25))
        #else
        self
        #endif
    }

    /// Draws the view's frame.
    @ViewBuilder
    func debugBorder(_ color: Color = .red, width: CGFloat = 1) -> some View {
        #if DEBUG
        border(color, width: width)
        #else
        self
        #endif
    }

    /// Logs the view's size when it changes (unified log, category "layout-debug").
    /// Uses onGeometryChange, which measures without participating in layout.
    @ViewBuilder
    func debugSize(_ label: String) -> some View {
        #if DEBUG
        onGeometryChange(for: CGSize.self) { proxy in proxy.size } action: { size in
            layoutLog.debug("\(label, privacy: .public) size = \(size.width, format: .fixed(precision: 1)) x \(size.height, format: .fixed(precision: 1))")
        }
        #else
        self
        #endif
    }
}

/// Call as `let _ = debugChanges(Self.self)` at the top of `body` to print what triggered it.
@MainActor @discardableResult
func debugChanges<V: View>(_ type: V.Type) -> Bool {
    #if DEBUG
    V._printChanges()
    #endif
    return true
}
