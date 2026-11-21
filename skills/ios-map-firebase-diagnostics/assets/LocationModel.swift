// LocationModel.swift: retained CLLocationManager + authorization + SwiftUI Map (iOS 17+).
// Avoids: MK001 (local manager), MK002/MK003 (authorization), MK009 (deprecated region binding),
// MK010 (delegate not set), MK011 (updates never stopped).
//
// Info.plist: NSLocationWhenInUseUsageDescription = "Shows nearby places on the map."

import CoreLocation
import MapKit
import SwiftUI

@MainActor @Observable
final class LocationModel: NSObject {
    private(set) var authorization: CLAuthorizationStatus = .notDetermined
    private(set) var lastLocation: CLLocation?
    private let manager = CLLocationManager()   // stored property: retained for the model's lifetime

    override init() {
        super.init()
        manager.delegate = self                       // before any request
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
        authorization = manager.authorizationStatus
    }

    func requestAccess() {
        if authorization == .notDetermined { manager.requestWhenInUseAuthorization() }
    }

    func start() {
        guard authorization == .authorizedWhenInUse || authorization == .authorizedAlways else { return }
        manager.startUpdatingLocation()
    }

    func stop() { manager.stopUpdatingLocation() }
}

extension LocationModel: CLLocationManagerDelegate {
    // Delegate callbacks are delivered on the thread that created the manager (main here),
    // but the protocol is nonisolated: copy Sendable values and hop explicitly.
    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        let status = manager.authorizationStatus
        Task { @MainActor in
            self.authorization = status
            self.start()
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let latest = locations.last else { return }
        Task { @MainActor in self.lastLocation = latest }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        // kCLErrorLocationUnknown (0) is transient; kCLErrorDenied (1) means the user said no.
    }
}

struct NearbyMap: View {
    @State private var location = LocationModel()
    @State private var position: MapCameraPosition = .userLocation(fallback: .automatic)

    var body: some View {
        Map(position: $position) {
            UserAnnotation()
        }
        .mapControls { MapUserLocationButton() }
        .overlay(alignment: .bottom) {
            if location.authorization == .denied {
                Link("Enable location in Settings", destination: URL(string: UIApplication.openSettingsURLString)!)
                    .padding()
                    .background(.thinMaterial, in: Capsule())
            }
        }
        .onAppear { location.requestAccess() }
        .onDisappear { location.stop() }
    }
}
