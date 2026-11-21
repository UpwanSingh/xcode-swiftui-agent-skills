# MapKit and Core Location: reference

## Contents
1. Authorization flow
2. SwiftUI Map (iOS 17+)
3. MKMapView in UIViewRepresentable
4. Regions, spans and coordinates
5. Annotations and clustering
6. Simulator and testing
7. Rule index (mapkit_check.py)

## 1. Authorization flow

```
Info.plist key present? ──no──► prompt never shows, status stays .notDetermined (MK002)
        │yes
Manager retained? ──no──► prompt flashes and disappears, no callbacks (MK001)
        │yes
delegate set? ──no──► no didChangeAuthorization / didUpdateLocations (MK010)
        │yes
requestWhenInUseAuthorization() ──► locationManagerDidChangeAuthorization(_:)
        │
.authorizedWhenInUse / .authorizedAlways ──► startUpdatingLocation() / Map shows UserAnnotation
.denied / .restricted ──► explain + link to Settings (the prompt is never shown again)
```

- `authorizationStatus` is an instance property (iOS 14+). Read it in the delegate callback, not right
  after requesting. The callback also fires once when the manager is created.
- **Precise vs approximate**: check `accuracyAuthorization`. With `.reducedAccuracy` the position may be
  off by kilometres. Request a temporary upgrade with
  `requestTemporaryFullAccuracyAuthorization(withPurposeKey:)` (needs `NSLocationTemporaryUsageDescriptionDictionary`).
- iOS 17+/18+: `CLLocationUpdate.liveUpdates()` (async sequence) and `CLServiceSession(authorization: .whenInUse)`
  remove most delegate plumbing. Keep a reference to the session while you need it.
- Calling `CLLocationManager.locationServicesEnabled()` on the main thread logs "This method can cause UI
  unresponsiveness". Rely on the authorization callback instead.

## 2. SwiftUI Map (iOS 17+)

```swift
struct PlacesMap: View {
    @State private var position: MapCameraPosition = .userLocation(fallback: .automatic)
    @State private var visibleRegion: MKCoordinateRegion?
    let places: [Place]

    var body: some View {
        Map(position: $position) {
            UserAnnotation()
            ForEach(places) { place in
                Marker(place.name, coordinate: place.coordinate)
            }
        }
        .mapControls { MapUserLocationButton(); MapCompass() }
        .onMapCameraChange(frequency: .onEnd) { context in visibleRegion = context.region }
    }
}
```

- `Map(coordinateRegion:)` (iOS 14–16 API) is deprecated (MK009). Binding a region that you also write
  from location updates gives "Modifying state during view update" warnings and fights user gestures.
- Set `position` to move the camera (`.region(…)`, `.camera(…)`, `.item(…)`, `.automatic` to fit content).
  Reading user changes goes through `onMapCameraChange`, not the binding.
- `ForEach` content needs stable ids (see the swiftui-state-layout skill, SUI006), or markers flicker.
- Large datasets (thousands of markers) are slow in SwiftUI `Map`. Cluster them with MKMapView, or filter
  to the visible region.

## 3. MKMapView in UIViewRepresentable

```swift
func makeUIView(context: Context) -> MKMapView {
    let map = MKMapView()
    map.delegate = context.coordinator
    map.register(MKMarkerAnnotationView.self, forAnnotationViewWithReuseIdentifier: "pin")
    return map
}

func updateUIView(_ map: MKMapView, context: Context) {
    // compare before setting (MK008); isApproximatelyEqual is your own small center/span comparison helper
    if !context.coordinator.lastRegion.isApproximatelyEqual(to: region) {
        map.setRegion(region, animated: true)
        context.coordinator.lastRegion = region
    }
    let existing = Set(map.annotations.compactMap { ($0 as? PlaceAnnotation)?.id })
    let wanted = Set(places.map(\.id))
    map.removeAnnotations(map.annotations.filter { ($0 as? PlaceAnnotation).map { !wanted.contains($0.id) } ?? false })
    map.addAnnotations(places.filter { !existing.contains($0.id) }.map(PlaceAnnotation.init))
}
```

- `regionDidChangeAnimated` → writes `@State region` → `updateUIView` → `setRegion` → … is a loop. Track the
  last applied value in the Coordinator and skip updates that originate from the map itself.
- In `mapView(_:viewFor:)`, return `nil` for `MKUserLocation` first (MK006). Otherwise the blue dot becomes a pin.
- `dequeueReusableAnnotationView(withIdentifier:for:)` requires `register` (MK007). The variant without
  `for:` returns an optional.
- Selection, callouts and overlays need the delegate. Forgetting `map.delegate = context.coordinator` is the
  #1 reason "nothing happens".

## 4. Regions, spans and coordinates

| Value | Valid range | Typical bug |
|---|---|---|
| latitude | −90…90 | Swapped with longitude (GeoJSON and many APIs use `[lng, lat]`) |
| longitude | −180…180 | |
| `latitudeDelta` | > 0, ≤ 180 | 0 or NaN from an empty list → "Invalid Region" exception |
| `longitudeDelta` | > 0, ≤ 360 | |
| `CLLocationCoordinate2DIsValid(c)` | | Use before building regions from server data |
| `(0, 0)` | valid but suspicious | Uninitialised data ("Null Island" in the Gulf of Guinea) |

Fitting annotations: compute the bounding box and pad it by 20–30%. Use a minimum span (e.g. 0.01°) for a
single point. With SwiftUI `Map`, `.automatic` camera position does this for you.

## 5. Annotations and clustering

- Set `clusteringIdentifier` on annotation views to cluster with MKMapView.
- `MKMarkerAnnotationView` gives the system look (glyph, tint). `MKAnnotationView` + `image` gives a custom look.
- Annotations must expose `@objc dynamic var coordinate` to animate position changes.
- Don't add thousands of annotations at once. Filter by `mapView.visibleMapRect`.

## 6. Simulator and testing

- Set a location: Features > Location, a GPX file in the scheme (Run > Options > Default Location), or
  `xcrun simctl location booted set <lat>,<lon>` (user action).
- Reset permission state: `xcrun simctl privacy booted reset location <bundle-id>` (user action, ask first).
- `MKLocalSearch` and directions need network access; failures there are not permission problems.
- Use the iOS 17+ `#Preview` macro with a fixed `MapCameraPosition` for quick visual checks.

## 7. Rule index

| Rule | Severity | Meaning |
|---|---|---|
| MK001 | error | `CLLocationManager()` in a local variable |
| MK002 | error | Authorization requested without the usage key |
| MK003 | warning | User location shown/updated, authorization never requested |
| MK004 | error | Background location without `UIBackgroundModes: location` |
| MK005 | error | Invalid coordinate or span literal (swapped lat/lon) |
| MK006 | warning | `viewFor` doesn't special-case `MKUserLocation` |
| MK007 | warning | Dequeue with `for:` without `register` |
| MK008 | warning | `updateUIView` sets region / adds annotations unconditionally |
| MK009 | info | `Map(coordinateRegion:)` deprecated |
| MK010 | warning | Delegate methods without `.delegate =` |
| MK011 | info | `startUpdatingLocation()` never stopped |
