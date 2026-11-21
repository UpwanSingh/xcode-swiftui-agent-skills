# Evaluations

Two layers of evaluation:

| Layer | What it checks | How |
|---|---|---|
| **Deterministic** | Scripts find exactly the planted bugs, MCP tools respect the path policy, skills follow the format | `python3 -m unittest discover -s tests -v` and `python3 tools/validate_skills.py` (run in CI) |
| **Agent behaviour** | The right skill triggers, the agent follows the workflow, and it respects guardrails | `scenarios.json`, run manually or with your own harness |

## Running agent scenarios

1. Install the skills for your agent (`./install.sh --agent <agent>`) and, optionally, the MCP server.
2. Open `tests/fixtures/BuggyApp` as the project (it's a deliberately broken app; see `// RULEID` comments).
3. For each scenario, start a fresh session, paste the `prompt` and grade the transcript:
   - **Trigger**: did the agent load `should_trigger` (and nothing for `null`)?
   - **Expect**: each item satisfied → 1 point.
   - **Must not**: any violation fails the scenario.
4. Record the results per agent/model. Track regressions when you edit a `description` (triggering) or a
   workflow (behaviour).

Tip: when a scenario fails to trigger, sharpen the skill's frontmatter `description` with the user's
vocabulary ("flickers", "PERMISSION_DENIED", "No profiles for"). Agents choose skills from that text alone.
