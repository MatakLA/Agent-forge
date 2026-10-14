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

### Finding 2: Host Header Port Injection, Empty Port String, and IPv6 Formatting Bypass in `host_allowed`
- **Severity**: HIGH
- **Location**: `web/server.py:host_allowed`
- **Abuse Scenario**: `host_allowed` stripped ports using `host.rsplit(":", 1)[0]` and `host.split("]")[0] + "]"` without validating that the trailing portion was a non-empty numeric port. An attacker could craft a Host header such as `127.0.0.1:evil.com`, `localhost:evil.com`, `[::1]:evil.com`, or `[::1]evil.com`; the function stripped the suffix and matched the prefix against allowed hosts, bypassing Host filtering. Furthermore, empty port strings like `127.0.0.1:` or `localhost:`, or malformed IPv6 brackets, could cause unexpected behavior.
- **Fix**: Strictly validate that if a colon is present in the host (or following a closing bracket in IPv6), the port portion consists solely of decimal digits and is non-empty (`not port or not port.isdigit()`). Reject malformed bracketed IPv6 strings and require `.ts.net` hostnames to have a valid non-empty subdomain.

### Finding 3: Missing Origin and Referer CSRF Validation on State-Changing POST Requests
- **Severity**: HIGH
- **Location**: `web/server.py:Handler._allowed`
- **Abuse Scenario**: Although `X-Agent-Forge: 1` is enforced for POST requests, the server failed to validate `Origin` or `Referer` headers. In scenarios where custom headers can be attached or sent across origins (e.g. browser extensions, misconfigured proxies, sandboxed pages, or CORS preflight bypasses), an attacker site could issue state-changing POST requests (such as task actions, killswitch triggers, settings changes, or egress modifications). Attackers could also send `Origin: null` from sandboxed iframes or data URLs to attempt CSRF bypasses.
- **Fix**: In `Handler._allowed(post=True)`, verify that if `Origin` is present or `Referer` is present, their parsed hostname/netloc satisfies `host_allowed()`. Explicitly reject untrusted values including `null` and empty strings with `403 Forbidden`.

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

## bin/lib/foreman/knowledge.py

### Threat Model
- **Input sources**: Document identifiers (`name`) and role names (`role`) supplied via web dashboard endpoints (proxied through `web/server.py`), CLI arguments, or internal foreman loop callers (`prompts.py`, `doc_list`, `doc_get`, `doc_set`, `doc_reset`, `role_playbook`).
- **Sink / Reach**: Local filesystem path resolution, read, write, and deletion operations within `ctx.CFG_DIR` (`etc/`, `etc/roles/`), `ctx.ROOT` (`roles/`), and project roots (`docs/vision.md`). No database, network sockets, or shell execution is reached directly.
- **Sensitive data**: Arbitrary host files outside the project configuration directories (e.g. `/etc/passwd`, private keys, secret tokens under `~/.config/sandbox/secrets/`, source code files).

### Findings

#### Finding 1: Path Traversal Bypass in `role_playbook` via Relative/Absolute Paths
- **Severity**: HIGH
- **Location**: `bin/lib/foreman/knowledge.py:role_playbook`
- **Abuse Scenario**: `role_playbook(role)` accepted arbitrary role strings, transformed them via `slugify(role)`, and performed word-intersection matching against available roles. An input containing path traversal sequences or directory paths targeting existing roles (such as `../backend-developer`, `../../../roles/backend-developer`, `/backend-developer`, or `backend-developer/../../etc/passwd`) was normalized into tokens matching legitimate role playbooks (e.g. matching `backend-developer`), bypassing input sanitization. If `role_playbook` or its callers were extended to load roles directly from disk using the unsanitized or slugified role name, arbitrary file paths could be reached.
- **Fix**: Validated the `role` parameter at entry in `role_playbook`: rejected non-strings, empty inputs, strings containing `..`, `/`, `\\`, or absolute paths (`os.path.isabs`). Canonicalized `role:<slug>` prefixes by stripping `role:`, slugified the remaining identifier, and strictly enforced that the resulting slug matches `ROLE_SLUG.fullmatch(slug)`. Returns `""` immediately for any invalid or traversal input.

