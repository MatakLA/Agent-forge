# tasklog

Time-tracking CLI. Flat layout: modules in the repo root, tests in `tests/` (one file per module, shared fixtures in `tests/helpers.py`).
Python 3 standard library only. Run tests: `python3 -m pytest -q` (or `python3 -m unittest discover -s tests -q`).
Every module starts with a short docstring (purpose, public interface, invariants); keep it true when you change the module.
CLI entry point: `python3 cli.py <command>`; the store file comes from `TASKLOG_STORE`.
