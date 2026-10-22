#!/usr/bin/env python3
"""Bytes a worker can write must never stop the host's own bookkeeping.

A worker (or the repository it works on) controls much of what the host reads back: the egress proxy's access log, stream.jsonl,
stderr.log, git output, process lists, test output. Decoded strictly, one bad byte in any of it raises, usually where nobody catches
it - one odd request or one `printf '\\351'` then stopped dispatching, the status page, the dashboard or the end of a run. Each test
feeds such bytes to the real code (no Docker, no model) and checks that it neither crashes nor loses the rest of the data.
Run: python3 tests/test_hostile_bytes.py"""
import ast, json, os, pathlib, re, shutil, subprocess, sys, tempfile, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix="af-test-bytes-")
os.environ["SANDBOX_TEST"] = "1"
os.environ["SANDBOX_RUNS_DIR"] = os.path.join(T, "runs")
os.environ["LIMITS_FILE"] = os.path.join(ROOT, "etc", "limits.example.conf")
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))
import runtimes, sbx

LIMITS = ("RUN_TIMEOUT_MIN=30\nRUN_TOKEN_CAP=1500000\nMAX_PARALLEL=4\nPOLL_SEC=1\nDAILY_TOKEN_BUDGET=3000000\n"
          "MAX_RUNS_PER_DAY=10\nCONSEC_FAIL_TRIP=3\nWORKER_MODEL=test-model\n")


def put(path, data):
    with open(path, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))


def get(path):
    with open(path, "rb") as f:
        return f.read()


def step(tokens, **kw):
    """One finished step of an agent's event stream, as UTF-8 bytes."""
    return json.dumps({"event": "step_update", "step_update": dict(state="DONE", usage={"total_tokens": tokens}, **kw)}, ensure_ascii=False).encode()


# what a worker's stream looks like when it (or a process it started) wrote something that is not UTF-8 into it
STREAM = (step(100, step_type="tool", tool_name="run_command") + b"\n"
          + step(20, step_type="agent_response", text_delta="café ok") + b"\n"
          + b'{"event": "log", "text": "caf\xe9 \xff\xfe"}\n'
          + b"\xe9 not json at all\n"
          + step(7, step_type="agent_response", text_delta="done") + b"\n")
STREAM_TOKENS = 127


def sandbox(td, docker_script):
    """A throwaway runs dir + limits file + a fake `docker` first on PATH; returns (env, runs dir)."""
    runs, bin_dir, limits = (os.path.join(td, n) for n in ("runs", "bin", "limits.conf"))
    os.makedirs(runs)
    os.makedirs(bin_dir)
    put(limits, LIMITS)
    put(os.path.join(bin_dir, "docker"), docker_script)
    os.chmod(os.path.join(bin_dir, "docker"), 0o755)
    env = dict(os.environ, PATH=bin_dir + os.pathsep + os.environ["PATH"], SANDBOX_TEST="1", SANDBOX_RUNS_DIR=runs, LIMITS_FILE=limits,
               FORGE_URL="http://127.0.0.1:9", PYTHONIOENCODING="utf-8")
    return env, runs


def write_run(runs, name, meta, files):
    run = os.path.join(runs, name)
    os.makedirs(run)
    put(os.path.join(run, "meta.json"), json.dumps(meta))
    for fname, data in files.items():
        put(os.path.join(run, fname), data)
    return run