#### Finding 2: Missing Path Traversal and Non-String Type Guards in `doc_path`
- **Severity**: HIGH
- **Location**: `bin/lib/foreman/knowledge.py:doc_path`
- **Abuse Scenario**: `doc_path(name, write=False)` accepted arbitrary document identifiers. If called with a non-string object (such as `None`), it raised unhandled `AttributeError` on `.startswith()`. Furthermore, while the web dashboard currently gates HTTP requests with `DOC_NAME`, `doc_path` did not independently reject path traversal characters (`..`, `/`, `\\`) or absolute path components before calling `os.path.join` for `CFG_DIR` or `roles`, risking file system escape if the HTTP-layer regex was loosened or if `doc_path` was invoked directly by other components.
- **Fix**: Validated that `name` is a non-empty string and immediately rejected any name containing `..`, `/`, `\\`, or matching `os.path.isabs(name)`. Restricted document names strictly to the known `DOCS` dictionary keys or `role:<slug>` matching `ROLE_SLUG.fullmatch`. In addition, implemented defense-in-depth confinement verification using `os.path.realpath` and `os.path.commonpath` to guarantee that resolved file paths stay strictly inside `CFG_DIR`, `ctx.ROOT/roles`, or the project root.

#### Finding 3: Path Traversal and Unhandled Non-String Errors in `doc_reset`
- **Severity**: MEDIUM
- **Location**: `bin/lib/foreman/knowledge.py:doc_reset`
- **Abuse Scenario**: `doc_reset(name)` directly called `name.startswith("role:")` without verifying string type, causing unhandled `AttributeError` on `None` or non-string inputs. Furthermore, it constructed deletion paths using `os.path.join(ctx.CFG_DIR, "roles", name[5:] + ".md")` without validating against path traversal sequences or checking resolved path containment before calling `os.remove`.
- **Fix**: Added explicit type validation, rejected traversal characters (`..`, `/`, `\\`, `os.path.isabs`), routed path resolution through hardened `doc_path(name, write=True)`, and verified via `os.path.realpath` / `os.path.commonpath` that the target file is strictly confined within `ctx.CFG_DIR/roles` before deletion.

#### Finding 4: File Write Path Traversal Protection in `doc_set`
- **Severity**: HIGH
- **Location**: `bin/lib/foreman/knowledge.py:doc_set`
- **Abuse Scenario**: `doc_set(name, text)` creates and overwrites document files on disk (`os.replace(tmp, p)`). If `doc_path` failed to reject traversal filenames, an attacker could write arbitrary files across the host filesystem.
- **Fix / Status**: Enforced strict reliance on hardened `doc_path(name, write=True)` which returns `None` for any traversal name, causing `doc_set` to fail closed and return `"unknown document"` without creating temporary files or directories. Added type validation ensuring `text` is a string.

#### Finding 5: Defense-in-Depth Path Confinement in `doc_path` and `doc_get`
- **Severity**: LOW (Defense-in-depth)
- **Location**: `bin/lib/foreman/knowledge.py:doc_path`, `doc_get`
- **Abuse Scenario**: Even if a regular expression matches an unexpected pattern or symlinks exist within the configuration tree, unconfined path construction could allow escaping `CFG_DIR` or `roles/`.
- **Fix**: Added canonical path resolution check verifying that `os.path.commonpath([base_dir, resolved]) == base_dir` and `resolved != base_dir`, ensuring target files remain strictly within their intended directory boundaries.

## Credential Handling (`bin/secret-set`, `worker/git-credential-forgejo`, `bin/forgejo-api`)

### Threat Model
- **Input sources**: Stdin streams (`bin/secret-set`, `worker/git-credential-forgejo`), git helper invocation arguments and stdin queries (`worker/git-credential-forgejo`), CLI arguments and path/query specifications (`bin/forgejo-api`), secrets store files on disk (`~/.config/sandbox/secrets/*`, `/run/secrets/forgejo-token`).
- **Sink / Reach**: Local filesystem (`~/.config/sandbox/secrets/*`), stdout/stderr streams, process arguments (`/proc/*/cmdline`), HTTP requests to Forgejo API (`urllib.request`).
- **Sensitive data**: API keys (Anthropic, OpenAI, Gemini, Compat), upstream forge tokens (GitHub, GitLab), notification tokens (`notify.token`), Forgejo worker tokens, monitoring tokens (`monitor.token`).

