# Crash triage: <App> <version> (<build>)

**Report:** `<file name>` · **OS / device:** <iOS x.y, model> · **Occurrences:** <n> since <date>

## Signal
- Exception: `<EXC_… (SIG…)>`
- Termination: `<namespace code / reason>`
- Application Specific Information: `<asi message>`

## Crashed thread (symbolicated)
```
#0 …
#1 <App>  <Type.method()>  (<File.swift:line>)   ← first app frame
```

## Diagnosis
<one or two sentences: the mechanism and the root cause>

## Reproduction
1. <steps>
2. Diagnostic used: <Address Sanitizer / Thread Sanitizer / exception breakpoint / none>

## Fix
- <code change, with file:line>
- <guard / test added so it cannot regress>

## Verification
- [ ] Reproduction steps no longer crash
- [ ] Relevant script is clean (e.g. `check_plist.py`, `mapkit_check.py`, `concurrency_check.py`)
- [ ] No new runtime warnings (`sim_logs.py scan`)

<!-- Do not paste user data, tokens or full device identifiers into this report. -->
