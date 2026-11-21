## What & why
<!-- Which real debugging problem does this solve? -->

## Checklist
- [ ] Read-only: no new side effects (new commands added to `ALLOWED_COMMANDS` with justification)
- [ ] No secrets or real credentials in code, fixtures or docs
- [ ] `python3 tools/sync_common.py --check` passes (edited `shared/_common.py`, not the copies)
- [ ] `python3 tools/validate_skills.py` passes (new rule ids documented)
- [ ] `python3 -m unittest discover -s tests -v` passes; new rules have positive and negative tests
- [ ] SKILL.md still under 500 lines