### Findings

#### Finding 6: Insecure File Permissions via Pre-existing Temporary File in `bin/secret-set`
- **Severity**: Medium
- **Location**: `bin/secret-set`
- **Abuse Scenario**: `bin/secret-set` created a temporary file `p + ".tmp"` using `os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)`. If `tmp` already existed (pre-created by another local user or a previous process with loose permissions such as 0666 or 0644), `os.open` with `O_CREAT` does not modify the permissions of an existing file. Subsequent `os.replace(tmp, p)` moved the loosely-permissioned file to `p`, leaving secrets world-readable. Additionally, pre-existing symlinks at `tmp` could lead to symlink traversal overwrites.
- **Fix**: Explicitly removed any pre-existing `tmp` file with `os.unlink`, opened with `os.O_NOFOLLOW`, set mode `0o600` via `os.fchmod(fd, 0o600)` on the open file descriptor, and enforced `os.chmod(p, 0o600)` after `os.replace`. Confirmed that only a confirmation message (`stored <name> (<len> characters)`) is printed and the secret value is never echoed to stdout or stderr. Confirmed no insecure fallback locations are used.

#### Finding 7: Credential Leakage via Shell Tracing (xtrace) and CLI Arguments in `worker/git-credential-forgejo`
- **Severity**: Medium
- **Location**: `worker/git-credential-forgejo`
- **Abuse Scenario**:
  1. When git or a worker executed the helper in a tracing/debug environment (such as `bash -x`, `set -x`, or inherited `SHELLOPTS=xtrace`), bash xtrace echoed `+ echo password=<TOKEN>` directly to stderr, leaking the worker's token into git command logs or container stderr.
  2. If git called the helper with verbose/debug flags (`-v get` or `--verbose get`), the check `[ "${1:-}" = "get" ]` failed, aborting credential resolution.
  3. If tokens were allowed to be passed via CLI arguments, `/proc/*/cmdline` would expose credentials to any user in the container or host PID namespace.
- **Fix**: Suppressed shell tracing immediately at entry with `set +x 2>/dev/null` and `set +v 2>/dev/null`. Implemented argument parsing to handle `-v`, `--verbose`, `-d`, `--debug` flags gracefully without outputting tokens. Guaranteed that tokens are never accepted or parsed from argv. Tokens are read strictly from `/run/secrets/forgejo-token` (or `FORGEJO_TOKEN_FILE`), using bash built-in file substitution `$(< ...)` to prevent spawning child processes (`tr`). Confirmed no fallback to insecure default directories exists.

#### Finding 8: Potential Credential Exposure in URL Query Strings in `bin/forgejo-api`
- **Severity**: Low
- **Location**: `bin/forgejo-api`
- **Abuse Scenario**: If a caller attempted to pass authentication credentials in URL query strings (e.g. `/repos/.../pulls?token=...` or `?access_token=...`), the token would be sent across HTTP query parameters and recorded in web server access logs and Squid egress proxy logs (which are analyzed by `bin/lib/egress.py`).
- **Fix**: Added explicit regex validation to reject any request containing `token=`, `access_token=`, or `secret=` in URL query parameters (`exit 2`). Confirmed that `bin/forgejo-api` places the monitoring token exclusively in the HTTP `Authorization: token <tok>` header. Handled `urllib.error.URLError` to prevent unhandled stack trace leaks. Confirmed no tokens are ever written to files under `runs/` (`ledger.jsonl`, `ALERT`, `events.jsonl`, transcripts). Confirmed no insecure default fallback exists.