class WorkerFiles(unittest.TestCase):
    def test_token_count_survives_a_line_that_is_not_utf8(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "stream.jsonl")
            put(p, STREAM)
            self.assertEqual(sbx.stream_tokens(p), STREAM_TOKENS)

    def test_a_file_can_be_read_leniently(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "stderr.log")
            put(p, b"first\nwarn: caf\xe9 failed\n")
            self.assertEqual(sbx.read(p, errors="replace"), "first\nwarn: caf� failed")

    def test_status_page_works_without_the_optional_guard_log_and_with_bad_bytes(self):
        fake = ('#!/bin/sh\ncase "$*" in\n  "ps -a --format {{.Names}}") echo worker-1 ;;\n'
                '  "inspect -f {{.State.Status}} worker-1") echo exited ;;\n  "inspect -f {{.RestartCount}} worker-1") echo 0 ;;\nesac\nexit 0\n')
        self.assertFalse(os.path.exists(os.path.join(ROOT, "bin", "guard-log")), "the open-source tree has no bin/guard-log")
        with tempfile.TemporaryDirectory() as td:
            env, runs = sandbox(td, fake)
            write_run(runs, "20261012T000000Z-worker-1", dict(reason="ok", worker="worker-1"),
                      {"finished": "2026-10-12T00:10:00Z\n", "exitcode": "0\n", "started": "2026-10-12T00:00:00Z\n", "stream.jsonl": STREAM,
                       "stderr.log": b"first\nwarn: caf\xe9 failed\n"})
            r = subprocess.run([sys.executable, os.path.join(ROOT, "bin", "status"), "--no-quota", "--json"], capture_output=True, env=env, timeout=120)
            err = r.stderr.decode("utf-8", "replace")
            self.assertNotIn("Traceback", err)
            w = json.loads(r.stdout.decode("utf-8"))["workers"][0]
            self.assertEqual((w["name"], w["state"], w["tokens"], w["steps"], w["tool_calls"]), ("worker-1", "FINISHED (ok)", STREAM_TOKENS, 3, 1))
            self.assertIn("done", w["last_lines"])
            self.assertIn("stderr: warn: caf� failed", w["last_lines"])
            r = subprocess.run([sys.executable, os.path.join(ROOT, "bin", "status"), "--no-quota"], capture_output=True, env=env, timeout=120)
            self.assertNotIn("Traceback", r.stderr.decode("utf-8", "replace"))
            self.assertIn("worker-1", r.stdout.decode("utf-8", "replace"))


class Supervisor(unittest.TestCase):
    def test_a_run_ends_properly_whatever_bytes_docker_and_the_agent_print(self):
        """Every docker call prints a byte that is not UTF-8 on stdout and stderr; the agent's stderr (the quota message) carries one
        before the words that name the reason. The run must still end with its exit code, its result and the right reason."""
        fake = r"""#!/bin/sh
case "$*" in
  *"{{.State.Running}}"*) echo true; printf 'caf\351\n' >&2 ;;
  *"/usage"*) printf 'Gemini caf\351\t5h\t80%%\t2h\n' ;;
  *agy-run*) printf 'caf\351 quota exceeded\n' >&2 ;;
  *) printf 'caf\351\n'; printf 'caf\351\n' >&2 ;;
esac
exit 0
"""
        with tempfile.TemporaryDirectory() as td:
            env, runs = sandbox(td, fake)
            name = "20261012T000000Z-worker-1"
            run = write_run(runs, name, dict(run_id=name, worker="worker-1", task="task-1.md", test=True, runner=None, runtime="agy", model="test-model",
                                             repo="sandbox/playground", branch="feat/task-1", conversation="", limits=dict(timeout_min=30, token_cap=1500000)),
                            {"prompt.md": "do the task\n"})
            r = subprocess.run([sys.executable, os.path.join(ROOT, "bin", "run-supervisor"), run], capture_output=True, env=env, timeout=120)
            err = r.stderr.decode("utf-8", "replace")
            self.assertEqual(r.returncode, 0, err[-1500:])
            self.assertNotIn("Traceback", err)
            self.assertTrue(os.path.exists(os.path.join(run, "finished")), "the run never ended")
            self.assertEqual(get(os.path.join(run, "exitcode")).strip(), b"75")
            meta = json.loads(get(os.path.join(run, "meta.json")))
            self.assertEqual(meta["reason"], "quota")
            self.assertIn("caf�", meta["salvage"]["out"])
            self.assertEqual(meta["quota_after"][0]["pct"], 80.0)


