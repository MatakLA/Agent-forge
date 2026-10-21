# Security review log

What was found and fixed while reviewing Agent-Forge's own code, component by component. Most of this was done by the
maintainer with AI-assisted review passes, with a regression test added for each fix; it is **not** an independent third-party
audit. For what Agent-Forge protects against, what it does not, and how to report a problem, read [SECURITY.md](SECURITY.md).

The pattern behind nearly every finding is the same: a value that came from a dashboard request, a config file or an environment
variable ended up in a shell command, a file path or a file's permissions. The fixes follow one rule - check the value against an
anchored pattern or an allow-list at the entry point, pass arguments as lists (never through a shell), keep secrets out of argv, and
fail closed when something does not match.

Severity is the reviewer's estimate of impact on a default install, not a CVSS score.

## Dashboard backend (`web/dashboard/backend.py`)

The dashboard's write actions start shell commands, locally or over ssh. Tested by `tests/test_dashboard_backend.py`.

| Severity | Problem | Fix |
|---|---|---|
| High | Project name, repo, worker list and language were pasted into the shell command that creates a project, so `proj; touch /tmp/pwn` ran. | Each value is matched against its own pattern (`PROJECT_RE`, `REPO_RE`, `WORKER_RE`, `LANG_RE`) and quoted with `shlex.quote`. The product vision travels over stdin. |
| High | The runtime chosen for a new worker was formatted into the command unchecked. | Checked against the fixed set of known runtimes; anything else fails the job at once. |
| High | The `--project` flag used when connecting GitHub/GitLab was interpolated raw into three remote commands. | Must be empty or `--project NAME` with a valid name. Settings and tokens go over stdin, not argv. |
| Medium | The in-browser login terminal accepted any worker name and wrote the owner's text to the PTY as is, so control characters (Ctrl-C, escape sequences, newlines) could end the login program and reach the shell. | Worker names are validated on every call; text with control characters or longer than 4096 characters is rejected. |
| Low | `pflag` and `ssh_run` accepted non-strings, and a bad project name could break polling. | Type checks everywhere; polling skips a project with an invalid name instead of failing. No secret is ever put in argv. |

## Worker and egress containers (`worker/entrypoint.sh`, `egress/entrypoint.sh`)

Tested by `tests/test_egress_security.sh`.

| Severity | Problem | Fix |
|---|---|---|
| High | `FORGEJO_USER` and `FORGE_REPO` went unchecked into `git clone` and `git config`; a newline in the user name could add a git credential helper, and a repo starting with `-` could inject a git option. | Both must match strict patterns and may not contain `..`; the entrypoint exits before running any git command otherwise. |
| Medium | `EGRESS_MODE` was put into a file name (`squid.${MODE}.conf`) after only a file-exists check. | Only `enforce` or `discovery` is accepted, `enforce` by default. |
| Medium | The default umask left the cloned repository and `.git/config` readable by others. | `umask 077` in the worker, `027` in the proxy, and the playground and `.git` are tightened on existing volumes. |
| Medium | If a container was started as root, the entrypoint ran the agent or the proxy as root. | Both entrypoints drop to the unprivileged user, or refuse to start if they cannot. |
| Checked | Could a worker use the proxy before the enforce rules load? No: Squid parses the config first (`squid -k parse`), does not open port 3128 until it is loaded, and the enforce config ends in `http_access deny all`. | Nothing to fix; documented here. |

## Dashboard server (`web/server.py`)

The dashboard is meant for localhost or a private network, but a web page you visit can still try to talk to it. Tested by
`tests/test_web.py`.

| Severity | Problem | Fix |
|---|---|---|
| High | `/healthz` answered before the Host header was checked, so a DNS-rebinding page could detect a running instance. | The Host check is the first thing `do_GET` and `do_HEAD` do. |
| High | The Host check cut off the port without checking it, so `localhost:evil.com`, `[::1]evil.com` or an empty port passed. | The port must be digits only; malformed IPv6 is rejected; `.ts.net` names need a real sub-domain. |
| High | POST requests were protected by the `X-Agent-Forge` header only; `Origin` and `Referer` were never looked at, and `Origin: null` (sandboxed frames) got through. | If `Origin` or `Referer` is present it must pass the same host check; `null` and empty values get 403. |
| Low | The dashboard token must not leak through timing or through error text. | Verified `hmac.compare_digest` for query and cookie; the token is also stripped from every JSON, error and raw response. |
| Checked | Can an id from the URL reach a shell or a path? No: ids, document names, hosts and targets are matched with `fullmatch` against anchored patterns or allow-lists, and owner text goes over stdin. | Nothing to fix. |

## Editable documents and roles (`bin/lib/foreman/knowledge.py`)

Backs the dashboard's editor for prompts, rules and role playbooks. Tested by `tests/test_knowledge_paths.py`.