### How to Verify
- Run the credential security test suite: `python3 tests/test_credentials.py` or `bash tests/check.sh`.
- Test permission hardening: Pre-create `~/.config/sandbox/secrets/anthropic.key.tmp` with mode 0666, store a secret via `printf '%s' "test-token" | bin/secret-set anthropic.key`, and verify `stat -c '%a' ~/.config/sandbox/secrets/anthropic.key` outputs `600`.
- Test tracing suppression: Run `bash -x worker/git-credential-forgejo get <<< "protocol=http"$'\n'"host=forgejo:3000"$'\n'` and verify the password value does not appear in stderr.
- Test query token rejection: Run `bin/forgejo-api GET "/repos/sandbox/playground/pulls?token=test"` and verify exit code 2 and refusal message.
- Verify static absence of argv/query credentials: `python3 -m unittest tests.test_credentials.TestForgejoApiSecurity.test_forgejo_api_source_code_uses_authorization_header_not_query`.

## install scripts

### Threat Model
- **Input sources**: Installer command-line arguments (`--demo`), environment variables (`SANDBOX_SECRETS`, `DOCKER_KEY_FPR`, `SUDO_USER`), and remote resources fetched during machine provisioning (Docker signing key and apt packages).
- **Sink / Reach**: Host filesystem (`/etc/docker/daemon.json`, `/etc/apt/sources.list.d/`, repository `etc/` configuration, `~/.config/sandbox/secrets`), Docker daemon commands, systemd unit files, and package management (`apt-get`).
- **Sensitive data**: Internal Forgejo administrator passwords and bot tokens (`forge-admin.token`, `orchestrator.token`, `monitor.token`), worker API keys (`anthropic.key`, `openai.key`, `gemini.key`, `compat.key`), upstream forge tokens (`github.token`, `gitlab.token`), webhook/notification URLs and secrets in `etc/*.conf`.
- **Attack surface / Threats**: Permissive file/directory permissions allowing other local users on a multi-user machine or VM to read sensitive configuration or credentials; unpinned remote code execution (`curl | bash`); token/password exposure in stdout, stderr, or cloud-init log files; silent security downgrades.
- **Invariants**: All secret directories must have mode 0700 and secret/config files mode 0600; installer processes must enforce restrictive umask (077 for user install, 027 for system baseline); remote downloads must be cryptographically pinned (never piped directly into a shell); credentials and tokens must never be printed to stdout or logged to world-readable files.

### Findings

#### Finding 1: Unrestricted Default Umask and World-Readable Configuration in `scripts/install.sh`
- **Severity**: HIGH
- **Location**: `scripts/install.sh:Configuration`
- **Abuse Scenario**: `scripts/install.sh` did not set `umask` and relied on standard shell defaults (commonly `0022` or `0002`). When copying example configurations (`cp etc/$f.example.conf etc/$f.conf`), files were created with mode `0644` (or `0664`), making configuration files world-readable. In Agent-Forge, configuration files in `etc/` (`foreman.conf`, `limits.conf`, `forge.conf`) and `projects/<name>/project.conf` contain notification webhook URLs (`NOTIFY_URL`), custom model server endpoints (`COMPAT_BASE_URL`), and architecture details. Any unprivileged local user or process on a multi-user VM could read the entire configuration. Furthermore, `etc/` directory itself was not restricted.
- **Fix**: Enforced `umask 077` at the start of `scripts/install.sh`. Explicitly hardened the directory `etc/` to mode `0700` and restricted all configuration files (`etc/*.conf` and `etc/team-rules.md`) to mode `0600`.

#### Finding 2: Unhardened Secrets Directory Permissions Default in `scripts/install.sh`
- **Severity**: HIGH
- **Location**: `scripts/install.sh:SECRETS`
- **Abuse Scenario**: The secrets directory (`~/.config/sandbox/secrets` or `$SANDBOX_SECRETS`) stores sensitive credentials including Forgejo administrator tokens (`forge-admin.token`), orchestrator tokens (`orchestrator.token`), and worker API keys. While individual tools like `bin/secret-set` restrict their own file descriptors, `scripts/install.sh` did not pre-create or enforce restrictive permissions (`0700`) on `$SANDBOX_SECRETS` before delegating to downstream scripts. If created under a permissive umask, intermediate directories (e.g., `~/.config/sandbox`) could be left world-traversable or readable.
- **Fix**: Pre-create and explicitly enforce mode `0700` on the secrets directory (`mkdir -p "$SECRETS" && chmod 700 "$SECRETS"`) and ensure `umask 077` applies to all downstream steps invoked by `scripts/install.sh`. Ensure any existing secret files are tightened to mode `0600`.

