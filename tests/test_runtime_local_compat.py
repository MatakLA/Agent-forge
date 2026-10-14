#!/usr/bin/env python3
"""Tests for local OpenAI-compatible runtimes (ollama, vllm, lmstudio, llamacpp) in bin/lib/runtimes.py.

Public interface:
- Run directly: python3 tests/test_runtime_local_compat.py
- Run via unittest: python3 -m unittest tests/test_runtime_local_compat.py
- Run via pytest: python3 -m pytest -q tests/test_runtime_local_compat.py

Important invariants:
- RUNTIMES entries for ollama, vllm, lmstudio, llamacpp use AGY_IMAGE with endpoint=True, api=False.
- hosts(rt, endpoint_url) returns only the endpoint's own host (never a static public allowlist host).
- cost_model(rt) classifies pricing as unknown/self-hosted with zero fabricated USD amounts.
- command(rt, ...) constructs curl HTTP POST invocations with prompt on stdin and safe URL defaults.
- scan(rt, ...) parses single/multiline JSON and SSE streams with tokens, tools, and result status.
- Zero real network calls and no real API keys anywhere in tests.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))

# Top-level smoke-import check to fail loudly if import breaks
try:
    import runtimes
    from runtimes import hosts, cost_model, command, scan, scan_file, RUNTIMES, is_api, uses_endpoint
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.stderr.write(f"smoke-import failed for runtimes: {e}\n")
    sys.exit(1)

LOCAL_RUNTIMES = ("ollama", "vllm", "lmstudio", "llamacpp")
NODE_AVAILABLE = bool(shutil.which("node") or os.environ.get("LOCAL_COMPAT_SERVER_PORT"))


class TestLocalCompatRuntime(unittest.TestCase):
    """Unit tests for local OpenAI-compatible runtimes against fake server fixture."""

    server_proc = None
    server_port = None
    server_temp_dir = None

    @classmethod
    def _start_fake_server(cls):
        fixture_path = os.path.join(ROOT, "tests", "fixtures", "fake-local-compat-server.js")
        cls.server_proc = subprocess.Popen(
            ["node", fixture_path, "0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        port = None
        start_time = time.time()
        while time.time() - start_time < 5.0:
            line = cls.server_proc.stdout.readline()
            if not line:
                break
            m = re.search(r"127\.0\.0\.1:(\d+)", line)
            if m:
                port = int(m.group(1))
                break
        if not port:
            if cls.server_proc.poll() is not None:
                raise RuntimeError(f"Fake server exited prematurely with code {cls.server_proc.returncode}")
            raise RuntimeError("Could not capture port from fake-local-compat-server.js output")
        return port

    @classmethod
    def _wait_for_server(cls, port):
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return
            except (OSError, ConnectionRefusedError):
                time.sleep(0.05)
        raise RuntimeError(f"Fake local compat server failed to accept connections on port {port}")

    @classmethod
    def setUpClass(cls):
        port_env = os.environ.get("LOCAL_COMPAT_SERVER_PORT")
        if port_env:
            cls.server_port = int(port_env)
            cls._wait_for_server(cls.server_port)
        elif shutil.which("node"):
            try:
                cls.server_port = cls._start_fake_server()
                os.environ["LOCAL_COMPAT_SERVER_PORT"] = str(cls.server_port)
                cls._wait_for_server(cls.server_port)
            except Exception as e:
                cls.server_port = None
                sys.stderr.write(f"Warning: could not start fake-local-compat-server.js: {e}\n")
        else:
            cls.server_port = None

        if cls.server_port:
            cls.server_temp_dir = tempfile.TemporaryDirectory()
            stub_curl = os.path.join(cls.server_temp_dir.name, "curl")
            with open(stub_curl, "w", encoding="utf-8") as f:
                f.write("""#!/usr/bin/env bash
if [ -x /usr/bin/curl ]; then
  exec /usr/bin/curl "$@"
elif [ -x /bin/curl ]; then
  exec /bin/curl "$@"
else
  python3 -c "
import sys, urllib.request
url = None
data = None
headers = {}
args = sys.argv[1:]
i = 0
while i < len(args):
    a = args[i]
    if a in ('-X', '--request') and i + 1 < len(args):
        i += 2
    elif a in ('-H', '--header') and i + 1 < len(args):
        k, v = args[i+1].split(':', 1)
        headers[k.strip()] = v.strip()
        i += 2
    elif a in ('-d', '--data') and i + 1 < len(args):
        val = args[i+1]
        data = sys.stdin.read().encode('utf-8') if val == '@-' else val.encode('utf-8')
        i += 2
    elif not a.startswith('-'):
        url = a
        i += 1
    else:
        i += 1
req = urllib.request.Request(url, data=data, headers=headers)
with urllib.request.urlopen(req) as resp:
    sys.stdout.write(resp.read().decode('utf-8'))
