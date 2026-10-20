# Quickstart: from zero to a merged pull request

The target is 30 minutes from a clean machine, most of it waiting. You install Agent-Forge, add one worker, give it a small goal on a sample project, and watch
the loop **plan, build, test, review and merge** it. At the end there is a merged pull request in a repository of yours.

You do not need to read anything else first. Just a terminal on the install machine and a browser. Commands that start with
`bin/` run from the checkout (`cd ~/agent-forge`); `forge` works from anywhere once it is on your `PATH`.

## What you need

- A machine or VM for the real install: Ubuntu 22.04+, 4+ vCPU, 8+ GB RAM, 40+ GB disk ([requirements](INSTALL.md#requirements)).
- **One way to pay for a model:** an API key (Anthropic, OpenAI or Gemini), or a Google account with an Antigravity
  subscription. Not sure? [CHOOSING-AN-AI.md](CHOOSING-AN-AI.md). A first goal on this sample costs little; the caps in
  `etc/limits.conf` stop a run that goes wrong.
- **A throwaway repository** on GitHub or GitLab for the sample code, and a token for that one repository. No account? See
  [Without GitHub or GitLab](#without-github-or-gitlab) at the end.

Only want to look first? `docker run --rm -p 8171:8171 ghcr.io/matakla/agent-forge-demo:latest` shows the dashboard on invented data.

## 1. Install (about 20 minutes)

```bash
curl -fsSL https://raw.githubusercontent.com/MatakLA/Agent-forge/main/install.sh | bash
forge doctor
```

`forge doctor` should end without a `✘`. If it does not, it prints what to do. Problems: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
If `forge` is not found yet, open a new shell or use `~/agent-forge/forge`.

Open the dashboard. On the install machine it is <http://127.0.0.1:8171>; from your laptop, run what `forge url` prints
(an `ssh -L 8171:127.0.0.1:8171 ...` tunnel), then open the same address there.

## 2. Add one worker

In the dashboard: **Settings → Workers → + Add a worker**.

- **With an API key:** pick Claude Code, Codex or Gemini and paste the key once.
- **With Antigravity:** pick it; a terminal opens in the page and you sign in with the worker's Google account.

From a shell instead, for example with an Anthropic key:

```bash
printf '%s' "$ANTHROPIC_API_KEY" | bin/secret-set anthropic.key
bin/worker-add 1 --runtime claude
```

(`bin/worker-add 1` alone makes an Antigravity worker; sign it in with `docker exec -it worker-1 agy-run`.) The key is stored in
`~/.config/sandbox/secrets` (mode 0600) and only reaches the agent's environment. The worker can reach its provider's API host and
nothing else. More: [RUNTIMES.md](RUNTIMES.md).

## 3. Put the sample project in a repository

The sample is `examples/sample-project`: a tiny standard-library Python CLI, `bookmarks`, with `add` and `list`, and a passing test
suite. It deliberately has no search. Copy the code out of the checkout and check that its tests pass:

```bash
cp -r ~/agent-forge/examples/sample-project/seed-repo ~/bookmarks-demo && cd ~/bookmarks-demo
rm -rf tests/__pycache__ && printf '__pycache__/\n' > .gitignore
python3 -m unittest discover -s tests
```

Create an **empty** repository on GitHub or GitLab (for example `you/bookmarks-demo`) and push the copy to its `main`:

```bash
git init -b main && git add . && git commit -m "Seed: bookmarks CLI"
git remote add origin git@github.com:you/bookmarks-demo.git && git push -u origin main
cd ~/agent-forge
```

Connect it: Overview → **Get started → Choose where the code lives → GitHub** (or GitLab), the repository name, and a token for
**that repository only** (GitHub: fine-grained, *Contents* and *Pull requests* read and write. GitLab: project access token, role
Developer, scopes `api` and `write_repository`). The dashboard checks the token and imports `main` into the internal Forgejo.
Workers never see the token. By hand, with the same effect:

```bash
echo '{"FORGE_KIND": "github", "UPSTREAM_REPO": "you/bookmarks-demo"}' | bin/foreman settings apply
printf '%s' "$TOKEN" | bin/secret-set github.token
bin/upstream check && bin/upstream import
```

Last, tell the gate how to run this project's tests, and give the team the sample's vision. Turn the autopilot off so that it does
not invent further goals after the first one:

```bash
echo '{"TEST_CMD": "python3 -m unittest discover -s tests"}' | bin/foreman settings apply
bin/foreman doc set vision < examples/sample-project/vision.md
forge autopilot off
```

Details on tokens and how pull requests work: [UPSTREAM.md](UPSTREAM.md).

## 4. Queue the goal

```bash
forge goal 'Add a `bookmarks search <tag>` command that lists only the bookmarks having that tag, with a test that fails without it.'
```

Or in the dashboard: Overview → *What should the team do?* → **Start now**.

## 5. Watch it work

`forge status` shows goals, tasks, workers and recent events; the dashboard shows the same with live transcripts. A goal goes through:

| stage | what happens | the dashboard says |
|---|---|---|
| plan | the orchestrator reads the vision and the code and splits the goal into tasks (here most likely one) | goal *Planning*, task *Splitting* |
| dispatch | a free worker takes the task in its own container, on its own branch | task *Running*; open **Live** to read along |
| build | the worker writes the command and the test, and commits | task *Running* |
| gate | a fresh container without network runs the full test suite on the branch | task *Checking* |
| review | a tool-less LLM call reads the diff against the acceptance criteria: approve, rework or reject | task *Checking* |
| merge | the orchestrator pushes the branch to your repository, opens a pull request and merges it | task *Merging*, then *Done* |

How long it takes depends on the model and the queue; the foreman checks every 20 seconds. The time budgets this page is tested
against are at the end.

If the gate fails, the worker gets the test output and tries again by itself (rework). If it is stuck, the task turns *Blocked* and the
**Needs you** list on the Overview says why.

## 6. Check the result

Open your repository: there is a merged pull request, with the review and the gate output in its description. Pull `main` and try it:

```bash
cd ~/bookmarks-demo && git pull origin main
BOOKMARKS_FILE=/tmp/b.json bin/bookmarks add https://example.com --tag demo
BOOKMARKS_FILE=/tmp/b.json bin/bookmarks search demo
```

The goal page in the dashboard shows the tasks, the events, the cost and whether each acceptance criterion was met.

## What next

- Give it a real goal on your own repository: connect it the same way (step 3), then `forge goal "..."`.
  Small goals that end in something checkable work best. Several at once go to the backlog (`forge backlog`).
- Turn the autopilot back on (`forge autopilot on`) and write your own `docs/vision.md` ([vision.example.md](vision.example.md)):
  when a worker would be idle, the orchestrator proposes the next goal from it, and you approve what you want to approve.
- Add more workers to work in parallel, or mix runtimes ([RUNTIMES.md](RUNTIMES.md)).
- Get a push notification when something needs you: Settings → Project → `NOTIFY_URL` (ntfy, Slack, Discord, webhook).
- Know the limits: [FAQ.md](FAQ.md) and [SECURITY.md](../SECURITY.md).

## Without GitHub or GitLab

Skip step 3. The default project already lives on the internal Forgejo (`http://127.0.0.1:3000`, repository `sandbox/playground`,
`main` protected so that only the orchestrator merges). The sample code is not there, so give the team the whole job instead:

```bash
forge goal 'Create a stdlib-only Python 3 CLI "bookmarks" (bin/bookmarks) with add <url> [--tag T] and list, storing to a JSON file, and a unittest suite in tests/ run by: python3 -m unittest discover -s tests'
```

Set `TEST_CMD` as in step 3 first. The rest is the same, except that the pull request is on the internal Forgejo.

## If it stalls

`forge doctor` first, then [TROUBLESHOOTING.md](TROUBLESHOOTING.md). The usual causes: no worker is signed in or has no key
(the Overview shows why an idle worker is idle), the kill switch is on, or the provider's quota is used up.

## How this is verified

`tests/test_quickstart_e2e.sh` runs the commands of this page against a fake model API and `tools/fake-docker`, in an isolated
scratch directory (no real keys, no Docker, nothing in your `runs/`): the sample's own tests, `bin/foreman goal` with the goal
above, and `bin/foreman status`. It checks that every step stays inside its wall-clock budget. It does not call a real model, so it
shows that the commands and the flow work, not how well a model does the job. It needs Node.js; without it the script prints
`SKIP: node is not available on host; quickstart e2e check requires node` and exits 0.

The budgets come from `tests/fixtures/quickstart_budget.py` (`STEP_BUDGETS`, `TOTAL_BUDGET`):

| step | budget |
|---|---|
| install | 300 s |
| plan | 180 s |
| dispatch | 120 s |
| gate | 300 s |
| merge | 60 s |
| **total** | **1800 s** |

With real models, plan and build take longer than on the fake API; the table in step 5 is what to expect.