#### Finding 3: Missing Umask in Root Baseline Script `scripts/vm-baseline-ubuntu.sh`
- **Severity**: MEDIUM
- **Location**: `scripts/vm-baseline-ubuntu.sh`
- **Abuse Scenario**: `scripts/vm-baseline-ubuntu.sh` runs as root via `sudo` and creates system files such as `/etc/docker/daemon.json`, backup copies of daemon.json, and `/etc/apt/sources.list.d/docker.sources`. Without an explicit restrictive `umask`, files created by root could inherit a loose umask (e.g. if the invoking user environment had `umask 000` or `002`), potentially creating world-writable configuration files or permissive directories.
- **Fix**: Enforced `umask 027` at the beginning of `scripts/vm-baseline-ubuntu.sh`. Ensured critical system files have secure, appropriate permissions (mode `0644` for apt sources and docker daemon configs, mode `0755` for apt keyrings) and are never world-writable.

#### Finding 4: Remote Execution Pattern Review (`curl | bash` / `wget | sh`)
- **Severity**: HIGH (Audit Verification - Confirmed Safe)
- **Location**: `scripts/install.sh`, `scripts/vm-baseline-ubuntu.sh`
- **Abuse Scenario**: Piping unreviewed remote scripts directly to a shell (`curl ... | bash` or `wget ... | sh`) without integrity checks exposes the host to man-in-the-middle attacks, DNS hijacking, or upstream compromise, resulting in immediate remote code execution with root privileges.
- **Fix / Status**: Confirmed that neither `scripts/install.sh` nor `scripts/vm-baseline-ubuntu.sh` uses `curl | bash` or `wget | sh`. In `scripts/vm-baseline-ubuntu.sh`, Docker's signing key is fetched to a temporary file (`curl -fsSL ... -o /etc/apt/keyrings/docker.asc.tmp`) and strictly validated against a pinned cryptographic fingerprint (`DOCKER_KEY_FPR`, `9DC858229FC7DD38854AE2D88D81803C0EBFCD88`) via `gpg --show-keys` before installation. If the fingerprint does not match, the file is deleted and the script terminates immediately. Static regression tests enforce that no piping of web downloads into shells is permitted in either script.

#### Finding 5: Secrets Exposure in Stdout, Terminal, and Log Output
- **Severity**: MEDIUM (Audit Verification - Confirmed Safe)
- **Location**: `scripts/install.sh`, `scripts/vm-baseline-ubuntu.sh`
- **Abuse Scenario**: Installer scripts often display or log generated credentials (such as database passwords, initial admin tokens, or API keys). If written to stdout, these secrets can be captured in terminal scrollback, CI artifacts, or cloud-init log files (`/home/forge/agent-forge/install.log` or `/var/log/cloud-init-output.log`), which may be readable by unauthorized users or monitoring tools.
- **Fix / Status**: Confirmed that neither script prints passwords or tokens to stdout or stderr. Generated Forgejo passwords and access tokens are written directly to mode `0600` secret files by `scripts/forge-bootstrap` and referenced only by path. `scripts/install.sh` and `scripts/vm-baseline-ubuntu.sh` output status indications without secret interpolation. Static analysis checks verify that neither script contains commands echoing or printing sensitive credentials.

## bin/lib/sbx.py + bin/dispatch

