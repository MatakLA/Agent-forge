## Project rules (copy to etc/team-rules.md and adapt - the orchestrator enforces these on top of prompts/orchestrator.md)
- Runtime: Python 3.12, standard library only, pytest for tests, SQLite via sqlite3.
- Every new public function gets a docstring and at least one test.
- Money is always Decimal, never float. Time zones are always explicit.
- No network access from the code or the tests.