| Severity | Problem | Fix |
|---|---|---|
| High | Role names such as `../backend-developer` or `/backend-developer` were normalised into a real role instead of being refused. | Names with `..`, a slash, a backslash or an absolute path are rejected; the rest must match `ROLE_SLUG`. |
| High | `doc_path` did not check for traversal itself; it relied on the web layer's pattern, and `None` crashed it. | Own checks: non-empty string, no traversal, known document or `role:<slug>`. |
| High | `doc_set` writes files, so a bad path would have meant arbitrary file writes. | It only writes to what `doc_path` returns; if that is nothing, it stops with "unknown document" before creating anything. |
| Medium | `doc_reset` deleted a path built from the name without checking either. | Same validation, and the resolved path must lie inside the roles directory before `os.remove`. |
| Low | Defence in depth against odd patterns or symlinks. | Every resolved path must stay inside its base directory (`realpath` + `commonpath`). |

## Secrets and tokens (`bin/secret-set`, `worker/git-credential-forgejo`, `bin/forgejo-api`)

Tested by `tests/test_credentials.py`.

| Severity | Problem | Fix |
|---|---|---|
| Medium | `secret-set` wrote through `<file>.tmp`; if that file already existed with loose permissions, or was a symlink, the secret ended up world-readable or in the wrong place. | The old temp file is removed, the new one is opened with `O_NOFOLLOW`, set to 0600 by file descriptor, and the final file is 0600 again after the rename. The value is never printed. |
| Medium | Under `bash -x` or `SHELLOPTS=xtrace`, the git credential helper printed the token to stderr; git's `-v` flag also made it give up. | Tracing is switched off on entry, the verbose flags are accepted, the token is read from the secret file only (never argv) with no child process. |
| Low | `forgejo-api` would have sent a token in a URL query string if a caller passed one, which ends up in access logs and the egress log. | Requests with `token=`, `access_token=` or `secret=` in the query are refused (exit 2); the token goes only in the `Authorization` header. |

To check by hand:

```bash
# permissions are fixed even if a loose temp file exists
touch ~/.config/sandbox/secrets/anthropic.key.tmp && chmod 666 ~/.config/sandbox/secrets/anthropic.key.tmp
printf '%s' "test-token" | bin/secret-set anthropic.key
stat -c '%a' ~/.config/sandbox/secrets/anthropic.key        # 600

# the helper does not print the token when traced
bash -x worker/git-credential-forgejo get <<< $'protocol=http\nhost=forgejo:3000\n'

# tokens in the URL are refused
bin/forgejo-api GET "/repos/sandbox/playground/pulls?token=test"   # exit 2
```

## Installers (`scripts/install.sh`, `scripts/vm-baseline-ubuntu.sh`)

Tested by `tests/test_install_security.sh`. The rules: secret directories 0700, secret and config files 0600, no `curl | bash`,
nothing secret printed.

| Severity | Problem | Fix |
|---|---|---|
| High | The installer set no umask, so `etc/*.conf` (webhook URLs, model-server addresses) were created world-readable. | `umask 077`; `etc/` is 0700 and the config files 0600. |
| High | The secrets directory was not created or tightened by the installer itself, only by individual tools. | Created with 0700 up front; existing secret files are set to 0600. |
| Medium | The root baseline script inherited the caller's umask for the files it creates under `/etc`. | `umask 027`; apt sources and Docker config stay 0644, the keyring directory 0755, nothing world-writable. |
| Checked | Does either script pipe a download into a shell? No. Docker's signing key is downloaded to a temp file and compared to a pinned fingerprint before it is installed; a mismatch deletes it and stops. A test forbids download-to-shell patterns. | Nothing to fix. |
| Checked | Do the scripts print generated passwords or tokens? No: `forge-bootstrap` writes them to 0600 files and only the path is shown. | Nothing to fix. |

## Core library and dispatcher (`bin/lib/sbx.py`, `bin/dispatch`)

Tested by `tests/test_sbx_security.py`.

| Severity | Problem | Fix |
|---|---|---|
| High | Project names were turned into paths (kill switch, config, ledger) without validation, so `../KILLSWITCH` reached outside the project directory. | One `PROJECT_RE`, enforced in every path helper; run ids and secret names have their own checks. |
| High | A project's `TEST_CMD` and `GATE_IMAGE` run in containers under `/bin/sh -c`; an invalid value was silently replaced by the default, hiding a bad config. | Both are validated and a bad value raises an error instead. |
| Medium | `bin/dispatch` passed the `--check` worker name straight to `docker inspect`, and ignored malformed `DISPATCH_MODEL` / `DISPATCH_ROLE`. | Worker names must match `worker-N`; the environment options are validated and the dispatch refuses (exit 3) before touching Docker. |
| Checked | Can a key reach argv or a log? No: keys are mounted read-only as files in the container and prompts travel over stdin. | Nothing to fix. |
| Checked | Does any code use `shell=True` or build shell strings? No: everything is an argument list. | Nothing to fix. |

## What this does not cover

The gate, the review and the egress allow-list limit what an agent can do, and they are tested - but a determined attack on the
container runtime or the kernel is outside what this project can promise. The limits are stated in [SECURITY.md](SECURITY.md).