### Finding 1: Path Traversal in Project and ID Helpers
- **Severity**: HIGH
- **Location**: `bin/lib/sbx.py:ks_path`, `project_conf`, `project_pool`, `ledger_path`, `run_path`, `secret_path`
- **Abuse Scenario**: Unvalidated project names containing directory traversal sequences (such as `../../etc`, `../KILLSWITCH`, or path separators) could be passed to `ks_path()`, `project_conf()`, or `project_pool()`. An attacker or compromised goal configuration could access or manipulate files outside the intended project/runs directory hierarchy, potentially triggering kill switches at unintended filesystem locations or reading unauthorized configuration files.
- **Fix**: Added `_check_project()` validation enforcing `PROJECT_RE` (`^[a-z0-9][a-z0-9-]{0,39}$`) and rejecting `..`, `/`, and `\` across all project path helpers (`ks_path`, `project_conf`, `project_pool`, `ledger_path`). Added dedicated path helpers `run_path(run_id)`, `run_dir(run_id)`, and `secret_path(name)` validating IDs against `RUN_ID_RE` / `SECRET_NAME_RE` and strictly rejecting path traversal sequences.

### Finding 2: Unvalidated Configuration Execution in `TEST_CMD` and `GATE_IMAGE`
- **Severity**: HIGH
- **Location**: `bin/lib/sbx.py:test_cmd`, `gate_image`
- **Abuse Scenario**: Projects define custom `TEST_CMD` and `GATE_IMAGE` settings in `project.conf` that are executed inside gate and maintenance containers via `/bin/sh -c <cmd>`. Previously, when a configuration supplied a malicious command or image containing shell metacharacters (e.g. `pytest; rm -rf /`, `pytest && curl evil.com`, `worker:1; rm -rf /`), `test_cmd()` and `gate_image()` silently fell back to defaults instead of failing closed, masking malicious configuration attempts or risking execution if fallback logic changed.
- **Fix**: Modified `test_cmd()` and `gate_image()` to validate non-empty values against `TEST_CMD_RE` and `IMAGE_RE`. If a configured command or image fails regex validation, the functions immediately raise `ValueError` (fail closed), preventing any execution of unvalidated commands.

### Finding 3: Missing Argument and Option Validation in `bin/dispatch`
- **Severity**: MEDIUM
- **Location**: `bin/dispatch:main`, `_opt`, `preflight`, `container_running`
- **Abuse Scenario**: `bin/dispatch` accepted worker arguments and optional environment flags (`DISPATCH_MODEL`, `DISPATCH_ROLE`, `DISPATCH_BRANCH`). When invoked with `--check <worker>`, the worker argument was passed directly to `container_running()` and `docker inspect` without regex validation, risking command-line option injection if arbitrary strings were supplied. In addition, malformed `DISPATCH_MODEL` or `DISPATCH_ROLE` values were silently ignored rather than actively refused.
- **Fix**: Validated worker arguments against `WORKER_RE` (`worker-\d+`) at entry in both `--check` mode and standard dispatch mode. Updated `_opt()` to validate environment variables against specific regexes (`MODEL_RE`, `ROLE_RE`, `SAFE_ARG`), refusing execution with exit code 3 if invalid values are supplied before any container inspection or limits locking occurs.

### Finding 4: Secrets Isolation in Argv and Environment Variables
- **Severity**: LOW (Audited & Confirmed Safe)
- **Location**: `bin/lib/sbx.py:SECRETS`, `worker/af-agent`, `worker/docker-compose.api.yml`
- **Abuse Scenario**: If API keys, tokens, or credentials from `~/.config/sandbox/secrets` were passed as command-line arguments in `subprocess` / `docker exec` argv or logged to disk, they could be leaked via process inspection (`/proc/*/cmdline`) or run artifacts.
- **Fix / Status**: Verified that credentials are never passed in argv elements or logged environment variables. API keys and tokens are mounted exclusively as read-only files (`/run/secrets/api-key:ro`, `/run/secrets/forgejo-token:ro`), and prompts are transmitted via stdin (`cat > "$prompt"`), guaranteeing that secrets remain isolated to the container runtime environment.

### Finding 5: Elimination of Shell String Concatenation and `shell=True`
- **Severity**: LOW (Audited & Confirmed Safe)
- **Location**: `bin/lib/sbx.py`, `bin/dispatch`
- **Abuse Scenario**: Using `shell=True` or joining command strings with spaces before passing them to a shell allows metacharacters in arguments (such as prompts or branch names) to break out into arbitrary command execution.
- **Fix / Status**: Verified that neither `sbx.py` nor `bin/dispatch` uses `shell=True` or shell string concatenation. All commands are passed strictly as lists (`argv`) to `subprocess.run` and `subprocess.Popen`. Added `sbx.sh(*args, **kw)` helper to enforce list-based execution.
