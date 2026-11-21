# Crash types, codes and symbolication: reference

## Contents
1. .ips structure
2. Exception types and signals
3. Termination codes
4. Swift runtime traps
5. Symbolication
6. Diagnostics to enable (scheme)
7. Runtime warning ids (sim_logs.py)

## 1. .ips structure (iOS 15+ / macOS 12+)

An `.ips` file is **two JSON documents**: line 1 is a header, the rest is the payload.

| Field | Where | Meaning |
|---|---|---|
| `bug_type` | header | `309` crash, `288` stackshot/hang, `298` jetsam (memory), others are diagnostics |
| `app_name`, `bundleID`, `app_version`, `os_version` | header | Identification |
| `exception.type/signal/codes/subtype` | payload | Mach exception and signal |
| `termination.namespace/code/indicator/reasons` | payload | Who killed the process and why |
| `asi` | payload | "Application Specific Information": Swift/ObjC messages |
| `faultingThread`, `threads[].frames[]` | payload | Backtraces; frames reference `usedImages[imageIndex]` + `imageOffset` |
| `lastExceptionBacktrace` | payload | Throw site of an uncaught Objective-C exception |
| `usedImages[]` | payload | `name`, `uuid` (dSYM UUID), `base` (load address), `arch` |

Address of a frame = `usedImages[imageIndex].base + imageOffset`. `crash_report.py` computes it.

Legacy `.crash` text reports have the same information under `Exception Type:`, `Termination Reason:`,
`Application Specific Information:` and `Thread N Crashed:`.

## 2. Exception types and signals