class MutationCheck(unittest.TestCase):
    """The script the gate runs in a container for `--mutations` (embedded in bin/review-branch), run here in a temp dir."""

    @classmethod
    def script(cls, td):
        src = pathlib.Path(ROOT, "bin", "review-branch").read_text()
        code = re.search(r"open\(runner, \"w\"\)\.write\('''(.*?)'''\)", src, re.S).group(1)
        for literal, real in (('"/spec.json"', os.path.join(td, "spec.json")), ('"/src"', os.path.join(td, "src")), ('"/tmp/m"', os.path.join(td, "m"))):
            assert literal in code, literal
            code = code.replace(literal, repr(real))
        assert not re.search(r'"/(spec\.json|src|tmp/m)"', code)
        path = os.path.join(td, "mutate.py")
        put(path, code)
        return path

    def test_one_mutant_that_cannot_be_set_up_does_not_hide_the_others(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "src")
            os.makedirs(src)
            put(os.path.join(src, "calc.py"), "def add(a, b):\n    return a + b\n")
            put(os.path.join(src, "legacy.py"), b'# -*- coding: latin-1 -*-\nNAME = "caf\xe9"\nVALUE = 1\n')
            put(os.path.join(src, "check.py"),
                "import sys, calc, legacy\n"
                "if legacy.VALUE == 3:\n    sys.stdout.buffer.write(b'caf\\xe9 broke\\n'); sys.exit(1)\n"
                "print('add=%d name=%s value=%d' % (calc.add(2, 3), legacy.NAME, legacy.VALUE))\n"
                "sys.exit(0 if calc.add(2, 3) == 5 and legacy.VALUE == 1 else 1)\n")
            os.symlink("nowhere", os.path.join(src, "dangling"))
            spec = [dict(name="plus", file="calc.py", old="a + b", new="a - b"),
                    dict(name="latin1", file="legacy.py", old="VALUE = 1", new="VALUE = 2"),
                    dict(name="gone", file="missing.py", old="x", new="y"),
                    dict(name="oops"),
                    dict(name="same", file="calc.py", old="a + b", new="b + a"),
                    dict(name="typo", file="calc.py", old="no such text", new="y"),
                    dict(name="noisy", file="legacy.py", old="VALUE = 1", new="VALUE = 3")]
            put(os.path.join(td, "spec.json"), json.dumps(spec))
            r = subprocess.run([sys.executable, self.script(td)], capture_output=True, env=dict(os.environ, TEST_CMD="python3 check.py"), timeout=120)
            out = r.stdout.decode("utf-8")
            self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace")[-1500:])
            got = [json.loads(l) for l in out.splitlines()]
            self.assertEqual([(d["name"], d["result"]) for d in got],
                             [("plus", "KILLED"), ("latin1", "KILLED"), ("gone", "ERROR"), ("oops", "ERROR"), ("same", "SURVIVED"),
                              ("typo", "BAD-SPEC"), ("noisy", "KILLED")], out)
            self.assertEqual(got[1]["tail"], "add=5 name=café value=2")        # the latin-1 file went through the mutation byte for byte
            self.assertIn("FileNotFoundError", got[2]["note"])
            self.assertEqual(got[6]["tail"], "caf� broke")                       # the test output that is not UTF-8 is read, not fatal
            self.assertEqual(get(os.path.join(src, "legacy.py")).count(b"\xe9"), 1, "the source tree is read-only for the check")


# lines of an agent's event stream that are valid JSON, or close enough to reach a parser, but not shaped like the events it expects
ODD_LINES = [
    "5", "null", "[1, 2]", '"text"', "true", "{}",
    '{"event": "step_update"}',
    '{"event": "step_update", "step_update": null}',
    '{"event": "step_update", "step_update": "x"}',
    '{"event": "step_update", "step_update": {"state": "DONE", "usage": null}}',
    '{"event": "step_update", "step_update": {"state": "DONE", "usage": "x"}}',
    '{"event": "step_update", "step_update": {"state": "DONE", "usage": {"total_tokens": "7"}}}',
    '{"event": "step_update", "step_update": {"state": "DONE", "usage": {"total_tokens": [1]}}}',
    '{"event": "step_update", "step_update": {"state": "DONE", "usage": {"total_tokens": 1e999}}}',
    '{"event": "step_update", "step_update": {"state": "DONE", "usage": {"total_tokens": NaN}}}',
    '{"event": "step_update", "step_update": {"state": "DONE", "usage": {"total_tokens": -Infinity}}}',
    '{"event": "step_update", "step_update": {"state": "DONE", "usage": {"total_tokens": -5}}}',
    '{"event": "step_update", "step_update": {"state": "DONE", "step_type": "tool", "tool_name": ["x"]}}',
    '{"event": "result", "result": 5}',
    '{"usage": "x"}', '{"usage": {"input_tokens": 1e999, "output_tokens": 1}}', '{"usage": {"input_tokens": [1], "output_tokens": "x"}}',
    '{"choices": [5]}', '{"choices": 5}', '{"message": {"content": [5]}}', '{"message": 5}',
    '{"type": "turn.completed", "usage": 5}', '{"type": "result", "usage": {"input_tokens": "x"}}', '{"type": ["x"]}',
    "data: 5", "data: null", "data: [1]", "event: error",
]
GOOD = step(500, step_type="tool", tool_name="run_command").decode()


