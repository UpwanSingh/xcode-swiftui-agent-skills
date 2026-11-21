---
name: ios-crash-log-triage
description: Triage iOS app crashes, hangs and runtime warnings - summarise .ips/.crash reports (EXC_BAD_ACCESS, EXC_BREAKPOINT Swift traps, SIGABRT uncaught NSException, 0x8badf00d watchdog, 0xdead10cc, actor-isolation runtime crashes, TCC privacy kills), symbolicate app frames with atos and dSYM UUIDs, list simulators and devices, query simulator unified logs safely, and classify console output for known SwiftUI, concurrency, Auto Layout, MapKit, privacy and Firebase runtime warnings. Use when an app crashes, freezes, is killed, or prints warnings in the Xcode console.
license: MIT
compatibility: Scripts need Python 3.9+ (stdlib only). Report and log-file analysis runs on any OS; simulator and device queries need macOS with Xcode 15+ (xcrun simctl/devicectl).
metadata:
  version: "1.0.0"
  author: UpwanSingh
---

# Crash report and runtime log triage

Crash first, code second. Get the **exception type, the termination reason, the crashed thread's top
app frame and the "Application Specific Information"** before you form a hypothesis. They usually
name the bug outright.

## Scripts (all read-only)

```bash
python3 scripts/crash_report.py list --app MyApp                 # newest reports (Mac + synced device logs)
python3 scripts/crash_report.py summary path/to/report.ips       # exception, frames, diagnosis, atos command
python3 scripts/sim_logs.py devices --booted                     # simulators
python3 scripts/sim_logs.py physical                             # devices via devicectl
python3 scripts/sim_logs.py logs --process MyApp --last 10m --errors-only
python3 scripts/sim_logs.py scan console.log                     # or: pbpaste | sim_logs.py scan -
```

`logs` builds its log predicate from validated flags only (no raw predicates, no shell). Output is
capped and secrets are masked.

## Workflow 1: the app crashed

1. **Find the report.**
   - Simulator crashes are Mac processes, so they land in `~/Library/Logs/DiagnosticReports/`
     (`crash_report.py list`).
   - Device: Xcode > Window > Devices and Simulators (select the device, then its device logs), Settings > Privacy & Security >
     Analytics & Improvements > Analytics Data on the device, or Xcode Organizer > Crashes for
     TestFlight/App Store builds. Synced device logs also appear under `~/Library/Logs/CrashReporter/MobileDevice/`.
2. **Summarise it.** Run `crash_report.py summary <file>`.
3. **Read it in this order:**
   1. `Exception` + `Termination` (table below).
   2. `App info` (asi): Swift runtime messages such as `Fatal error: Unexpectedly found nil…` include file:line.
   3. Crashed-thread frames: the **first frame in your app's image** is where to look. Frames above it in
      system libraries are the mechanism, not the cause.
   4. `Last exception backtrace` (Objective-C exceptions): this is where the exception was *thrown*.
4. **Symbolicate when app frames show only addresses.** The summary prints the dSYM UUID, an `mdfind`
   command to locate the dSYM, and an `atos` command with load address and frame addresses. Archives
   keep dSYMs in `~/Library/Developer/Xcode/Archives/…/dSYMs`. App Store builds: download dSYMs in Organizer.
5. **Reproduce with a diagnostic** that matches the class of crash (table below), then fix the top app frame.

| Exception / termination | Meaning | Reproduce with |
|---|---|---|
| `EXC_BREAKPOINT (SIGTRAP)` in libswiftCore | Swift trap: force unwrap of nil, index out of range, `try!`, `as!`, `fatalError`, overflow | Run in debugger; the asi message gives the line |
| `EXC_BAD_ACCESS (SIGSEGV/SIGBUS)` `KERN_INVALID_ADDRESS` | Bad pointer: use-after-free, `unowned` on a released object, data race, unsafe pointer | Address Sanitizer; Zombie Objects for ObjC; Thread Sanitizer for races |
| `EXC_CRASH (SIGABRT)` + `lastExceptionBacktrace` | Uncaught NSException (`NSInvalidArgumentException`, `NSRangeException`, `NSInternalInconsistencyException`) | Exception breakpoint (Breakpoint navigator > + > Exception) |
| `FRONTBOARD 0x8badf00d` | Watchdog: main thread blocked during launch/resume/scene update | Instruments Time Profiler / Hangs; move I/O off main |
| `0xdead10cc` | Suspended while holding a file lock (SQLite in an App Group container) | Finish DB work before backgrounding |
| `EXC_RESOURCE` | CPU, memory or wakeup limit | Instruments Allocations / Leaks / Energy |
| `_dispatch_assert_queue_fail`, `Incorrect actor executor assumption` | Swift 6 runtime isolation check: `@MainActor` code ran off-main | See swiftui-state-layout → swift6-concurrency.md §3 |
| `TCC` / "attempted to access privacy-sensitive data" | Missing `NS…UsageDescription` | xcode-build-signing → `check_plist.py --sources` |
| `Invalid Region` (NSInvalidArgumentException) | Bad MKCoordinateRegion | ios-map-firebase-diagnostics → `mapkit_check.py` |
| "default FirebaseApp instance must be configured" | Firebase used before `configure()` | ios-map-firebase-diagnostics → `check_firebase_config.py` |

