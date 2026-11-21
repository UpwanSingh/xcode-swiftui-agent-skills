#!/usr/bin/env python3
"""Static checks for MapKit / Core Location bugs: permissions, regions, annotations, map updates.

Examples:
  mapkit_check.py App/
  mapkit_check.py App/ --plist App/Info.plist
  mapkit_check.py App/ --project App.xcodeproj --json     # generated Info.plist (INFOPLIST_KEY_*)
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import List, Optional, Sequence, Set

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as c  # noqa: E402

RULES = {
    "MK001": ("error", "CLLocationManager stored in a local variable (deallocated immediately)"),
    "MK002": ("error", "Location authorization requested without the Info.plist usage key"),
    "MK003": ("warning", "User location shown/updated but authorization is never requested"),
    "MK004": ("error", "allowsBackgroundLocationUpdates without UIBackgroundModes 'location' (crash)"),
    "MK005": ("error", "Coordinate or span literal out of range (Invalid Region crash / swapped lat-lon)"),
    "MK006": ("warning", "mapView(_:viewFor:) does not return nil for MKUserLocation"),
    "MK007": ("warning", "dequeueReusableAnnotationView(withIdentifier:for:) without register(...)"),
    "MK008": ("warning", "updateUIView sets the region / adds annotations unconditionally"),
    "MK009": ("info", "Map(coordinateRegion:) is deprecated (iOS 17); use Map(position:)"),
    "MK010": ("warning", "Location delegate methods implemented but delegate never assigned"),
    "MK011": ("info", "startUpdatingLocation() without stopUpdatingLocation()"),
}
NUM = r"(-?\d+(?:\.\d+)?)"
COORD = re.compile(r"CLLocationCoordinate2D\s*\(\s*latitude:\s*" + NUM + r"\s*,\s*longitude:\s*" + NUM + r"\s*\)")
SPAN = re.compile(r"MKCoordinateSpan\s*\(\s*latitudeDelta:\s*" + NUM + r"\s*,\s*longitudeDelta:\s*" + NUM + r"\s*\)")
LOCAL_MANAGER = re.compile(r"^[ \t]*(?:let|var)\s+(\w+)\s*(?::\s*CLLocationManager\s*)?=\s*CLLocationManager\s*\(\s*\)", re.M)
SHOWS_USER = re.compile(r"\bshowsUserLocation\s*=\s*true|\bUserAnnotation\s*\(|\bMapUserLocationButton\b|"
                        r"\bstartUpdatingLocation\s*\(|\.userLocation\b(?=\s*\()|\bMapCameraPosition\.userLocation")
REQUEST_AUTH = re.compile(r"\brequest(WhenInUse|Always)Authorization\s*\(")
IMPLICIT_AUTH = re.compile(r"\bCLLocationUpdate\b|\bCLServiceSession\b|\bCLMonitor\b|\bLocationButton\b")
VIEW_FOR = re.compile(r"func\s+mapView\s*\(\s*_\s+\w+\s*:\s*MKMapView\s*,\s*viewFor\s+\w+\s*:\s*(?:any\s+)?MKAnnotation\s*\)")
UPDATE_UIVIEW = re.compile(r"func\s+updateUIView\s*\(")


def _keys_from(plist: Optional[str], project: Optional[str]) -> tuple:
    keys: Set[str] = set()
    modes: Set[str] = set()
    known = False
    if plist:
        data = c.load_plist(plist)
        keys |= set(data)
        modes |= set(data.get("UIBackgroundModes", []) or [])
        known = True
    if project:
        settings = c.read_build_settings(project)
        if "YES" in c.setting_values(settings, "GENERATE_INFOPLIST_FILE"):
            for k, vals in settings.items():
                if k.startswith("INFOPLIST_KEY_"):
                    key = k[len("INFOPLIST_KEY_"):].split("[", 1)[0]
                    keys.add(key)
                    if key == "UIBackgroundModes":
                        for v in vals:
                            modes |= set(v.split())
            known = True
        elif not plist:
            # INFOPLIST_KEY_* are ignored without generation; read the target's own Info.plist instead.
            base = c.project_dir(project)
            for value in c.setting_values(settings, "INFOPLIST_FILE"):
                candidate = os.path.join(base, re.sub(r"^\$[({]SRCROOT[)}]/", "", value))
                if "$(" not in candidate and os.path.isfile(candidate):
                    data = c.load_plist(candidate)
                    keys |= set(data)
                    modes |= set(data.get("UIBackgroundModes", []) or [])
                    known = True
    return keys, modes, known


TYPE_DECL = re.compile(r"\b(class|struct|actor|extension|enum)\b")


def _is_property_initializer(sf: c.SourceFile, enc: int, header: str, name: str) -> bool:
    """True when the manager lives in a closure that initialises a stored/lazy property, or the
    closure hands the manager back to its caller, so it is retained after the closure returns."""
    if enc < 0:
        return False
    close = sf.match_close(enc)
    body = sf.code[enc:close] if close > 0 else ""
    if re.search(r"\breturn\s+" + re.escape(name) + r"\b", body):
        return True
    if re.search(r"=\s*$", header):
        outer = sf.enclosing_open(enc)
        return outer >= 0 and bool(TYPE_DECL.search(sf.header_before(outer)))
    return False


def analyze(paths: Sequence[str], plist: Optional[str] = None, project: Optional[str] = None) -> dict:
    files = c.load_swift_files(paths)
    keys, modes, plist_known = _keys_from(plist, project)
    findings: List[c.Finding] = []
    all_code = "\n".join(sf.code for sf in files)

    def add(rule: str, sf: c.SourceFile, idx: int, msg: str, fix: str, severity: Optional[str] = None) -> None:
        findings.append(c.Finding(rule, severity or RULES[rule][0], msg, sf.path, sf.line_of(idx), fix))

    requests = [(sf, m) for sf in files for m in REQUEST_AUTH.finditer(sf.code)]
    for sf in files:
        code = sf.code
        for m in LOCAL_MANAGER.finditer(code):
            enc = sf.enclosing_open(m.start())
            header = sf.header_before(enc) if enc >= 0 else ""
            if _is_property_initializer(sf, enc, header, m.group(1)):
                continue  # lazy var manager = { let m = CLLocationManager(); ...; return m }()
            if re.search(r"\bfunc\b|\binit\s*\(|\bin$|\bdo$|\bTask\b|\bonAppear\b|\btask\b|=\s*$", header) \
                    and not re.search(r"\b(class|struct|actor|extension|enum)\b", header):
                add("MK001", sf, m.start(), f"`{m.group(1)}` = CLLocationManager() is a local; it is released when "
                    "the scope ends, so the permission prompt vanishes and delegate callbacks never arrive",
                    "Store the manager in a long-lived object (a property of a class/@Observable model).")

        for m in COORD.finditer(code):
            lat, lon = float(m.group(1)), float(m.group(2))
            if abs(lat) > 90 or abs(lon) > 180:
                swapped = abs(lat) > 90 >= abs(lon)
                add("MK005", sf, m.start(), f"coordinate ({lat}, {lon}) is invalid" +
                    (" – latitude and longitude look swapped" if swapped else ""),
                    "Latitude is -90...90 (north/south), longitude -180...180.")
        for m in SPAN.finditer(code):
            dlat, dlon = float(m.group(1)), float(m.group(2))
            if not (0 < dlat <= 180) or not (0 < dlon <= 360):
                add("MK005", sf, m.start(), f"MKCoordinateSpan({dlat}, {dlon}) is outside valid ranges; "
                    "setRegion throws 'Invalid Region'", "Use deltas > 0, latitudeDelta <= 180 and "
                    "longitudeDelta <= 360 (or MKCoordinateRegion(center:latitudinalMeters:longitudinalMeters:)).")

        for m in VIEW_FOR.finditer(code):
            brace = code.find("{", m.end())
            close = sf.match_close(brace) if brace >= 0 else -1
            if close > 0 and "MKUserLocation" not in code[brace:close] and "MKUserLocation" not in code[m.end():brace]:
                add("MK006", sf, m.start(), "mapView(_:viewFor:) returns a custom view for every annotation, "
                    "including the blue user-location dot", "Start with `if annotation is MKUserLocation { return nil }`.")

        if re.search(r"dequeueReusableAnnotationView\s*\(\s*withIdentifier:[^)]*,\s*for:", code) and \
                not re.search(r"\.register\s*\(\s*\w+(?:\.self)?\s*,\s*forAnnotationViewWithReuseIdentifier", all_code):
            m = re.search(r"dequeueReusableAnnotationView\s*\(\s*withIdentifier:[^)]*,\s*for:", code)
            add("MK007", sf, m.start(), "dequeueReusableAnnotationView(withIdentifier:for:) crashes unless the "
                "class was registered", "Call mapView.register(MyView.self, forAnnotationViewWithReuseIdentifier: id) "
                "when creating the map, or use the non-`for:` variant and handle nil.")

        for m in UPDATE_UIVIEW.finditer(code):
            brace = code.find("{", m.end())
            close = sf.match_close(brace) if brace >= 0 else -1
            body = code[brace:close] if close > 0 else ""
            sr = re.search(r"\bsetRegion\s*\(|\.region\s*=", body)
            if sr and not re.search(r"\bif\b|\bguard\b", body[:sr.start()]):
                add("MK008", sf, brace + sr.start(), "updateUIView sets the region on every SwiftUI update; this "
                    "fights user panning and can loop with regionDidChange -> @State -> updateUIView",
                    "Compare with the current region (or track the last applied value in the Coordinator) "
                    "before calling setRegion.")
            aa = re.search(r"\baddAnnotations?\s*\(", body)
            if aa and not re.search(r"\bremoveAnnotations?\s*\(", body):
                add("MK008", sf, brace + aa.start(), "updateUIView adds annotations without removing the old ones; "
                    "pins duplicate on each update", "Diff the annotations (remove stale, add new) or remove "
                    "existing ones first.")

        for m in re.finditer(r"\bMap\s*\(\s*coordinateRegion:", code):
            add("MK009", sf, m.start(), "Map(coordinateRegion:) is deprecated; binding a region that you also "
                "update on location changes causes 'Modifying state during view update' warnings and jitter",
                "Use `Map(position: $position)` with `MapCameraPosition` and read changes via "
                "`.onMapCameraChange`.")

        if re.search(r"func\s+locationManager\s*\(\s*_\s+\w+\s*:\s*CLLocationManager", code) and \
                not re.search(r"\.delegate\s*=", all_code):
            m = re.search(r"func\s+locationManager\s*\(", code)
            add("MK010", sf, m.start(), "CLLocationManagerDelegate methods exist but no `.delegate =` assignment "
                "was found", "Set `manager.delegate = self` before requesting authorization or updates.")

        for m in re.finditer(r"\bstartUpdatingLocation\s*\(", code):
            if "stopUpdatingLocation" not in all_code:
                add("MK011", sf, m.start(), "location updates are never stopped (battery drain, background blue "
                    "bar)", "Call stopUpdatingLocation() when the map disappears, or use CLLocationUpdate.liveUpdates() "
                    "in a cancellable task.")

    for sf, m in requests:
        key = "NSLocationAlwaysAndWhenInUseUsageDescription" if m.group(1) == "Always" else "NSLocationWhenInUseUsageDescription"
        if plist_known and key not in keys:
            also = " plus NSLocationWhenInUseUsageDescription" if m.group(1) == "Always" else ""
            add("MK002", sf, m.start(), f"request{m.group(1)}Authorization() without {key}: the prompt never "
                "appears and status stays .notDetermined", f"Add {key}{also} to Info.plist (or INFOPLIST_KEY_{key} "
                "with a generated Info.plist).")
    if not requests and not IMPLICIT_AUTH.search(all_code):
        for sf in files:
            m = SHOWS_USER.search(sf.code)
            if m:
                add("MK003", sf, m.start(), "user location is displayed/updated but no authorization request was "
                    "found; the dot never appears until the user grants access",
                    "Call requestWhenInUseAuthorization() on a retained CLLocationManager (or use "
                    "CLServiceSession on iOS 18+).")
                break
    for sf in files:
        for m in re.finditer(r"\ballowsBackgroundLocationUpdates\s*=\s*true", sf.code):
            if plist_known and "location" not in modes:
                add("MK004", sf, m.start(), "allowsBackgroundLocationUpdates = true without UIBackgroundModes "
                    "'location' raises NSInternalInconsistencyException", "Enable Background Modes > Location "
                    "updates for the target (adds 'location' to UIBackgroundModes).")
            elif not plist_known:
                add("MK004", sf, m.start(), "allowsBackgroundLocationUpdates = true: verify UIBackgroundModes "
                    "contains 'location' (pass --plist/--project to check)", "Background Modes > Location updates.",
                    severity="info")
    return c.make_result(findings, files_scanned=len(files), plist_checked=plist_known)


@c.cli
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="*", help="Swift files or directories")
    p.add_argument("--plist", help="app Info.plist (enables permission/background-mode checks)")
    p.add_argument("--project", help=".xcodeproj for INFOPLIST_KEY_* settings (generated Info.plist)")
    p.add_argument("--list-rules", action="store_true", help="print rule ids and exit")
    c.add_output_args(p)
    args = p.parse_args(argv)
    if args.list_rules:
        c.print_rules(RULES)
        return 0
    if not args.paths:
        p.error("at least one path is required")
    result = analyze(args.paths, args.plist, args.project)
    if args.json:
        c.print_json(result)
    else:
        print(c.format_findings(result))
        print(f"scanned {result['files_scanned']} Swift file(s)"
              + ("" if result["plist_checked"] else "; pass --plist/--project for permission checks"))
    return c.exit_status(result, args.fail_on)


if __name__ == "__main__":
    sys.exit(main())
