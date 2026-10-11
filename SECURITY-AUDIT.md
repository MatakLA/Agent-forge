# Security Audit

This document records security audit findings, threat modeling, abuse scenarios, and fixes across Agent-Forge components.

## web/dashboard/backend.py

### Threat Model
- **Input sources**: Dashboard owner-facing write actions and background jobs (`/api/projects`, `/api/workers`, `/api/upstream`, `/api/login/terminal`), environment configuration (`VM_IP`, `VM_USER`, `VM_KEY`, `VM_KNOWN_HOSTS`, `FORGE_DIR`).
- **Sink / Reach**: Local shell execution via `bash -c` or remote execution over SSH (`ssh_run`, `ssh_run_full`), container execution via `docker exec`, local PTY session via `pty.openpty()` / `os.write`.
- **Sensitive data**: Authentication tokens (Forgejo admin/orchestrator tokens, upstream GitHub/GitLab tokens, API keys, keyring password), private SSH key (`VM_KEY`), and container volumes.

### Findings

#### Finding 1: Command Injection via Unvalidated Job Arguments in `create_project_job`
- **Severity**: High
- **Location**: `web/dashboard/backend.py:create_project_job`
- **Abuse Scenario**: `create_project_job` constructed shell command strings for `bin/foreman init-project`, `FORGE_REPO=... scripts/forge-bootstrap`, and `sudo -n scripts/install-services --project ...` by directly interpolating `name`, `repo`, `workers_`, and single-quoted `lang`. An attacker supplying a crafted project name (`proj; touch /tmp/pwn`), repo (`org/repo; id`), worker list (`["worker-1; reboot"]`), or language (`English'; reboot #`) could achieve arbitrary shell command execution with the privileges of the Agent-Forge process or root (via sudo).
- **Fix**: Validated `name` with `PROJECT_RE` (disallowing `'default'`), `repo` with `REPO_RE` (`[A-Za-z0-9_.-]{1,40}/[A-Za-z0-9_.-]{1,60}`), each worker with `WORKER_RE` (`worker-\d+`), and `lang` with `LANG_RE` (`[A-Za-z][A-Za-z ()-]{0,30}`). Safely escaped all interpolated variables in shell strings using `shlex.quote`. Product vision text is strictly passed over stdin (`inp=vision` to `--vision -`), never in the command line.

#### Finding 2: Command Injection via Unvalidated Runtime in `add_worker_job`
- **Severity**: High
- **Location**: `web/dashboard/backend.py:add_worker_job`
- **Abuse Scenario**: `add_worker_job` formatted `f" --runtime {runtime}"` directly into the shell string for `bin/worker-add {n}` without verifying the runtime against the supported set. An attacker submitting an adversarial runtime payload (e.g. `claude; touch /tmp/pwn`) could execute arbitrary commands.
- **Fix**: Validated `runtime` against `VALID_RUNTIMES = frozenset({"agy", "claude", "codex", "gemini", "compat", "aider"})` before formatting, escaping the parameter with `shlex.quote` and failing the job immediately on invalid input.

#### Finding 3: Command Injection via Unvalidated Project Flag in `connect_upstream_job`
- **Severity**: High
- **Location**: `web/dashboard/backend.py:connect_upstream_job`
- **Abuse Scenario**: `connect_upstream_job` accepted a project flag argument `pf` and interpolated it raw into remote command lines (`bin/foreman{pf} settings apply`, `bin/upstream{pf} check`, `bin/upstream{pf} import`). If a crafted flag containing shell metacharacters was passed (e.g. `; reboot;`), it was executed by the shell.
- **Fix**: Validated `pf` to ensure it is either empty or matches `--project NAME` with `NAME` matching `PROJECT_RE` (or validated via `pflag`). Sensitive configuration `changes` are passed strictly via stdin (`inp=json.dumps(changes)`), keeping tokens out of argv.

#### Finding 4: Shell Breakout via PTY Control Sequences and Unvalidated Worker in `LoginSessions`
- **Severity**: Medium
- **Location**: `web/dashboard/backend.py:LoginSessions` (`start`, `stop`, `read`, `send`)
- **Abuse Scenario**:
  1. `start` and `stop` interpolated worker name `w` directly into `docker exec` command strings executed over SSH or subprocess without validating against `worker-\d+`.
  2. `send(w, text)` wrote owner-supplied `text + "\r"` directly to the terminal PTY. An attacker could embed control characters such as `\x03` (SIGINT/Ctrl-C), `\x04` (EOF), `\x1a` (suspend), `\x1b` (ANSI escape sequences), or embedded newlines (`\n`, `\r`) to terminate the foreground `agy` process and execute secondary shell commands within the interactive container shell.
- **Fix**: Enforced `WORKER_RE.fullmatch(w)` on all `LoginSessions` methods (`start`, `stop`, `read`, `send`). In `send(w, text)`, strictly rejected all control characters matching `[\x00-\x1f\x7f-\x9f]` (including newlines and terminal escape controls) and enforced length bounds (<= 4096), ensuring only clean, single-line auth tokens reach the PTY.

#### Finding 5: Parameter Validation and Shell Safety in `pflag`, `poll_project`, and `ssh_run` / `ssh_run_full`
- **Severity**: Low
- **Location**: `web/dashboard/backend.py:pflag`, `poll_project`, `ssh_run`, `ssh_run_full`
- **Abuse Scenario**:
  1. `pflag` could raise unexpected type exceptions on non-string inputs.
  2. `poll_project` did not catch validation exceptions from `pflag(name)`, which could disrupt polling state.
  3. `ssh_run` and `ssh_run_full` accepted arbitrary objects without validating string type for `remote_cmd`.
  4. Potential exposure of secrets via command line arguments (`/proc/*/cmdline`).
- **Fix**: Enforced strict type checks (`isinstance(str)`) across all functions. Ensured `pflag` rejects non-strings and any project name failing `PROJECT_RE.fullmatch`. Handled `pflag` errors gracefully in `poll_project` without calling `ssh_run`. Confirmed that no secret or token is ever formatted into argv elements or remote command strings; all sensitive data (settings JSON, secret values, vision text) is piped exclusively over stdin (`inp=...`).
