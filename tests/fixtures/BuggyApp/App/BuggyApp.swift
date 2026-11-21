import SwiftUI
import FirebaseCore
import FirebaseFirestore

@main
struct BuggyApp: App {
    let db = Firestore.firestore()                 // FB008: runs before init() body
    @State private var cart = CartModel()          // FB008 (warning)

    init() {
        FirebaseApp.configure()
    }

    var body: some Scene {
        WindowGroup { RootView() }
    }
}

final class Preview {
    func setUp() {
        FirebaseApp.configure()                    // FB007: second call
    }
}