class OddEvents(unittest.TestCase):
    """What runs next to the agent can write into its stdout, so any line of stream.jsonl may have any shape. The host reads that file
    every few seconds (run-supervisor, the status page, the dashboard, the token ledger): none of those may raise on a line, lose the
    lines around it, or turn a number in it into `inf` or `nan` - `Infinity` in the ledger would stop dispatching until the next UTC day."""

    def test_token_figures_of_any_type_are_whole_numbers_or_zero(self):
        for x, want in ((7, 7), (7.9, 7), (0, 0), (-3, 0), (True, 0), ("7", 0), (None, 0), ([1], 0), ({"a": 1}, 0),
                        (float("inf"), 0), (float("-inf"), 0), (float("nan"), 0)):
            self.assertEqual(runtimes.token_count(x), want, repr(x))

    def test_json_numbers_beyond_the_float_range_and_nan_count_as_zero(self):
        self.assertEqual(runtimes._loads('{"a": 1e999, "b": NaN, "c": -Infinity, "d": 2.5, "e": 3}'), dict(a=0, b=0, c=0, d=2.5, e=3))

    def test_token_total_skips_every_odd_line_and_keeps_the_good_ones(self):
        for odd in ODD_LINES:
            with tempfile.TemporaryDirectory() as d:
                p = os.path.join(d, "stream.jsonl")
                put(p, "\n".join([GOOD, odd, GOOD]) + "\n")
                self.assertEqual(sbx.stream_tokens(p), 1000, odd)

    def test_scanning_never_raises_and_never_yields_a_non_finite_number(self):
        for rt in sorted(runtimes.RUNTIMES):
            for odd in ODD_LINES:
                for lines in ([GOOD + "\n", odd + "\n", GOOD + "\n"], [odd], [odd, odd]):
                    with self.subTest(runtime=rt, line=odd):
                        st = runtimes.scan(rt, lines)
                        tokens = st["tokens"]
                        self.assertTrue(tokens is None or (isinstance(tokens, (int, float)) and tokens == tokens and abs(tokens) != float("inf")), tokens)
                        self.assertIsInstance(st["tools"], int)
                        json.dumps(st, allow_nan=False)           # what is stored in meta.json and sent to the browser

    def test_the_lines_around_an_odd_one_still_count(self):
        for odd in ODD_LINES:
            st = runtimes.scan("agy", [GOOD + "\n", odd + "\n", GOOD + "\n"])
            self.assertEqual(st["tokens"], 1000, odd)
            self.assertGreaterEqual(st["tools"], 2, odd)

    def test_a_file_with_an_odd_line_is_scanned_like_the_same_lines_in_memory(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "stream.jsonl")
            lines = [GOOD] + ODD_LINES + ["{not json", GOOD]
            put(p, ("\n".join(lines) + "\n").encode() + b"\xe9 \xff\n")
            st = runtimes.scan_file("agy", p)
            self.assertEqual(st, runtimes.scan("agy", [l + "\n" for l in lines] + ["� �\n"]))
            self.assertEqual(st["tokens"], 1000)

    def test_the_status_page_shows_a_run_whose_stream_has_odd_lines(self):
        fake = ('#!/bin/sh\ncase "$*" in\n  "ps -a --format {{.Names}}") echo worker-1 ;;\n'
                '  "inspect -f {{.State.Status}} worker-1") echo exited ;;\n  "inspect -f {{.RestartCount}} worker-1") echo 0 ;;\nesac\nexit 0\n')
        with tempfile.TemporaryDirectory() as td:
            env, runs = sandbox(td, fake)
            stream = "\n".join([GOOD] + ODD_LINES + [GOOD]) + "\n"
            write_run(runs, "20261012T000000Z-worker-1", dict(reason="ok", worker="worker-1"),
                      {"finished": "2026-10-12T00:10:00Z\n", "exitcode": "0\n", "started": "2026-10-12T00:00:00Z\n", "stream.jsonl": stream})
            for flags in (["--json"], []):
                r = subprocess.run([sys.executable, os.path.join(ROOT, "bin", "status"), "--no-quota"] + flags, capture_output=True, env=env, timeout=120)
                self.assertNotIn("Traceback", r.stderr.decode("utf-8", "replace"))
                if flags:
                    w = json.loads(r.stdout.decode("utf-8"))["workers"][0]
                    self.assertEqual((w["name"], w["state"], w["tokens"]), ("worker-1", "FINISHED (ok)", 1000))
                else:
                    self.assertIn("worker-1", r.stdout.decode("utf-8", "replace"))


