# Contributing

Thanks for helping make iOS debugging less painful. Issues and pull requests are welcome.

## Ground rules

1. **Read-only by default.** Scripts and MCP tools must never build, sign, install, erase, upload, deploy,
   or modify project files, keychains, simulators or Firebase projects. New external commands must be
   added to `ALLOWED_COMMANDS` in `shared/_common.py`, with a read-only justification in the PR.
2. **No secrets, ever.** No real keys, certificates, profiles, tokens or GoogleService-Info files in code,
   fixtures or docs. Build fake values at runtime in tests (see `tests/helpers.py`). Output that could
   contain secrets must go through `redact_text` / `redact_obj`.
3. **Stdlib only, Python 3.9+.** Users should be able to run everything on a stock Mac with no installation step.
4. **Skills stay self-contained.** A skill folder must work when copied on its own. Shared code lives in
   `shared/_common.py`, and each skill gets an identical copy via `python3 tools/sync_common.py`.
   Don't edit the copies directly.
5. **Every rule is documented and tested.** A new rule id (e.g. `SUI014`) needs a row in the skill's
   reference docs (`tools/validate_skills.py` enforces this), a positive case in the fixtures and a test.
   Add a negative case if the rule could plausibly misfire.
6. **SKILL.md stays under 500 lines.** Put depth in `references/` (one level deep) and keep SKILL.md to
   workflows, decision tables and worked examples.

## Development loop

```bash
python3 tools/sync_common.py                 # after editing shared/_common.py
python3 tools/validate_skills.py             # Agent Skills format + docs coverage
python3 -m unittest discover -s tests -v     # unit + MCP end-to-end tests
python3 mcp-server/server.py --list-tools    # MCP smoke test
```

Try a script against the fixture app:

```bash
python3 skills/swiftui-state-layout/scripts/swiftui_lint.py tests/fixtures/BuggyApp/App
```

## Adding a rule (checklist)

- [ ] Add it to `RULES` (or `CATALOG` / `RUNTIME_WARNINGS`) with a severity
- [ ] Message says *what is wrong and its runtime effect*; `fix` says *what to change*
- [ ] Runs on `SourceFile.code` (comments/strings stripped) unless it needs literal contents
- [ ] Fixture line in `tests/fixtures/BuggyApp` marked with a `// RULEID` comment
- [ ] Test asserting it fires, plus a negative test for near-misses
- [ ] Row in the skill's reference rule index

## Adding a skill

1. `skills/<name>/SKILL.md` with frontmatter: `name` (matches folder), `description` (what it does
   **and when to use it**, ≤ 1024 chars), `license`, `compatibility`, `metadata`.
2. `scripts/`, `references/`, `assets/` as needed; copy `_common.py` with `tools/sync_common.py`.
3. Expose read-only functionality to the MCP server by adding a `Tool` in `mcp-server/ios_debug_mcp/tools.py`.
4. Add scenarios to `evals/scenarios.json`.

## Pull requests

- Keep PRs focused, and describe the real-world bug the change catches (link Apple/Firebase docs when relevant).
- CI must pass on Linux and macOS.
- Be kind in reviews. This project follows the [Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/).
