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
## egress/worker sandbox

### Threat Model
- **Input sources**: Container environment variables (`EGRESS_MODE`, `FORGEJO_USER`, `FORGE_REPO`, `WORKER_USER`, `PROXY_USER`), container command arguments (`"$@"`), host volume mounts (`/home/worker`, `/run/secrets/*`, `/etc/squid/*`).
- **Sink / Reach**: Container entrypoint execution, shell variable expansions, Git commands (`git clone`, `git config`), Squid proxy daemon configuration and listening socket (port 3128), agent process execution.
- **Sensitive data**: Forgejo tokens (`/run/secrets/forgejo-token`), provider API keys (`/run/secrets/api-key`), keyring password (`/run/secrets/keyring`), Git repository source code and commit history in `$HOME/playground`, network egress allowlists.

### Findings

#### Finding 1: Unvalidated Environment Variables and Argument Injection in `worker/entrypoint.sh`
- **Severity**: HIGH
- **Location**: `worker/entrypoint.sh:FORGEJO_USER`, `FORGE_REPO`
- **Abuse Scenario**: `FORGEJO_USER` and `FORGE_REPO` were read directly from environment variables without allow-pattern validation and interpolated into `git clone` and `git config` commands. An attacker, malicious prompt, or rogue project configuration setting `FORGEJO_USER="worker-1\n[credential]\nhelper=evil"` could inject arbitrary git config directives into `$HOME/playground/.git/config`. A crafted `FORGE_REPO` starting with `-` (such as `--upload-pack=evil`) or containing command separators could inject git command-line options. In addition, directory traversal sequences like `../../etc` could cause repository operations outside intended paths.
- **Fix**: Validated `FORGEJO_USER` against `^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$` and `FORGE_REPO` against `^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}/[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$`, rejecting any occurrence of `..`. The script fails closed with exit code 1 immediately if validation fails before any Git command is executed. Ensured all shell variable expansions (`"$FORGEJO_USER"`, `"$FORGE_REPO"`, `"$HOME"`, `"$@"`) are quoted.

#### Finding 2: Unvalidated Mode Pattern and Path Traversal in `egress/entrypoint.sh`
- **Severity**: MEDIUM
- **Location**: `egress/entrypoint.sh:MODE`, `CONF`
- **Abuse Scenario**: `MODE="${EGRESS_MODE:-enforce}"` was interpolated into `CONF="/etc/squid/squid.${MODE}.conf"` relying only on file existence (`[ -f "$CONF" ]`). An attacker supplying an arbitrary mode string or path traversal sequence could attempt to load unauthorized Squid configurations or trigger unexpected startup failures.
- **Fix**: Validated `MODE` strictly against the allow-pattern `^(enforce|discovery)$`, defaulting to `enforce` if unset. Enforced strict quoting on all expansions (`"$MODE"`, `"$CONF"`). Supported `SQUID_CONF_DIR` for hermetic testing.

#### Finding 3: Startup Window and Default-Allow Analysis in `egress/entrypoint.sh`
- **Severity**: LOW (Audited & Confirmed Safe)
- **Location**: `egress/entrypoint.sh`, `egress/squid.enforce.conf`
- **Abuse Scenario**: If Squid were started in the background with a permissive default configuration before applying the enforce configuration, or if port 3128 began accepting connections before enforce ACL rules were loaded, a worker racing the container startup could exfiltrate data through unapproved destinations during that startup window.
- **Fix / Status**: Audited Squid startup behavior and confirmed safe by construction. `egress/entrypoint.sh` defaults strictly to `MODE="enforce"`. The entrypoint validates configuration syntax via `squid -k parse -f "$CONF"` prior to launching the daemon, and executes `exec squid -N -f "$CONF"`. Squid does not bind or listen on port 3128 until its configuration file is completely parsed and ACL rules are compiled into memory. Because `squid.enforce.conf` ends with `http_access deny all` and port binding occurs only post-parsing, there is zero time window where default-allow applies to worker connections.

#### Finding 4: Overly Permissive File Permissions on Worker Files and Git Configuration
- **Severity**: MEDIUM
- **Location**: `worker/entrypoint.sh`, `egress/entrypoint.sh`
- **Abuse Scenario**: `worker/entrypoint.sh` ran with ambient process umask (typically `0022`), creating `$HOME/playground/.git/config`, cloned source files, and workspace directories with world-readable permissions (`0644` for files, `0755` for directories). If credentials, tokens, or private repository files were stored in the workspace, any other process or user in the container or volume mount could read them. Similarly, `egress/entrypoint.sh` lacked a restrictive umask for generated runtime PID or log files.
- **Fix**: Added `umask 077` at entrypoint initialization in `worker/entrypoint.sh`, ensuring all files and directories created by git clone and git config have permissions `0600` and `0700` (no group or world access). Explicitly applied `chmod 700 "$HOME/playground"` and `chmod -R go-rwx "$HOME/playground/.git"` to lock down pre-existing volume mounts. Set `umask 027` in `egress/entrypoint.sh` to ensure PID, cache, and log files are never world-readable or world-writable.