class NoStrictDecoding(unittest.TestCase):
    """The class of bug above comes back with every new `subprocess.run(..., text=True)`: in the runtime code (bin/, web/) a call that
    decodes text must say what happens to a byte that is not UTF-8."""
    CALLS = {"subprocess.run", "subprocess.Popen", "subprocess.check_output", "subprocess.check_call", "subprocess.call"}

    def test_every_text_mode_subprocess_call_in_the_runtime_code_sets_errors(self):
        bad = []
        for top in ("bin", "web"):
            for dp, dn, fn in os.walk(os.path.join(ROOT, top)):
                dn[:] = [x for x in dn if x not in ("node_modules", "__pycache__", "static")]
                for f in fn:
                    p = os.path.join(dp, f)
                    try:
                        src = get(p).decode("utf-8")
                    except (UnicodeDecodeError, OSError):
                        continue
                    if not (f.endswith(".py") or (src.startswith("#!") and "python" in src.split("\n", 1)[0])):
                        continue
                    for n in ast.walk(ast.parse(src)):
                        if isinstance(n, ast.Call) and ast.unparse(n.func) in self.CALLS:
                            kws = {k.arg: ast.unparse(k.value) for k in n.keywords}
                            if ("errors" not in kws and any(kws.get(k, "False") != "False" for k in ("text", "universal_newlines", "encoding"))):
                                bad.append(f"{os.path.relpath(p, ROOT)}:{n.lineno}")
        self.assertEqual(bad, [], "text=True without errors=: a byte that is not UTF-8 raises here; add errors=\"replace\"")