" "$@"
fi
""")
            os.chmod(stub_curl, 0o755)
            for rt in LOCAL_RUNTIMES:
                stub_rt = os.path.join(cls.server_temp_dir.name, rt)
                with open(stub_rt, "w", encoding="utf-8") as f:
                    f.write(f"#!/usr/bin/env bash\necho 'stub {rt}' >&2\n")
                os.chmod(stub_rt, 0o755)
            os.environ["PATH"] = f"{cls.server_temp_dir.name}:{os.environ.get('PATH', '')}"

    @classmethod
    def tearDownClass(cls):
        if cls.server_proc:
            try:
                cls.server_proc.terminate()
                cls.server_proc.wait(timeout=2)
            except Exception:
                pass
        if cls.server_temp_dir:
            try:
                cls.server_temp_dir.cleanup()
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Dedicated per-runtime test methods for ollama
    # -------------------------------------------------------------------------
    def test_ollama_metadata_and_registration(self):
        """RUNTIMES['ollama'] is properly configured with endpoint=True and port 11434."""
        self.assertIn("ollama", RUNTIMES)
        entry = RUNTIMES["ollama"]
        self.assertEqual(entry["image"], runtimes.AGY_IMAGE)
        self.assertIsNone(entry["key"])
        self.assertEqual(entry["hosts"], [])
        self.assertFalse(entry["api"])
        self.assertTrue(entry["endpoint"])
        self.assertTrue(entry["optional_key"])
        self.assertEqual(entry["default_port"], 11434)
        self.assertEqual(entry["default_path"], "/v1/chat/completions")
        self.assertFalse(is_api("ollama"))
        self.assertTrue(uses_endpoint("ollama"))
        assert "ollama" in RUNTIMES
        assert entry["image"] == runtimes.AGY_IMAGE
        assert entry["key"] is None
        assert entry["hosts"] == []
        assert entry["api"] is False
        assert entry["endpoint"] is True
        assert entry["default_port"] == 11434
        assert entry["default_path"] == "/v1/chat/completions"
        assert is_api("ollama") is False
        assert uses_endpoint("ollama") is True

    def test_ollama_hosts_resolution(self):
        """hosts('ollama', ...) returns only endpoint host, never a static public host."""
        self.assertEqual(hosts("ollama", ""), [])
        self.assertEqual(hosts("ollama"), [])
        self.assertEqual(hosts("ollama", "http://127.0.0.1:11434/v1"), ["127.0.0.1"])
        self.assertEqual(hosts("ollama", "http://localhost:11434/v1/chat/completions"), ["localhost"])
        self.assertNotIn("api.openai.com", hosts("ollama", "http://127.0.0.1:11434/v1"))
        assert hosts("ollama", "") == []
        assert hosts("ollama") == []
        assert hosts("ollama", "http://127.0.0.1:11434/v1") == ["127.0.0.1"]
        assert hosts("ollama", "http://localhost:11434/v1/chat/completions") == ["localhost"]
        assert "api.openai.com" not in hosts("ollama", "http://127.0.0.1:11434/v1")

    def test_ollama_cost_model(self):
        """cost_model('ollama') states self-hosted/unknown with zero fabricated USD."""
        cm = cost_model("ollama")
        self.assertEqual(cm["kind"], "unknown")
        self.assertIn("Self-hosted", cm["detail"])
        self.assertIn("ollama", cm["detail"].lower())
        self.assertIn("no per-token charges", cm["detail"])
        self.assertNotIn("$", cm["detail"])
        self.assertNotRegex(cm["detail"], r"\$\d+\.?\d*")
        assert cm["kind"] == "unknown"
        assert "Self-hosted" in cm["detail"]
        assert "ollama" in cm["detail"].lower()
        assert "no per-token charges" in cm["detail"]
        assert "$" not in cm["detail"]

    def test_ollama_command_argv_and_stdin(self):
        """command('ollama', ...) builds curl POST argv with prompt on stdin."""
        argv, stdin = command("ollama", "Fix memory leak in buffer", "llama3.2")
        self.assertEqual(argv[0], "curl")
        self.assertEqual(argv[1:4], ["-s", "-X", "POST"])
        self.assertEqual(argv[4], "http://127.0.0.1:11434/v1/chat/completions")
        self.assertIn("-H", argv)
        self.assertIn("Content-Type: application/json", argv)
        self.assertIn("-d", argv)
        self.assertIn("@-", argv)
        self.assertNotIn("Fix memory leak in buffer", argv)
        body = json.loads(stdin)
        self.assertEqual(body["model"], "llama3.2")
        self.assertEqual(body["messages"][-1]["content"], "Fix memory leak in buffer")
        assert argv[0] == "curl"
        assert argv[1:4] == ["-s", "-X", "POST"]
        assert argv[4] == "http://127.0.0.1:11434/v1/chat/completions"
        assert "Fix memory leak in buffer" not in argv
        assert body["model"] == "llama3.2"
        assert body["messages"][-1]["content"] == "Fix memory leak in buffer"

    def test_ollama_scan_success(self):
        """scan('ollama', ...) parses standard OpenAI completion JSON into SUCCESS."""
        sample = json.dumps({
            "id": "chatcmpl-ollama-001",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "Ollama response"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
        })
        res = scan("ollama", [sample])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "Ollama response")
        self.assertEqual(res["tokens"], 120)
        self.assertEqual(res["conversation_id"], "chatcmpl-ollama-001")
        self.assertIsNone(res["result"]["cost_usd"])
        assert res["result"]["status"] == "SUCCESS"
        assert res["result"]["response"] == "Ollama response"
        assert res["tokens"] == 120
        assert res["conversation_id"] == "chatcmpl-ollama-001"
        assert res["result"]["cost_usd"] is None

    # -------------------------------------------------------------------------
    # Dedicated per-runtime test methods for vllm
    # -------------------------------------------------------------------------
    def test_vllm_metadata_and_registration(self):
        """RUNTIMES['vllm'] is properly configured with endpoint=True, key='compat.key', and port 8000."""
        self.assertIn("vllm", RUNTIMES)
        entry = RUNTIMES["vllm"]
        self.assertEqual(entry["image"], runtimes.AGY_IMAGE)
        self.assertEqual(entry["key"], "compat.key")
        self.assertEqual(entry["hosts"], [])
        self.assertFalse(entry["api"])
        self.assertTrue(entry["endpoint"])
        self.assertTrue(entry["optional_key"])
        self.assertEqual(entry["default_port"], 8000)
        self.assertEqual(entry["default_path"], "/v1/chat/completions")
        self.assertFalse(is_api("vllm"))
        self.assertTrue(uses_endpoint("vllm"))
        assert "vllm" in RUNTIMES
        assert entry["image"] == runtimes.AGY_IMAGE
        assert entry["key"] == "compat.key"
        assert entry["hosts"] == []
        assert entry["api"] is False
        assert entry["endpoint"] is True
        assert entry["default_port"] == 8000
        assert is_api("vllm") is False
        assert uses_endpoint("vllm") is True

    def test_vllm_hosts_resolution(self):
        """hosts('vllm', ...) returns only endpoint host, never a static public host."""
        self.assertEqual(hosts("vllm", ""), [])
        self.assertEqual(hosts("vllm"), [])
        self.assertEqual(hosts("vllm", "http://127.0.0.1:8000/v1"), ["127.0.0.1"])
        self.assertEqual(hosts("vllm", "https://vllm.corp.local:8443/v1"), ["vllm.corp.local"])
        self.assertNotIn("api.mistral.ai", hosts("vllm", "http://127.0.0.1:8000/v1"))
        assert hosts("vllm", "") == []
        assert hosts("vllm") == []
        assert hosts("vllm", "http://127.0.0.1:8000/v1") == ["127.0.0.1"]
        assert hosts("vllm", "https://vllm.corp.local:8443/v1") == ["vllm.corp.local"]
        assert "api.mistral.ai" not in hosts("vllm", "http://127.0.0.1:8000/v1")

    def test_vllm_cost_model(self):
        """cost_model('vllm') states self-hosted/unknown with zero fabricated USD."""
        cm = cost_model("vllm")
        self.assertEqual(cm["kind"], "unknown")
        self.assertIn("Self-hosted", cm["detail"])
        self.assertIn("vllm", cm["detail"].lower())
        self.assertIn("no per-token charges", cm["detail"])
        self.assertNotIn("$", cm["detail"])
        self.assertNotRegex(cm["detail"], r"\$\d+\.?\d*")
        assert cm["kind"] == "unknown"
        assert "Self-hosted" in cm["detail"]
        assert "vllm" in cm["detail"].lower()
        assert "no per-token charges" in cm["detail"]
        assert "$" not in cm["detail"]

    def test_vllm_command_argv_and_stdin(self):
        """command('vllm', ...) builds curl POST argv with prompt on stdin."""
        argv, stdin = command("vllm", "Deploy model cluster", "meta-llama/Llama-3-70b-Instruct")
        self.assertEqual(argv[0], "curl")
        self.assertEqual(argv[1:4], ["-s", "-X", "POST"])
        self.assertEqual(argv[4], "http://127.0.0.1:8000/v1/chat/completions")
        self.assertNotIn("Deploy model cluster", argv)
        body = json.loads(stdin)
        self.assertEqual(body["model"], "meta-llama/Llama-3-70b-Instruct")
        self.assertEqual(body["messages"][-1]["content"], "Deploy model cluster")
        assert argv[0] == "curl"
        assert argv[1:4] == ["-s", "-X", "POST"]
        assert argv[4] == "http://127.0.0.1:8000/v1/chat/completions"
        assert "Deploy model cluster" not in argv
        assert body["model"] == "meta-llama/Llama-3-70b-Instruct"
        assert body["messages"][-1]["content"] == "Deploy model cluster"

    def test_vllm_scan_success(self):
        """scan('vllm', ...) parses standard OpenAI completion JSON into SUCCESS."""
        sample = json.dumps({
            "id": "chatcmpl-vllm-001",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "vLLM output text"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 150, "total_tokens": 650}
        })
        res = scan("vllm", [sample])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "vLLM output text")
        self.assertEqual(res["tokens"], 650)
        self.assertEqual(res["conversation_id"], "chatcmpl-vllm-001")
        self.assertIsNone(res["result"]["cost_usd"])
        assert res["result"]["status"] == "SUCCESS"
        assert res["result"]["response"] == "vLLM output text"
        assert res["tokens"] == 650
        assert res["conversation_id"] == "chatcmpl-vllm-001"
        assert res["result"]["cost_usd"] is None

    # -------------------------------------------------------------------------
    # Dedicated per-runtime test methods for lmstudio
    # -------------------------------------------------------------------------
    def test_lmstudio_metadata_and_registration(self):
        """RUNTIMES['lmstudio'] is properly configured with endpoint=True and port 1234."""
        self.assertIn("lmstudio", RUNTIMES)
        entry = RUNTIMES["lmstudio"]
        self.assertEqual(entry["image"], runtimes.AGY_IMAGE)
        self.assertIsNone(entry["key"])
        self.assertEqual(entry["hosts"], [])
        self.assertFalse(entry["api"])
        self.assertTrue(entry["endpoint"])
        self.assertTrue(entry["optional_key"])
        self.assertEqual(entry["default_port"], 1234)
        self.assertEqual(entry["default_path"], "/v1/chat/completions")
        self.assertFalse(is_api("lmstudio"))
        self.assertTrue(uses_endpoint("lmstudio"))
        assert "lmstudio" in RUNTIMES
        assert entry["image"] == runtimes.AGY_IMAGE
        assert entry["key"] is None
        assert entry["hosts"] == []
        assert entry["api"] is False
        assert entry["endpoint"] is True
        assert entry["default_port"] == 1234
        assert is_api("lmstudio") is False
        assert uses_endpoint("lmstudio") is True

    def test_lmstudio_hosts_resolution(self):
        """hosts('lmstudio', ...) returns only endpoint host, never a static public host."""
        self.assertEqual(hosts("lmstudio", ""), [])
        self.assertEqual(hosts("lmstudio"), [])
        self.assertEqual(hosts("lmstudio", "http://127.0.0.1:1234/v1"), ["127.0.0.1"])
        self.assertEqual(hosts("lmstudio", "http://192.168.1.100:1234"), ["192.168.1.100"])
        self.assertNotIn("api.github.com", hosts("lmstudio", "http://127.0.0.1:1234/v1"))
        assert hosts("lmstudio", "") == []
        assert hosts("lmstudio") == []
        assert hosts("lmstudio", "http://127.0.0.1:1234/v1") == ["127.0.0.1"]
        assert hosts("lmstudio", "http://192.168.1.100:1234") == ["192.168.1.100"]
        assert "api.github.com" not in hosts("lmstudio", "http://127.0.0.1:1234/v1")

    def test_lmstudio_cost_model(self):
        """cost_model('lmstudio') states self-hosted/unknown with zero fabricated USD."""
        cm = cost_model("lmstudio")
        self.assertEqual(cm["kind"], "unknown")
        self.assertIn("Self-hosted", cm["detail"])
        self.assertIn("lm studio", cm["detail"].lower())
        self.assertIn("no per-token charges", cm["detail"])
        self.assertNotIn("$", cm["detail"])
        self.assertNotRegex(cm["detail"], r"\$\d+\.?\d*")
        assert cm["kind"] == "unknown"
        assert "Self-hosted" in cm["detail"]
        assert "lm studio" in cm["detail"].lower()
        assert "no per-token charges" in cm["detail"]
        assert "$" not in cm["detail"]

    def test_lmstudio_command_argv_and_stdin(self):
        """command('lmstudio', ...) builds curl POST argv with prompt on stdin."""
        argv, stdin = command("lmstudio", "Refactor UI components", "lmstudio-community/mistral-7b")
        self.assertEqual(argv[0], "curl")
        self.assertEqual(argv[1:4], ["-s", "-X", "POST"])
        self.assertEqual(argv[4], "http://127.0.0.1:1234/v1/chat/completions")
        self.assertNotIn("Refactor UI components", argv)
        body = json.loads(stdin)
        self.assertEqual(body["model"], "lmstudio-community/mistral-7b")
        self.assertEqual(body["messages"][-1]["content"], "Refactor UI components")
        assert argv[0] == "curl"
        assert argv[1:4] == ["-s", "-X", "POST"]
        assert argv[4] == "http://127.0.0.1:1234/v1/chat/completions"
        assert "Refactor UI components" not in argv
        assert body["model"] == "lmstudio-community/mistral-7b"
        assert body["messages"][-1]["content"] == "Refactor UI components"

    def test_lmstudio_scan_success(self):
        """scan('lmstudio', ...) parses standard OpenAI completion JSON into SUCCESS."""
        sample = json.dumps({
            "id": "chatcmpl-lmstudio-001",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "LM Studio generated code"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280}
        })
        res = scan("lmstudio", [sample])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "LM Studio generated code")
        self.assertEqual(res["tokens"], 280)
        self.assertEqual(res["conversation_id"], "chatcmpl-lmstudio-001")
        self.assertIsNone(res["result"]["cost_usd"])
        assert res["result"]["status"] == "SUCCESS"
        assert res["result"]["response"] == "LM Studio generated code"
        assert res["tokens"] == 280
        assert res["conversation_id"] == "chatcmpl-lmstudio-001"
        assert res["result"]["cost_usd"] is None

    # -------------------------------------------------------------------------
    # Dedicated per-runtime test methods for llamacpp
    # -------------------------------------------------------------------------
    def test_llamacpp_metadata_and_registration(self):
        """RUNTIMES['llamacpp'] is properly configured with endpoint=True and port 8080."""
        self.assertIn("llamacpp", RUNTIMES)
        entry = RUNTIMES["llamacpp"]
        self.assertEqual(entry["image"], runtimes.AGY_IMAGE)
        self.assertIsNone(entry["key"])
        self.assertEqual(entry["hosts"], [])
        self.assertFalse(entry["api"])
        self.assertTrue(entry["endpoint"])
        self.assertTrue(entry["optional_key"])
        self.assertEqual(entry["default_port"], 8080)
        self.assertEqual(entry["default_path"], "/v1/chat/completions")
        self.assertFalse(is_api("llamacpp"))
        self.assertTrue(uses_endpoint("llamacpp"))
        assert "llamacpp" in RUNTIMES
        assert entry["image"] == runtimes.AGY_IMAGE
        assert entry["key"] is None
        assert entry["hosts"] == []
        assert entry["api"] is False
        assert entry["endpoint"] is True
        assert entry["default_port"] == 8080
        assert is_api("llamacpp") is False
        assert uses_endpoint("llamacpp") is True

    def test_llamacpp_hosts_resolution(self):
        """hosts('llamacpp', ...) returns only endpoint host, never a static public host."""
        self.assertEqual(hosts("llamacpp", ""), [])
        self.assertEqual(hosts("llamacpp"), [])
        self.assertEqual(hosts("llamacpp", "http://127.0.0.1:8080/v1"), ["127.0.0.1"])
        self.assertEqual(hosts("llamacpp", "http://server.home.arpa:8080"), ["server.home.arpa"])
        self.assertNotIn("api.anthropic.com", hosts("llamacpp", "http://127.0.0.1:8080/v1"))
        assert hosts("llamacpp", "") == []
        assert hosts("llamacpp") == []
        assert hosts("llamacpp", "http://127.0.0.1:8080/v1") == ["127.0.0.1"]
        assert hosts("llamacpp", "http://server.home.arpa:8080") == ["server.home.arpa"]
        assert "api.anthropic.com" not in hosts("llamacpp", "http://127.0.0.1:8080/v1")

    def test_llamacpp_cost_model(self):
        """cost_model('llamacpp') states self-hosted/unknown with zero fabricated USD."""
        cm = cost_model("llamacpp")
        self.assertEqual(cm["kind"], "unknown")
        self.assertIn("Self-hosted", cm["detail"])
        self.assertIn("llama.cpp", cm["detail"].lower())
        self.assertIn("no per-token charges", cm["detail"])
        self.assertNotIn("$", cm["detail"])
        self.assertNotRegex(cm["detail"], r"\$\d+\.?\d*")
        assert cm["kind"] == "unknown"
        assert "Self-hosted" in cm["detail"]
        assert "llama.cpp" in cm["detail"].lower()
        assert "no per-token charges" in cm["detail"]
        assert "$" not in cm["detail"]

    def test_llamacpp_command_argv_and_stdin(self):
        """command('llamacpp', ...) builds curl POST argv with prompt on stdin."""
        argv, stdin = command("llamacpp", "Write unit test suite", "ggml-model-q4_0.gguf")
        self.assertEqual(argv[0], "curl")
        self.assertEqual(argv[1:4], ["-s", "-X", "POST"])
        self.assertEqual(argv[4], "http://127.0.0.1:8080/v1/chat/completions")
        self.assertNotIn("Write unit test suite", argv)
        body = json.loads(stdin)
        self.assertEqual(body["model"], "ggml-model-q4_0.gguf")
        self.assertEqual(body["messages"][-1]["content"], "Write unit test suite")
        assert argv[0] == "curl"
        assert argv[1:4] == ["-s", "-X", "POST"]
        assert argv[4] == "http://127.0.0.1:8080/v1/chat/completions"
        assert "Write unit test suite" not in argv
        assert body["model"] == "ggml-model-q4_0.gguf"
        assert body["messages"][-1]["content"] == "Write unit test suite"

    def test_llamacpp_scan_success(self):
        """scan('llamacpp', ...) parses standard OpenAI completion JSON into SUCCESS."""
        sample = json.dumps({
            "id": "chatcmpl-llamacpp-001",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "llama.cpp response text"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 400, "completion_tokens": 100, "total_tokens": 500}
        })
        res = scan("llamacpp", [sample])
        self.assertEqual(res["result"]["status"], "SUCCESS")
        self.assertEqual(res["result"]["response"], "llama.cpp response text")
        self.assertEqual(res["tokens"], 500)
        self.assertEqual(res["conversation_id"], "chatcmpl-llamacpp-001")
        self.assertIsNone(res["result"]["cost_usd"])
        assert res["result"]["status"] == "SUCCESS"
        assert res["result"]["response"] == "llama.cpp response text"
        assert res["tokens"] == 500
        assert res["conversation_id"] == "chatcmpl-llamacpp-001"
        assert res["result"]["cost_usd"] is None

    # -------------------------------------------------------------------------
    # Cross-cutting tests covering all four local-compat runtimes
    # -------------------------------------------------------------------------
    def test_all_local_compat_hosts_isolation_never_returns_static_public_hosts(self):
        """hosts(rt, endpoint_url) isolates egress and never returns static public allowlist hosts."""
        public_hosts = {"api.openai.com", "api.anthropic.com", "api.github.com", "api.githubcopilot.com", "api.mistral.ai"}
        for rt in LOCAL_RUNTIMES:
            # Empty / None endpoint returns empty list
            self.assertEqual(hosts(rt, ""), [], f"{rt} hosts('') should be []")
            self.assertEqual(hosts(rt, None), [], f"{rt} hosts(None) should be []")
            self.assertEqual(hosts(rt), [], f"{rt} hosts() should be []")
            self.assertEqual(hosts(rt, "invalid://"), [], f"{rt} hosts('invalid://') should be []")
            self.assertEqual(hosts(rt, "ftp://myhost:21"), [], f"{rt} hosts('ftp://') should be []")
            assert hosts(rt, "") == []
            assert hosts(rt, None) == []
            assert hosts(rt) == []
            assert hosts(rt, "invalid://") == []
            assert hosts(rt, "ftp://myhost:21") == []

            # Valid model endpoints resolve only the specific target host
            h_local = hosts(rt, "http://127.0.0.1:9000/v1")
            self.assertEqual(h_local, ["127.0.0.1"], f"{rt} failed to resolve 127.0.0.1")
            assert h_local == ["127.0.0.1"]
            h_domain = hosts(rt, "https://Custom-Host.Internal:8443/chat")
            self.assertEqual(h_domain, ["custom-host.internal"], f"{rt} failed to lowercase host")
            assert h_domain == ["custom-host.internal"]

            # Static hosts list in RUNTIMES is empty
            self.assertEqual(RUNTIMES[rt]["hosts"], [], f"{rt} RUNTIMES entry must have empty static hosts")
            assert RUNTIMES[rt]["hosts"] == []

            # Never intersects with known public AI endpoints
            for h in hosts(rt, "http://127.0.0.1:8000"):
                self.assertNotIn(h, public_hosts)
                assert h not in public_hosts

    def test_all_local_compat_cost_model_honest_unknown_no_fabricated_usd(self):
        """cost_model(rt) states unknown/self-hosted with no fabricated USD or token charges."""
        for rt in LOCAL_RUNTIMES:
            cm = cost_model(rt)
            self.assertIsInstance(cm, dict)
            self.assertEqual(set(cm.keys()), {"kind", "detail"})
            self.assertEqual(cm["kind"], "unknown", f"{rt} cost_model kind must be 'unknown'")
            self.assertIn("Self-hosted", cm["detail"])
            self.assertIn("no per-token charges", cm["detail"])
            self.assertNotIn("$", cm["detail"], f"{rt} cost_model detail contains '$'")
            self.assertNotRegex(cm["detail"], r"\$\d+\.?\d*", f"{rt} detail fabricated a USD price")
            self.assertNotRegex(cm["detail"], r"\b0\.00\b", f"{rt} detail fabricated a zero USD price")
            assert cm["kind"] == "unknown"
            assert "Self-hosted" in cm["detail"]
            assert "no per-token charges" in cm["detail"]
            assert "$" not in cm["detail"]

    def test_all_local_compat_command_endpoint_url_and_env_normalization(self):
        """command(rt, ...) normalizes endpoint URLs with various paths and env fallbacks."""
        for rt in LOCAL_RUNTIMES:
            default_port = RUNTIMES[rt]["default_port"]
            default_url = f"http://127.0.0.1:{default_port}/v1/chat/completions"

            # 1. Unset endpoint uses default port and path
            old_ep = os.environ.pop("AF_ENDPOINT", None)
            old_compat = os.environ.pop("COMPAT_BASE_URL", None)
            try:
                argv, _ = command(rt, "task", "model")
                self.assertEqual(argv[4], default_url)
                assert argv[4] == default_url

                # 2. Explicit endpoint_url with /chat/completions preserved
                argv2, _ = command(rt, "task", "model", endpoint_url="http://10.0.0.5:9999/v1/chat/completions")
                self.assertEqual(argv2[4], "http://10.0.0.5:9999/v1/chat/completions")
                assert argv2[4] == "http://10.0.0.5:9999/v1/chat/completions"

                # 3. Endpoint ending with /v1
                argv3, _ = command(rt, "task", "model", endpoint_url="http://10.0.0.5:9999/v1")
                self.assertEqual(argv3[4], "http://10.0.0.5:9999/v1/chat/completions")
                assert argv3[4] == "http://10.0.0.5:9999/v1/chat/completions"

                # 4. Endpoint ending with /v1/
                argv4, _ = command(rt, "task", "model", endpoint_url="http://10.0.0.5:9999/v1/")
                self.assertEqual(argv4[4], "http://10.0.0.5:9999/v1/chat/completions")
                assert argv4[4] == "http://10.0.0.5:9999/v1/chat/completions"

                # 5. Base URL without /v1
                argv5, _ = command(rt, "task", "model", endpoint_url="http://10.0.0.5:9999")
                self.assertEqual(argv5[4], f"http://10.0.0.5:9999{RUNTIMES[rt]['default_path']}")
                assert argv5[4] == f"http://10.0.0.5:9999{RUNTIMES[rt]['default_path']}"

                # 6. Fallback to AF_ENDPOINT
                os.environ["AF_ENDPOINT"] = "http://af-host:7777/v1"
                argv6, _ = command(rt, "task", "model")
                self.assertEqual(argv6[4], "http://af-host:7777/v1/chat/completions")
                assert argv6[4] == "http://af-host:7777/v1/chat/completions"

                # 7. Fallback to COMPAT_BASE_URL
                os.environ.pop("AF_ENDPOINT", None)
                os.environ["COMPAT_BASE_URL"] = "http://compat-host:6666/v1"
                argv7, _ = command(rt, "task", "model")
                self.assertEqual(argv7[4], "http://compat-host:6666/v1/chat/completions")
                assert argv7[4] == "http://compat-host:6666/v1/chat/completions"
            finally:
                if old_ep is not None:
                    os.environ["AF_ENDPOINT"] = old_ep
                else:
                    os.environ.pop("AF_ENDPOINT", None)
                if old_compat is not None:
                    os.environ["COMPAT_BASE_URL"] = old_compat
                else:
                    os.environ.pop("COMPAT_BASE_URL", None)

    def test_all_local_compat_command_secrets_omitted_and_prompt_safety(self):
        """command(rt, ...) never leaks prompts or secret keys into argv; handles unicode and large payloads."""
        long_unicode_prompt = "Refactor prompt with unicode: 🚀 修复漏洞 ⚡ \n" * 500
        for rt in LOCAL_RUNTIMES:
            argv, stdin = command(rt, long_unicode_prompt, "local-model", budget_usd=10.0)
            argv_str = " ".join(argv)

            # Never puts prompt in argv
            self.assertNotIn("Refactor prompt with unicode", argv_str)
            self.assertNotIn("🚀", argv_str)
            self.assertNotIn("修复漏洞", argv_str)
            assert "Refactor prompt with unicode" not in argv_str
            assert "🚀" not in argv_str
            assert "修复漏洞" not in argv_str

            # Never puts secret keys or tokens in argv
            self.assertNotIn("compat.key", argv_str)
            self.assertNotIn("sk-", argv_str)
            self.assertNotIn("ghp_", argv_str)
            self.assertNotIn("bearer", argv_str.lower())
            assert "compat.key" not in argv_str
            assert "sk-" not in argv_str
            assert "ghp_" not in argv_str
            assert "bearer" not in argv_str.lower()

            # Payload correctly serializes unicode without corruption
            body = json.loads(stdin)
            self.assertEqual(body["messages"][-1]["content"], long_unicode_prompt)
            assert body["messages"][-1]["content"] == long_unicode_prompt

    def test_all_local_compat_command_conversation_structures(self):
        """command(rt, ...) handles string, list, and dict conversation history structures."""
        for rt in LOCAL_RUNTIMES:
            # 1. Plain string conversation ID
            _, stdin1 = command(rt, "follow-up question", "m1", conversation="ses_thread_987")
            body1 = json.loads(stdin1)
            self.assertEqual(body1["conversation_id"], "ses_thread_987")
            self.assertEqual(body1["messages"], [{"role": "user", "content": "follow-up question"}])
            assert body1["conversation_id"] == "ses_thread_987"
            assert body1["messages"] == [{"role": "user", "content": "follow-up question"}]

            # 2. List of messages as Python list
            history_list = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello."},
                {"role": "assistant", "content": "Hi there!"}
            ]
            _, stdin2 = command(rt, "Next step", "m1", conversation=history_list)
            body2 = json.loads(stdin2)
            self.assertEqual(len(body2["messages"]), 4)
            self.assertEqual(body2["messages"][0]["role"], "system")
            self.assertEqual(body2["messages"][-1]["content"], "Next step")
            assert len(body2["messages"]) == 4
            assert body2["messages"][0]["role"] == "system"
            assert body2["messages"][-1]["content"] == "Next step"

            # 3. List of messages as JSON string
            history_json = json.dumps(history_list)
            _, stdin3 = command(rt, "Next step", "m1", conversation=history_json)
            body3 = json.loads(stdin3)
            self.assertEqual(len(body3["messages"]), 4)
            assert len(body3["messages"]) == 4

            # 4. Dict with system and messages
            history_dict = {
                "system": "System instructions here",
                "messages": [{"role": "user", "content": "First input"}]
            }
            _, stdin4 = command(rt, "Second input", "m1", conversation=history_dict)
            body4 = json.loads(stdin4)
            self.assertEqual(body4["messages"][0], {"role": "system", "content": "System instructions here"})
            self.assertEqual(body4["messages"][1], {"role": "user", "content": "First input"})
            self.assertEqual(body4["messages"][2], {"role": "user", "content": "Second input"})
            assert body4["messages"][0] == {"role": "system", "content": "System instructions here"}
            assert body4["messages"][1] == {"role": "user", "content": "First input"}
            assert body4["messages"][2] == {"role": "user", "content": "Second input"}

            # 5. Empty prompt handled safely
            _, stdin5 = command(rt, "", "m1")
            body5 = json.loads(stdin5)
            self.assertEqual(body5["messages"], [{"role": "user", "content": ""}])
            assert body5["messages"] == [{"role": "user", "content": ""}]

    def test_all_local_compat_scan_single_and_multiline_json_completion(self):
        """scan(rt, ...) parses single-line and pretty-printed multiline JSON completions."""
        for rt in LOCAL_RUNTIMES:
            # Single-line JSON
            single = json.dumps({
                "id": "cmpl-single-01",
                "object": "chat.completion",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "Done: file updated."},
                    "finish_reason": "stop"
                }],
                "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}
            })
            res1 = scan(rt, [single])
            self.assertEqual(res1["result"]["status"], "SUCCESS")
            self.assertEqual(res1["result"]["response"], "Done: file updated.")
            self.assertEqual(res1["tokens"], 60)
            self.assertEqual(res1["conversation_id"], "cmpl-single-01")
            self.assertIsNone(res1["result"]["cost_usd"])
            assert res1["result"]["status"] == "SUCCESS"
            assert res1["result"]["response"] == "Done: file updated."
            assert res1["tokens"] == 60
            assert res1["conversation_id"] == "cmpl-single-01"
            assert res1["result"]["cost_usd"] is None

            # Multiline pretty-printed JSON
            multiline = """{
  "id": "cmpl-multi-02",
  "object": "chat.completion",
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "Done: all tests pass."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 200,
    "completion_tokens": 50,
    "total_tokens": 250
  }
}"""
            res2 = scan(rt, multiline.splitlines())
            self.assertEqual(res2["result"]["status"], "SUCCESS")
            self.assertEqual(res2["result"]["response"], "Done: all tests pass.")
            self.assertEqual(res2["tokens"], 250)
            self.assertEqual(res2["conversation_id"], "cmpl-multi-02")
            assert res2["result"]["status"] == "SUCCESS"
            assert res2["result"]["response"] == "Done: all tests pass."
            assert res2["tokens"] == 250
            assert res2["conversation_id"] == "cmpl-multi-02"

    def test_all_local_compat_scan_streaming_sse_chunks(self):
        """scan(rt, ...) parses SSE chunk streams terminated by [DONE] into SUCCESS."""
        chunks = [
            'data: {"id": "cmpl-stream-001", "choices": [{"delta": {"role": "assistant", "content": "Writing "}}]}',
            '',
            'data: {"id": "cmpl-stream-001", "choices": [{"delta": {"content": "code..."}}]}',
            '',
            'data: {"id": "cmpl-stream-001", "choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"total_tokens": 300, "cached_tokens": 50}}',
            '',
            'data: [DONE]'
        ]
        for rt in LOCAL_RUNTIMES:
            res = scan(rt, chunks)
            self.assertEqual(res["result"]["status"], "SUCCESS")
            self.assertEqual(res["result"]["response"], "Writing code...")
            self.assertEqual(res["tokens"], 250)
            self.assertEqual(res["conversation_id"], "cmpl-stream-001")
            self.assertIsNone(res["result"]["cost_usd"])
            assert res["result"]["status"] == "SUCCESS"
            assert res["result"]["response"] == "Writing code..."
            assert res["tokens"] == 250
            assert res["conversation_id"] == "cmpl-stream-001"
            assert res["result"]["cost_usd"] is None

    def test_all_local_compat_scan_tool_usage_counting(self):
        """scan(rt, ...) counts tool calls from tool_calls lists, function_call, and SEARCH diffs."""
        events = [
            # Message with tool_calls array
            json.dumps({
                "choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "t1"}, {"id": "t2"}]}}]
            }),
            # Message with deprecated function_call
            json.dumps({
                "choices": [{"message": {"role": "assistant", "function_call": {"name": "execute"}}}]
            }),
            # Content with Aider-style SEARCH/REPLACE diff marker
            json.dumps({
                "choices": [{"message": {"role": "assistant", "content": "<<<<<<< SEARCH\nfoo\n=======\nbar\n>>>>>>> REPLACE"}}]
            }),
            # Event with type=tool_use
            json.dumps({"type": "tool_use", "name": "bash"}),
            # Top-level tool_calls array
            json.dumps({"tool_calls": [{"id": "t3"}]}),
            # Final event
            json.dumps({
                "object": "chat.completion",
                "choices": [{"finish_reason": "stop", "message": {"content": "Finished"}}]
            })
        ]
        for rt in LOCAL_RUNTIMES:
            res = scan(rt, events)
            # Expect: 2 (tool_calls) + 1 (function_call) + 1 (SEARCH) + 1 (type: tool_use) + 1 (tool_calls) = 6 tools
            self.assertEqual(res["tools"], 6, f"{rt} expected 6 tools, got {res['tools']}")
            self.assertEqual(res["result"]["status"], "SUCCESS")
            assert res["tools"] == 6
            assert res["result"]["status"] == "SUCCESS"

    def test_all_local_compat_scan_token_calculation_and_caching(self):
        """scan(rt, ...) calculates tokens subtracting cached tokens, with bounds and fallbacks."""
        for rt in LOCAL_RUNTIMES:
            # 1. Total tokens with cached tokens
            ev1 = [json.dumps({"object": "chat.completion", "usage": {"total_tokens": 1500, "cached_tokens": 300}, "choices": [{"finish_reason": "stop"}]})]
            self.assertEqual(scan(rt, ev1)["tokens"], 1200)
            assert scan(rt, ev1)["tokens"] == 1200

            # 2. Cached tokens > total tokens clamped to 0
            ev2 = [json.dumps({"object": "chat.completion", "usage": {"total_tokens": 100, "cached_tokens": 150}, "choices": [{"finish_reason": "stop"}]})]
            self.assertEqual(scan(rt, ev2)["tokens"], 0)
            assert scan(rt, ev2)["tokens"] == 0

            # 3. Missing total_tokens falls back to prompt + completion - cached
            ev3 = [json.dumps({"object": "chat.completion", "usage": {"prompt_tokens": 800, "completion_tokens": 200, "cached_prompt_tokens": 100}, "choices": [{"finish_reason": "stop"}]})]
            self.assertEqual(scan(rt, ev3)["tokens"], 900)
            assert scan(rt, ev3)["tokens"] == 900

            # 4. Usage as raw integer
            ev4 = [json.dumps({"object": "chat.completion", "usage": 450, "choices": [{"finish_reason": "stop"}]})]
            self.assertEqual(scan(rt, ev4)["tokens"], 450)
            assert scan(rt, ev4)["tokens"] == 450

            # 5. Missing usage leaves tokens as None
            ev5 = [json.dumps({"object": "chat.completion", "choices": [{"finish_reason": "stop"}]})]
            self.assertIsNone(scan(rt, ev5)["tokens"])
            assert scan(rt, ev5)["tokens"] is None

    def test_all_local_compat_scan_error_handling(self):
        """scan(rt, ...) parses OpenAI API error payloads into ERROR result status."""
        error_payload = json.dumps({
            "error": {
                "message": "Model 'mistral:latest' not found on Ollama server",
                "type": "model_not_found_error",
                "code": 404
            }
        })
        for rt in LOCAL_RUNTIMES:
            res = scan(rt, [error_payload])
            self.assertIsNotNone(res["result"])
            self.assertEqual(res["result"]["status"], "ERROR")
            self.assertIn("Model 'mistral:latest' not found", res["result"]["error"])
            assert res["result"] is not None
            assert res["result"]["status"] == "ERROR"
            assert "Model 'mistral:latest' not found" in res["result"]["error"]

    def test_all_local_compat_scan_malformed_lines_and_empty_stream(self):
        """scan(rt, ...) ignores malformed JSON, curl progress, and empty streams safely."""
        malformed = [
            None,
            "",
            "   ",
            "curl: (7) Failed to connect to 127.0.0.1 port 11434",
            "  % Total    % Received % Xferd  Average Speed   Time    Time     Time  Current",
            "100   240  100   120  100   120    500    500 --:--:-- --:--:-- --:--:--   500",
            "<html><body>502 Bad Gateway</body></html>",
            "{unclosed json: 123",
            "[1, 2, 3]",
            '"just a string"'
        ]
        for rt in LOCAL_RUNTIMES:
            # 1. Malformed stream without completion
            res1 = scan(rt, malformed)
            self.assertIsNone(res1["tokens"])
            self.assertEqual(res1["tools"], 0)
            self.assertIsNone(res1["conversation_id"])
            self.assertIsNone(res1["result"])
            assert res1["tokens"] is None
            assert res1["tools"] == 0
            assert res1["conversation_id"] is None
            assert res1["result"] is None

            # 2. Empty stream
            res2 = scan(rt, [])
            self.assertIsNone(res2["tokens"])
            self.assertIsNone(res2["result"])
            assert res2["tokens"] is None
            assert res2["result"] is None

            # 3. None stream
            res3 = scan(rt, None)
            self.assertIsNone(res3["tokens"])
            self.assertIsNone(res3["result"])
            assert res3["tokens"] is None
            assert res3["result"] is None

    def test_all_local_compat_scan_response_truncation_and_unicode(self):
        """scan(rt, ...) preserves unicode characters and truncates responses to the last 4000 characters."""
        long_text = "A" * 5000 + " 🚀 成功完成任务"
        completion = json.dumps({
            "object": "chat.completion",
            "choices": [{"finish_reason": "stop", "message": {"content": long_text}}]
        })
        for rt in LOCAL_RUNTIMES:
            res = scan(rt, [completion])
            self.assertEqual(res["result"]["status"], "SUCCESS")
            resp = res["result"]["response"]
            self.assertEqual(len(resp), 4000)
            self.assertTrue(resp.endswith(" 🚀 成功完成任务"))
            assert res["result"]["status"] == "SUCCESS"
            assert len(resp) == 4000
            assert resp.endswith(" 🚀 成功完成任务")

    def test_all_local_compat_scan_cost_usd_pricing(self):
        """scan(rt, ...) keeps cost_usd None by default and computes formula when prices string is passed."""
        event = json.dumps({
            "object": "chat.completion",
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "cached_tokens": 200},
            "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]
        })
        for rt in LOCAL_RUNTIMES:
            # Without prices -> None
            res_no_price = scan(rt, [event])
            self.assertIsNone(res_no_price["result"]["cost_usd"])
            assert res_no_price["result"]["cost_usd"] is None

            # With prices="3.0,0.5,15.0" (input $3/M, cache $0.5/M, output $15/M)
            # cost = (1000 - 200)*3.0 + 200*0.5 + 500*15.0 = 2400 + 100 + 7500 = 10000 micro-USD = $0.010000
            res_priced = scan(rt, [event], prices="3.0,0.5,15.0")
            self.assertEqual(res_priced["result"]["cost_usd"], 0.01)
            assert res_priced["result"]["cost_usd"] == 0.01

    def test_all_local_compat_scan_file_helper(self):
        """scan_file(rt, path) parses files from disk and returns default state for missing files."""
        for rt in LOCAL_RUNTIMES:
            with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as tf:
                tf.write(json.dumps({
                    "id": f"cmpl-file-{rt}",
                    "object": "chat.completion",
                    "choices": [{"finish_reason": "stop", "message": {"content": f"Result for {rt}"}}],
                    "usage": {"total_tokens": 42}
                }) + "\n")
                tf.flush()
                res = scan_file(rt, tf.name)
                self.assertEqual(res["conversation_id"], f"cmpl-file-{rt}")
                self.assertEqual(res["tokens"], 42)
                self.assertEqual(res["result"]["status"], "SUCCESS")
                self.assertEqual(res["result"]["response"], f"Result for {rt}")
                assert res["conversation_id"] == f"cmpl-file-{rt}"
                assert res["tokens"] == 42
                assert res["result"]["status"] == "SUCCESS"
                assert res["result"]["response"] == f"Result for {rt}"

            # Missing file returns graceful default
            missing = scan_file(rt, "/path/does/not/exist.json")
            self.assertIsNone(missing["tokens"])
            self.assertIsNone(missing["result"])
            assert missing["tokens"] is None
            assert missing["result"] is None

    # -------------------------------------------------------------------------
    # Live fixture tests against fake-local-compat-server.js (requires node)
    # -------------------------------------------------------------------------
    @unittest.skipUnless(NODE_AVAILABLE, "node not available to run fake-local-compat-server.js")
    def test_live_fixture_round_trip_all_four_runtimes(self):
        """command() drives stub binary against fake-local-compat-server.js and scan() parses round trip for all 4 runtimes."""
        if not self.server_port:
            self.skipTest("fake-local-compat-server.js is not running")

        endpoint = f"http://127.0.0.1:{self.server_port}/v1/chat/completions"
        for rt in LOCAL_RUNTIMES:
            prompt = f"Implement integration test for {rt}"
            argv, stdin = command(rt, prompt, "local-test-model", endpoint_url=endpoint)
            proc = subprocess.run(argv, input=stdin, text=True, capture_output=True)
            self.assertEqual(proc.returncode, 0, f"curl failed for {rt}: {proc.stderr}")
            assert proc.returncode == 0

            res = scan(rt, proc.stdout.splitlines())
            self.assertIsNotNone(res["result"], f"{rt} result should not be None")
            self.assertEqual(res["result"]["status"], "SUCCESS")
            self.assertEqual(res["result"]["response"], "Task completed successfully.")
            self.assertEqual(res["tokens"], 1240)
            self.assertEqual(res["conversation_id"], "chatcmpl-fake-local-001")
            self.assertIsNone(res["result"]["cost_usd"])
            assert res["result"] is not None
            assert res["result"]["status"] == "SUCCESS"
            assert res["result"]["response"] == "Task completed successfully."
            assert res["tokens"] == 1240
            assert res["conversation_id"] == "chatcmpl-fake-local-001"
            assert res["result"]["cost_usd"] is None

    @unittest.skipUnless(NODE_AVAILABLE, "node not available to run fake-local-compat-server.js")
    def test_live_fixture_error_round_trip(self):
        """Error response from fake-local-compat-server.js parses into status ERROR."""
        if not self.server_port:
            self.skipTest("fake-local-compat-server.js is not running")

        endpoint = f"http://127.0.0.1:{self.server_port}/error"
        for rt in LOCAL_RUNTIMES:
            argv, stdin = command(rt, "Trigger error test", "local-test-model", endpoint_url=endpoint)
            proc = subprocess.run(argv, input=stdin, text=True, capture_output=True)
            res = scan(rt, proc.stdout.splitlines())
            self.assertIsNotNone(res["result"])
            self.assertEqual(res["result"]["status"], "ERROR")
            self.assertIn("simulated local compat server error", res["result"]["error"])
            assert res["result"] is not None
            assert res["result"]["status"] == "ERROR"
            assert "simulated local compat server error" in res["result"]["error"]

    @unittest.skipUnless(NODE_AVAILABLE, "node not available to run fake-local-compat-server.js")
    def test_live_fixture_stream_round_trip(self):
        """Streaming request with stream=True against fake server parses chunks into SUCCESS."""
        if not self.server_port:
            self.skipTest("fake-local-compat-server.js is not running")

        url = f"http://127.0.0.1:{self.server_port}/v1/chat/completions"
        req_body = json.dumps({"model": "test-stream", "messages": [{"role": "user", "content": "hi"}], "stream": True}).encode("utf-8")
        req = urllib.request.Request(url, data=req_body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            lines = [line.decode("utf-8") for line in resp]

        for rt in LOCAL_RUNTIMES:
            res = scan(rt, lines)
            self.assertEqual(res["result"]["status"], "SUCCESS")
            self.assertEqual(res["result"]["response"], "Task completed successfully.")
            self.assertEqual(res["tokens"], 1240)
            assert res["result"]["status"] == "SUCCESS"
            assert res["result"]["response"] == "Task completed successfully."
            assert res["tokens"] == 1240

    # -------------------------------------------------------------------------
    # Dedicated edge cases per runtime (fixture error response shapes)
    # -------------------------------------------------------------------------

    def test_ollama_error_edge_case(self):
        """scan('ollama', ...) handles fixture error response shape without crashing."""
        err_payload = {
            "error": {
                "message": "model 'ollama:latest' not found, try pulling it first",
                "type": "invalid_request_error",
                "code": "simulated_error"
            }
        }
        res = scan("ollama", [json.dumps(err_payload)])
        assert res["result"] is not None
        assert res["result"]["status"] == "ERROR"
        assert "model 'ollama:latest' not found" in res["result"]["error"]

        # Assert command with error endpoint builds valid argv without crashing
        argv, stdin = command("ollama", "task", "ollama-model", endpoint_url="http://127.0.0.1:11434/error/chat/completions")
        assert argv[0] == "curl"
        assert argv[4] == "http://127.0.0.1:11434/error/chat/completions"
        assert json.loads(stdin)["model"] == "ollama-model"

    def test_vllm_error_edge_case(self):
        """scan('vllm', ...) handles fixture error response shape without crashing."""
        err_payload = {
            "error": {
                "message": "vLLM CUDA out of memory error during generation",
                "type": "invalid_request_error",
                "code": "simulated_error"
            }
        }
        res = scan("vllm", [json.dumps(err_payload)])
        assert res["result"] is not None
        assert res["result"]["status"] == "ERROR"
        assert "vLLM CUDA out of memory" in res["result"]["error"]

        # Assert command with error endpoint builds valid argv without crashing
        argv, stdin = command("vllm", "task", "vllm-model", endpoint_url="http://127.0.0.1:8000/error/chat/completions")
        assert argv[0] == "curl"
        assert argv[4] == "http://127.0.0.1:8000/error/chat/completions"
        assert json.loads(stdin)["model"] == "vllm-model"

    def test_lmstudio_error_edge_case(self):
        """scan('lmstudio', ...) handles fixture error response shape without crashing."""
        err_payload = {
            "error": {
                "message": "LM Studio context length exceeded maximum allowed tokens",
                "type": "invalid_request_error",
                "code": "simulated_error"
            }
        }
        res = scan("lmstudio", [json.dumps(err_payload)])
        assert res["result"] is not None
        assert res["result"]["status"] == "ERROR"
        assert "context length exceeded" in res["result"]["error"]

        # Assert command with error endpoint builds valid argv without crashing
        argv, stdin = command("lmstudio", "task", "lmstudio-model", endpoint_url="http://127.0.0.1:1234/error/chat/completions")
        assert argv[0] == "curl"
        assert argv[4] == "http://127.0.0.1:1234/error/chat/completions"
        assert json.loads(stdin)["model"] == "lmstudio-model"

    def test_llamacpp_error_edge_case(self):
        """scan('llamacpp', ...) handles fixture error response shape without crashing."""
        err_payload = {
            "error": {
                "message": "llama.cpp slot processing failed: internal server error",
                "type": "invalid_request_error",
                "code": "simulated_error"
            }
        }
        res = scan("llamacpp", [json.dumps(err_payload)])
        assert res["result"] is not None
        assert res["result"]["status"] == "ERROR"
        assert "slot processing failed" in res["result"]["error"]

        # Assert command with error endpoint builds valid argv without crashing
        argv, stdin = command("llamacpp", "task", "llamacpp-model", endpoint_url="http://127.0.0.1:8080/error/chat/completions")
        assert argv[0] == "curl"
        assert argv[4] == "http://127.0.0.1:8080/error/chat/completions"
        assert json.loads(stdin)["model"] == "llamacpp-model"

    def test_all_local_compat_scan_choice_error_finish_reason(self):
        """scan(rt, ...) flags choice with finish_reason='error' as ERROR status."""
        for rt in LOCAL_RUNTIMES:
            payload = {
                "choices": [{"finish_reason": "error", "error": "Model generation aborted due to safety filter"}]
            }
            res = scan(rt, [json.dumps(payload)])
            assert res["result"] is not None
            assert res["result"]["status"] == "ERROR"
            assert "safety filter" in res["result"]["error"]

    def test_all_local_compat_scan_status_failed_event(self):
        """scan(rt, ...) flags event with type='step_failed' as ERROR status."""
        for rt in LOCAL_RUNTIMES:
            payload = {
                "type": "step_failed",
                "message": "Execution aborted by client timeout"
            }
            res = scan(rt, [json.dumps(payload)])
            assert res["result"] is not None
            assert res["result"]["status"] == "ERROR"
            assert "Execution aborted" in res["result"]["error"]


if __name__ == "__main__":
    unittest.main()
