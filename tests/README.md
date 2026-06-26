# tests/ — health dashboard + functional scenarios

- **`health.py`** — read-only **health dashboard**: is every component alive right now? Flags:
  `--watch [s]`, `--only <substr>`, `--runtime aap|awx`.
- **`scenarios/`** — real **functional tests**, numbered in run order; each prints PASS/FAIL.

```bash
python3 tests/health.py
python3 tests/scenarios/1_pull_incident_remediation.py
```

📖 **What each test proves and when to run it → [docs/10-tests.md](../docs/10-tests.md)** (Validate).