#### Finding 5: Potential Execution as Root in Container Entrypoints
- **Severity**: MEDIUM
- **Location**: `worker/entrypoint.sh`, `egress/entrypoint.sh`
- **Abuse Scenario**: Although container images specify `USER worker` and `USER proxy`, if a container is started with a root override (`--user root` or orchestration environments defaulting to root), the entrypoints previously executed `exec "$@"` as root. Running the AI agent process or proxy as root would allow a compromised model or malicious tool call to modify container binaries, tamper with system configuration, or attempt container breakout.
- **Fix**: Added explicit root detection (`[ "$(id -u)" -eq 0 ]`) to both entrypoint scripts. If invoked as root, the entrypoints drop privileges to the unprivileged user (`worker` for worker container, `proxy` for egress proxy) via `runuser` or `su`. If unable to switch to the non-root user, the entrypoints fail closed (`exit 1`), refusing to execute the agent process or proxy daemon as root.
## web/server.py

### Finding 1: DNS Rebinding / Host Header Validation Bypassed on `/healthz`
- **Severity**: HIGH
- **Location**: `web/server.py:Handler.do_GET`
- **Abuse Scenario**: The `/healthz` endpoint dispatched and responded with `200 OK` before invoking `_allowed()`, bypassing `host_allowed()`. A malicious web page visited by a user on the LAN could use DNS rebinding (an attacker-controlled domain with a short TTL resolving to `127.0.0.1`) to send requests to `http://attacker.example:8171/healthz`. Because the Host header was unvalidated on this path, the attacker could detect and fingerprint the running Agent-Forge instance on localhost.
- **Fix**: Check `host_allowed(self.headers.get("Host"))` at the very start of `do_GET` (and `do_HEAD`) before evaluating `/healthz` or other paths, immediately returning `403 Forbidden` for any disallowed host.

### Finding 2: Host Header Port Injection and IPv6 Formatting Bypass in `host_allowed`
- **Severity**: HIGH
- **Location**: `web/server.py:host_allowed`
- **Abuse Scenario**: `host_allowed` stripped ports using `host.rsplit(":", 1)[0]` and `host.split("]")[0] + "]"` without validating that the trailing portion was a numeric port. An attacker could craft a Host header such as `127.0.0.1:evil.com`, `localhost:evil.com`, `[::1]:evil.com`, or `[::1]evil.com`; the function stripped the suffix and matched the prefix against allowed hosts, bypassing Host filtering. Furthermore, `.ts.net` matched without requiring a subdomain prefix.
- **Fix**: Strictly validate that if a colon is present in the host (or following a closing bracket in IPv6), the port portion consists solely of decimal digits. Reject malformed bracketed IPv6 strings and require `.ts.net` hostnames to have a valid non-empty subdomain.

### Finding 3: Missing Origin and Referer CSRF Validation on State-Changing POST Requests
- **Severity**: HIGH
- **Location**: `web/server.py:Handler._allowed`
- **Abuse Scenario**: Although `X-Agent-Forge: 1` is enforced for POST requests, the server failed to validate `Origin` or `Referer` headers. In scenarios where custom headers can be attached or sent across origins (e.g. browser extensions, misconfigured proxies, sandboxed pages, or CORS preflight bypasses), an attacker site could issue state-changing POST requests (such as task actions, killswitch triggers, settings changes, or egress modifications).
- **Fix**: In `Handler._allowed(post=True)`, verify that if `Origin` is present or `Referer` is present, their parsed hostname/netloc satisfies `host_allowed()`. Reject any request with `403 Forbidden` if an untrusted Origin or Referer is supplied.

### Finding 4: Dashboard Token Constant-Time Comparison
- **Severity**: LOW
- **Location**: `web/server.py:token_ok`
- **Abuse Scenario**: If authentication tokens were compared using `==` or `in`, character-by-character early termination would introduce a timing side-channel, allowing an attacker to deduce the `DASHBOARD_TOKEN` byte by byte over repeated requests.
- **Fix / Status**: Verified that `token_ok()` compares both query tokens and `af_token` cookie values using `hmac.compare_digest`. Hardened token parsing to strip surrounding whitespace and quotes, and moved `hmac` import to module top-level.

### Finding 5: Potential Token Leakage in Error Responses and Payloads
- **Severity**: LOW
- **Location**: `web/server.py:Handler._json`, `Handler._deny`, `Handler._raw`, `as_json`
- **Abuse Scenario**: If a backend command failed or returned an error traceback containing environment variables or command invocations with the token, raw error strings in JSON bodies or deny messages could disclose `DASHBOARD_TOKEN` to the client or log files.
- **Fix**: Keep request logging silenced (`log_message` is a no-op). Automatically redact occurrences of `DASHBOARD_TOKEN` with `[REDACTED]` in `as_json`, `Handler._json`, `Handler._deny`, and `Handler._raw` before transmitting responses.

### Finding 6: Regex-Validated Identifiers Input Handling
- **Severity**: LOW (No exploitable issue found)
- **Location**: `web/server.py:TASK_ID`, `GOAL_ID`, `DOC_NAME`, `HOST`, `TARGET`
- **Abuse Scenario**: If untrusted input identifiers were concatenated into shell commands or filesystem paths prior to validation, an attacker could achieve arbitrary command execution or path traversal.
- **Fix / Status**: No exploitable issue found. All identifiers (`TASK_ID`, `GOAL_ID`, `DOC_NAME`, `HOST`, `TARGET`, along with project, action, worker, and duration arguments) are strictly validated against anchored regular expressions (`.fullmatch()`) or explicit whitelists before concatenation. Dynamic command arguments use `shlex.quote()`, and owner content is transmitted via standard input (`inp`).
