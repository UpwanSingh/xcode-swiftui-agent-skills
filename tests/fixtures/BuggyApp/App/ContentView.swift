// Fixture: deliberately buggy SwiftUI code. Every bug is expected by tests/test_skills.py.
import SwiftUI

@Observable
final class CartModel {
    var items: [String] = []
    @Published var total: Double = 0          // SUI005
}

final class LegacyStore: ObservableObject {    // SC006 (not @MainActor)
    @Published var names: [String] = []
}

struct RootView: View {
    @State var selection = 0                  // SUI001
    @StateObject private var cart = CartModel()   // SUI004
    @ObservedObject var store = LegacyStore()     // SUI003

    var body: some View {
        NavigationStack {
            TabView(selection: $selection) {       // SUI011
                ListScreen().tag(0)
                Text("Settings").tag(1)
            }
        }
        .navigationTitle("Home")                   // SUI010
    }
}

struct ListScreen: View {
    let rows = ["a", "b", "c"]
    @State private var query = ""
    @State private var size: CGSize = .zero

    var body: some View {
        List {
            ForEach(Array(rows.enumerated()), id: \.offset) { pair in   // SUI006
                NavigationLink(pair.element, value: pair.element)
                    .navigationDestination(for: String.self) { Text($0) }   // SUI012
            }
        }
        .id(UUID())                                                         // SUI006
        .onChange(of: query) { _, newValue in
            query = newValue.lowercased()                                   // SUI007
        }
        .background(
            GeometryReader { proxy in
                Color.clear.onAppear { size = proxy.size }                 // SUI008
            }
        )
    }
}

struct EditorView: View {
    @State private var draft: String
    init(text: String) {
        _draft = State(initialValue: text)                                 // SUI002
    }
    var body: some View {
        let _ = Self._printChanges()                                       // SUI013
        NavigationView { TextField("Draft", text: $draft) }                // SUI009
    }
}

// A comment mentioning @ObservedObject var x = Foo() must not be reported.
let banner = "NavigationView { } inside a string must not be reported"
