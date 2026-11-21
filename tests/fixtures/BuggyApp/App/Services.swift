// Fixture: concurrency, Firebase, MapKit, privacy and resource bugs.
import SwiftUI
import MapKit
import CoreLocation
import AVFoundation
import FirebaseCore
import FirebaseFirestore

enum Cache {
    static var shared: [String: Data] = [:]          // SC008
    static var computed: Int { 42 }                  // computed: not reported
    static let constant = 1                          // let: not reported
}

var globalCounter = 0                                // SC008 (global)

final class Unsafe: @unchecked Sendable {            // SC003
    nonisolated(unsafe) static var hits = 0          // SC004
    func refresh() {
        Task.detached { print("work") }              // SC005
        DispatchQueue.main.async { print("ui") }     // SC001
        DispatchQueue.main.sync { print("ui") }      // SC002
    }
}

@MainActor
final class SafeStore: ObservableObject {           // @MainActor: SC006 not reported
    @Published var value = 0
    static var instances = 0                         // isolated by @MainActor type: not reported
}

final class LocationModel: NSObject, CLLocationManagerDelegate {
    func start() {
        let manager = CLLocationManager()            // MK001
        manager.requestWhenInUseAuthorization()      // MK002 (no NSLocationWhenInUseUsageDescription)
        manager.allowsBackgroundLocationUpdates = true   // MK004
        manager.startUpdatingLocation()              // MK011
    }
    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {}  // MK010
}

struct MapScreen: View {
    @State private var region = MKCoordinateRegion(
        center: CLLocationCoordinate2D(latitude: -122.41, longitude: 37.77),   // MK005 (swapped)
        span: MKCoordinateSpan(latitudeDelta: 0, longitudeDelta: 0.05))        // MK005 (zero span)
    var body: some View {
        Map(coordinateRegion: $region)                                         // MK009
    }
}

final class Coordinator: NSObject, MKMapViewDelegate {
    func mapView(_ mapView: MKMapView, viewFor annotation: MKAnnotation) -> MKAnnotationView? {   // MK006
        mapView.dequeueReusableAnnotationView(withIdentifier: "pin", for: annotation)             // MK007
    }
}

struct LegacyMap: UIViewRepresentable {
    var region: MKCoordinateRegion
    var pins: [MKPointAnnotation]
    func makeUIView(context: Context) -> MKMapView { MKMapView() }
    func updateUIView(_ view: MKMapView, context: Context) {
        view.setRegion(region, animated: true)       // MK008
        view.addAnnotations(pins)                    // MK008
    }
}

final class Camera {
    let session = AVCaptureSession()                 // PL004 (no NSCameraUsageDescription)
    func openMaps() -> Bool {
        UIApplication.shared.canOpenURL(URL(string: "comgooglemaps://")!)    // PL007
    }
}

final class Assets {
    let config = Bundle.main.url(forResource: "config", withExtension: "json")        // exists
    let missing = Bundle.main.url(forResource: "seed", withExtension: "json")         // BR001
    let logo = UIImage(named: "Logo")                                                 // exists
    let ghost = Image("GhostIcon")                                                    // BR007
    let symbol = Image(systemName: "star")                                            // not an asset
    let font = UIFont(name: "Inter-Regular", size: 14)                                // BR006
}

final class Feed {
    let db = Firestore.firestore()
    func load() {
        db.collection("posts").getDocuments { _, _ in }        // FR007 (no rule)
        db.collection("users").getDocuments { _, _ in }        // covered by rule
        db.useEmulator(withHost: "localhost", port: 8080)      // FB011
    }
}