Full code reference: [references/crash-types.md](references/crash-types.md).

## Workflow 2: the app hangs or is slow

1. Watchdog reports (`0x8badf00d`) and `stackshot/hang` reports in `crash_report.py list` show the main
   thread's stack at the time of the hang. The top frames name the blocking call (`sqlite3_step`,
   `semaphore_wait`, `URLSession` sync wrappers, `JSONDecoder` on large payloads).
2. While the app runs on a simulator, ask for `sim_logs.py logs --process App --contains "Hang"`
   or the Thread Performance Checker messages (RW013).
3. Fix: move the blocking work to a `nonisolated`/`@concurrent` async function or an actor, and keep the
   main actor for UI.

## Workflow 3: runtime warnings in the console

1. Save the Xcode console (right-click > Save, or copy) and run `sim_logs.py scan console.log`. Or pull the
   last minutes from a booted simulator with `sim_logs.py logs --process App --last 5m`.
2. Each match has an id (RW001–RW025), an area and a next step that points at the skill/script to use:
   - SwiftUI publishing/state-update warnings, AttributeGraph cycles, navigation warnings → swiftui-state-layout
   - Privacy, ATS → xcode-build-signing
   - MapKit, Core Location, Firebase permissions/config/index → ios-map-firebase-diagnostics
3. For Swift Concurrency runtime warnings, set the scheme environment variable
   `SWIFT_UNEXPECTED_EXECUTOR_LOG_LEVEL` only if the user asks. The defaults are fine for diagnosis.

## Guardrails

- Read-only: never delete reports, erase simulators, uninstall apps or reset privacy without asking.
  Commands such as `xcrun simctl erase` and `simctl privacy reset` are user actions.
- Crash reports can contain user data (paths, account names, device identifiers). Quote only the
  frames and fields needed, and never upload reports to third-party services.
- Unsymbolicated frames are not "unknown". Find the dSYM before guessing.

## Worked examples

**Crash on launch on TestFlight only.**
`crash_report.py summary` shows `EXC_BREAKPOINT SIGTRAP`, and asi says `Fatal error: Unexpectedly found nil
while unwrapping an Optional value` at `ConfigLoader.swift:18`. The frames are unsymbolicated, so the
summary prints `mdfind "com_apple_xcode_dsym_uuids == …"` and an `atos` line that resolves to
`ConfigLoader.load()`. Release builds didn't bundle `config.json` (checked with
`check_bundle_resources.py --app`, BR002). Fix: add the file to the target, and replace `!` with a
diagnosable `guard … else { assertionFailure(); return default }`.

**Killed after 10 s at launch.**
`FRONTBOARD`, code `0x8badf00d`, explanation `scene-update watchdog transgression`. The crashed main-thread
stack tops out at `sqlite3_step`. The diagnosis is a watchdog kill. Fix: open and migrate the database
off the main actor, and show a placeholder UI first.

**Console floods with "Publishing changes from background threads is not allowed".**
`sim_logs.py scan` reports RW001 ×240. `concurrency_check.py` (swiftui-state-layout) reports SC006 for
`final class FeedStore: ObservableObject`. Annotate the class `@MainActor`, and the network call
stays async off-main.

## References

- [references/crash-types.md](references/crash-types.md): exception types, termination codes, .ips structure, symbolication, sanitizers
- [assets/bug-report-template.md](assets/bug-report-template.md): template for writing up a triaged crash