| Type | Signal | Typical causes |
|---|---|---|
| `EXC_BREAKPOINT` | SIGTRAP | Swift runtime traps (section 4), `__builtin_trap`, dispatch assertions |
| `EXC_BAD_ACCESS` | SIGSEGV / SIGBUS | Dangling pointer, `unowned` to a freed object, data race corrupting memory, stack overflow (`KERN_PROTECTION_FAILURE` near the stack guard), misaligned access |
| `EXC_BAD_INSTRUCTION` | SIGILL | Older Swift traps on x86_64 simulators |
| `EXC_CRASH` | SIGABRT | `abort()`: uncaught NSException, C++ exception, failed `assert`, malloc corruption |
| `EXC_CRASH` | SIGKILL | Killed by the system: watchdog, jetsam, code-signing invalid, user force-quit |
| `EXC_GUARD` | | Guarded resource misuse (closing a file descriptor you don't own) |
| `EXC_RESOURCE` | | CPU, wakeups or memory limit exceeded (often a non-fatal report) |

`KERN_INVALID_ADDRESS at 0x0000000000000000` (or a small offset like `0x10`) is a null-pointer dereference.
Large random addresses point to use-after-free or corruption.

## 3. Termination codes

| Code | Name | Meaning |
|---|---|---|
| `0x8badf00d` | "ate bad food" | Watchdog: main thread unresponsive during launch, resume, suspend or scene update |
| `0xdead10cc` | "deadlock" | Suspended while holding a file lock or SQLite DB in a shared container |
| `0xc00010ff` | "cool off" | Thermal |
| `0xbaadca11` | "bad call" | PushKit VoIP push without reporting a call to CallKit |
| `0xbad22222` | | VoIP app resumed too often |
| `0x2bad45ec` | "too bad for security" | Security violation |
| `CODESIGNING` namespace | | Invalid signature or profile on launch (see xcode-build-signing) |
| `TCC` namespace | | Privacy-protected resource accessed without a usage description |

## 4. Swift runtime traps (EXC_BREAKPOINT)

| asi message | Fix pattern |
|---|---|
| `Unexpectedly found nil while unwrapping an Optional value` | `guard let`, `??` default, or surface an error |
| `Unexpectedly found nil while implicitly unwrapping an Optional value` | An IBOutlet not connected, or a `!`-declared property read before it was set |
| `Index out of range` | Check `indices.contains(i)`. Async updates can shrink the array between check and use; isolate the state to one actor |
| `Fatal error: Duplicate keys of type … were found in a Dictionary` | `Dictionary(uniqueKeysWithValues:)` with duplicates; use `Dictionary(_:uniquingKeysWith:)` |
| `'try!' expression unexpectedly raised an error` | `do/catch` |
| `Could not cast value of type X to Y` | `as?` |
| `Arithmetic overflow` / `Division by zero` | Validate inputs; use `&+` only when wrapping is intended |
| `Incorrect actor executor assumption` | Runtime isolation check (swiftui-state-layout swift6-concurrency.md §3) |

## 5. Symbolication

1. Find the app image's UUID in `usedImages` (the summary prints it).
2. Locate the dSYM: `mdfind "com_apple_xcode_dsym_uuids == <UUID>"`, or look in
   `~/Library/Developer/Xcode/Archives/<date>/<App>.xcarchive/dSYMs/`, or download it in Organizer
   (App Store builds).
3. Verify: `dwarfdump --uuid App.app.dSYM` must list the same UUID for the crash's architecture.
4. Resolve: `atos -arch arm64 -o App.app.dSYM/Contents/Resources/DWARF/App -l <load address> <frame addresses…>`.
5. Xcode symbolicates reports it imports (Devices and Simulators device logs, Organizer) automatically
   when the matching dSYM is indexed by Spotlight.

If the UUID matches no dSYM, the build was not archived on this machine, or bitcode/stripping settings
changed. Rebuilding does **not** produce the same UUID.

## 6. Diagnostics to enable (scheme > Run > Diagnostics)

| Diagnostic | Finds | Notes |
|---|---|---|
| Address Sanitizer | Use-after-free, buffer overflow | ~2–3× slower; not with TSan |
| Thread Sanitizer | Data races | Simulator only for iOS |
| Undefined Behavior Sanitizer | C/ObjC UB | |
| Main Thread Checker | UIKit/AppKit calls off-main | On by default in Debug |
| Zombie Objects | Messages to deallocated ObjC objects | Memory grows; debug only |
| Malloc Scribble / Guard Edges | Heap corruption | |

## 7. Runtime warning ids (sim_logs.py scan)

| Id | Area | Pattern (abridged) |
|---|---|---|
| RW001 | swiftui | Publishing changes from background threads |
| RW002 | swiftui | Publishing changes from within view updates |
| RW003 | swiftui | Modifying state during view update |
| RW004 | swiftui | AttributeGraph: cycle detected |
| RW005 | swiftui | onChange(of:) action tried to update multiple times per frame |
| RW006 | swiftui | Bound preference tried to update multiple times per frame |
| RW007 | navigation | NavigationLink value with no matching navigationDestination |
| RW008 | navigation | navigation destination inside a lazy container |
| RW009 | navigation | Only root-level navigation destinations are effective |
| RW010 | layout | Unable to simultaneously satisfy constraints |
| RW011 | layout | Invalid frame dimension (negative or non-finite) |
| RW012 | concurrency | Incorrect actor executor assumption |
| RW013 | performance | Thread Performance Checker / Main Thread Checker |
| RW014 | privacy | privacy-sensitive data / missing UsageDescription |
| RW015 | network | App Transport Security blocked cleartext HTTP |
| RW016 | mapkit | Invalid Region / MKMapView off-main |
| RW017 | location | kCLErrorDomain |
| RW018 | location | locationServicesEnabled on main thread |
| RW019 | firebase | default FirebaseApp not configured |
| RW020 | firebase | Missing or insufficient permissions / PERMISSION_DENIED |
| RW021 | firebase | Firestore settings changed after start |
| RW022 | firebase | Auth: invalid API key / app not authorized |
| RW023 | firebase | APNS token not set before FCM token |
| RW024 | storage | Object / bucket does not exist |
| RW025 | firebase | The query requires an index |
