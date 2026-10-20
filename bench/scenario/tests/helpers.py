"""Shared fixtures for the tasklog tests.

Public interface: entry(...), sample_entries(), TempStore (context manager), run_cli(argv, store_path).
Invariants: nothing here touches the real home directory; the sample entries are fixed and never depend on the clock.
"""
import contextlib
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli  # noqa: E402
from models import Entry  # noqa: E402
from storage import Store  # noqa: E402


def entry(start="2024-03-04T09:00", minutes=60, label="work", tags=None, note="", id=0):
    return Entry(start=start, minutes=minutes, label=label, tags=tags or [], note=note, id=id)


def sample_entries():
    return [
        entry("2024-03-04T09:00", 90, "api", ["backend", "api"], id=1),
        entry("2024-03-04T13:00", 45, "review", ["review"], id=2),
        entry("2024-03-05T10:00", 120, "api", ["backend"], id=3),
        entry("2024-03-07T08:30", 30, "inbox", [], id=4),
        entry("2024-03-11T09:00", 60, "ui", ["frontend"], note="login page", id=5),
    ]


class TempStore:
    def __enter__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "store.json")
        return self

    def __exit__(self, *exc):
        self.dir.cleanup()

    def store(self):
        return Store(self.path)

    def fill(self, entries=None):
        s = self.store()
        for e in entries or sample_entries():
            s.add(entry(e.start, e.minutes, e.label, e.tags, e.note))
        return s


def run_cli(argv, store_path):
    """Run cli.main with TASKLOG_STORE pinned; returns (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    old = os.environ.get("TASKLOG_STORE")
    os.environ["TASKLOG_STORE"] = store_path
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(argv)
            except SystemExit as e:
                code = e.code
    finally:
        if old is None:
            os.environ.pop("TASKLOG_STORE", None)
        else:
            os.environ["TASKLOG_STORE"] = old
    return code, out.getvalue(), err.getvalue()