class AuditLines(unittest.TestCase):
    """runs/ALERT is the owner's audit trail and a message written to it can be built from text a worker shaped (a failing test's
    output, a reviewer's reply, a command's stderr). It must stay one line per record, so that text cannot pass for another alert
    or steer a terminal, and writing it must not raise for a character that cannot be encoded."""
    SPECIAL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")
    STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ ")

    def written(self, *messages):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(sbx, "RUNS", td):
            for m in messages:
                sbx.alert(m)
            return get(os.path.join(td, "ALERT")).decode("utf-8")

    def test_a_message_with_line_breaks_stays_one_record(self):
        forged = "2001-01-01T00:00:00Z KILLSWITCH OFF"
        text = self.written("task 1 blocked: boom\n" + forged, "second")
        lines = text.split("\n")
        self.assertEqual(len(lines), 3, text)
        self.assertEqual(lines[2], "")
        self.assertTrue(all(self.STAMP.match(l) for l in lines[:2]), text)
        self.assertIn(forged, lines[0])
        self.assertEqual([l for l in lines if l.startswith("2001-")], [])

    def test_every_line_break_and_control_character_is_replaced(self):
        for ch in ("\n", "\r", "\r\n", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029", "\x1b[2J", "\x00", "\x07", "\x7f",
                   "\u202e", "\u2066"):
            text = self.written(f"a{ch}b")
            self.assertEqual(text.count("\n"), 1, repr(ch))
            self.assertIsNone(self.SPECIAL.search(text[:-1]), repr(ch))
            self.assertRegex(text, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ a.+b\n$", repr(ch))

    def test_ordinary_text_is_written_as_it_is(self):
        self.assertTrue(self.written("caf\u00e9 \u2013 ok \u2714 [x] {y}").endswith(" caf\u00e9 \u2013 ok \u2714 [x] {y}\n"))

    def test_a_character_that_cannot_be_encoded_is_written_not_raised(self):
        text = self.written("caf\udce9 \ud800 ok")
        self.assertIn("caf", text)
        self.assertTrue(text.endswith(" ok\n"), text)


class AuditRotate(unittest.TestCase):
    """bin/audit-rotate is the only way the audit logs may shrink: it must keep every record whole and every archive name safe,
    whatever the lines hold."""
    def rotate(self, **files):
        """Runs `bin/audit-rotate --force` on a runs dir with these files (name -> bytes); returns the process, the files afterwards
        and the archive (name -> bytes)."""
        with tempfile.TemporaryDirectory() as td:
            runs = os.path.join(td, "runs")
            os.makedirs(runs)
            for name, data in files.items():
                put(os.path.join(runs, name), data)
            env = dict(os.environ, SANDBOX_TEST="1", SANDBOX_RUNS_DIR=runs)
            r = subprocess.run([sys.executable, os.path.join(ROOT, "bin", "audit-rotate"), "--force"], env=env, capture_output=True, timeout=60)
            arch = os.path.join(runs, "archive")
            found = {n: get(os.path.join(arch, n)) for n in sorted(os.listdir(arch))} if os.path.isdir(arch) else {}
            return r, {n: get(os.path.join(runs, n)) for n in files}, found

    def test_a_multi_line_alert_moves_as_one_record_into_safely_named_files(self):
        old = (b"2026-09-30T23:00:00Z foreman: task 1 blocked: an older month\n"
               b"2026-10-20T10:00:00Z foreman: task 2 blocked: FAILED tests/t.py::x\n"
               b"--- a/src/x.py\n"                       # a slash within the first seven characters: it used to be part of the file name
               b"/bin/sh: 1: tool: not found\r\n"
               b'  File "t.py", line 3\n'
               b"caf\xe9 not utf-8\n"
               b"\n"
               b"2026-10-20T11:00:00Z a second old one\n")
        new = (sbx.today() + "T09:00:00Z today's alert\ncontinuation of today's alert\n").encode()
        r, files, archive = self.rotate(ALERT=old + new)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertEqual(sorted(archive), ["ALERT.2026-09", "ALERT.2026-10"])
        first, rest = old.split(b"\n", 1)
        self.assertEqual(archive["ALERT.2026-09"], first + b"\n")
        self.assertEqual(archive["ALERT.2026-10"], rest)
        self.assertTrue(files["ALERT"].startswith(new), files["ALERT"])
        tail = files["ALERT"][len(new):]
        self.assertEqual(tail.count(b"\n"), 1, tail)
        self.assertIn(b"audit-rotate: archived 8 line(s)", tail)
        self.assertIn(b"8 line(s) of earlier days", r.stdout)

    def test_ledger_lines_without_a_usable_date_stay_where_they_are(self):
        lines = [b'{"date":"2026-10-20","run_id":"a"}', b"not json", b"[1,2]", b'{"date":5}', b'{"date":"../../etc/x"}',
                 b'{"date":"2026-10-21","run_id":"b"}', json.dumps({"date": sbx.today(), "run_id": "c"}).encode()]
        r, files, archive = self.rotate(**{"ledger.jsonl": b"\n".join(lines) + b"\n"})
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertEqual(archive, {"ledger.jsonl.2026-10": lines[0] + b"\n" + lines[5] + b"\n"})
        self.assertEqual(files["ledger.jsonl"], b"\n".join(lines[1:5] + [lines[6]]) + b"\n")


def tearDownModule():
    shutil.rmtree(T, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
