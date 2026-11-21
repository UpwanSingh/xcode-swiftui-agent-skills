// FirebaseBootstrap.swift: reference initialization order for a SwiftUI app.
// Avoids: FB007 (double configure), FB008 (Firebase used before configure), FB011 (emulator in release).

import SwiftUI
import FirebaseCore
import FirebaseAuth
import FirebaseFirestore

@main
struct ExampleApp: App {
    // Stored properties initialise BEFORE init() runs, so never put Firestore.firestore() here.
    @State private var session: SessionModel

    init() {
        FirebaseBootstrap.configureOnce()
        _session = State(initialValue: SessionModel())   // created after configure()
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(session)
        }
    }
}

enum FirebaseBootstrap {
    /// Safe to call from previews and tests; configures exactly once.
    static func configureOnce() {
        guard FirebaseApp.app() == nil else { return }
        // For per-environment configs, load an explicit file instead:
        //   let path = Bundle.main.path(forResource: "GoogleService-Info-Staging", ofType: "plist")!
        //   FirebaseApp.configure(options: FirebaseOptions(contentsOfFile: path)!)
        FirebaseApp.configure()

        let db = Firestore.firestore()
        let settings = db.settings                 // change settings before the first read/write
        settings.cacheSettings = PersistentCacheSettings()
        #if DEBUG
        if ProcessInfo.processInfo.environment["USE_FIREBASE_EMULATOR"] == "1" {
            settings.host = "localhost:8080"
            settings.cacheSettings = MemoryCacheSettings()
            settings.isSSLEnabled = false
            Auth.auth().useEmulator(withHost: "localhost", port: 9099)
        }
        #endif
        db.settings = settings
    }
}

@MainActor @Observable
final class SessionModel {
    private(set) var user: User?
    @ObservationIgnored private var handle: AuthStateDidChangeListenerHandle?

    init() {
        // Auth restores the signed-in user asynchronously; read user data only after this fires.
        handle = Auth.auth().addStateDidChangeListener { [weak self] _, user in
            MainActor.assumeIsolated { self?.user = user }   // listener callbacks arrive on the main thread
        }
    }
}

struct RootView: View {
    @Environment(SessionModel.self) private var session
    var body: some View {
        if let user = session.user {
            Text("Signed in as \(user.uid)")
        } else {
            Text("Signed out")
        }
    }
}
