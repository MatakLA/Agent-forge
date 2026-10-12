# Sample Project: Bookmarks CLI

This directory (`examples/sample-project/`) contains a tiny, runnable example project designed for evaluating Agent-Forge on an existing codebase.

## What this is

The sample project pairs three files that represent a complete, miniature Agent-Forge setup:

- `seed-repo/`: A clean starting repository containing `bookmarks`, an executable Python 3 CLI tool that uses only the standard library. The tool already implements `add` and `list` subcommands, persisting bookmark entries to a JSON file. It includes a passing offline test suite in `seed-repo/tests/`. You can inspect and run the tests locally with `python3 -m unittest discover -s tests` inside `seed-repo/`. The seed repository deliberately lacks a search command.
- `vision.md`: A product vision document following the standard format described in [docs/vision.example.md](../../docs/vision.example.md). It outlines the purpose of `bookmarks`, its architectural constraints (standard library only, no external dependencies), and the target goal: adding a `search <tag>` subcommand with test coverage.
- `project.conf`: A project configuration file that sets the test runner command (`TEST_CMD=python3 -m unittest discover -s tests`), gate container image, and language settings.

## Prerequisites

Before trying this example end to end, make sure you have:

1. A running Agent-Forge deployment with Docker, the internal Forgejo Git server, and at least one configured worker container. For initial installation and VM setup instructions, refer to [docs/SETUP.md](../../docs/SETUP.md).
2. Shell access to the Agent-Forge host machine.
3. Access to the web dashboard (`http://127.0.0.1:8171`).

## How to try it end to end

Follow these steps to run the sample project through the complete planning, dispatch, gating, and merge cycle:

### 1. Verify the seed repository locally

Check that the seed code and offline test suite pass before handing the repository to Agent-Forge:

```bash
cd examples/sample-project/seed-repo
python3 -m unittest discover -s tests
```

The test runner discovers and passes all existing tests, verifying that `add` and `list` function correctly and confirming that `search` does not exist yet. Return to the repository root when finished.

### 2. Initialize a fresh Agent-Forge project

Create a new project named `bookmarks` targeting a repository path (for example, `sandbox/bookmarks` on Forgejo):

```bash
bin/foreman init-project bookmarks --repo sandbox/bookmarks
```

You can also create the project from the web dashboard by navigating to the project picker and clicking **+ New project**. For detailed multi-project configuration options, refer to [docs/SETUP.md](../../docs/SETUP.md).

### 3. Bootstrap the repository and push the seed files

Bootstrap the target repository on Forgejo to establish permissions and branch protection on `main`:

```bash
FORGE_REPO=sandbox/bookmarks scripts/forge-bootstrap
```

Next, initialize a Git repository from a copy of `seed-repo/` and push its files to `main`:

```bash
TEMP_DIR=$(mktemp -d)
cp -r examples/sample-project/seed-repo/. "$TEMP_DIR"
cd "$TEMP_DIR"
git init -b main
git config user.name "Setup"
git config user.email "setup@example.com"
git add -A
git commit -m "Initial commit of bookmarks seed repository"
git remote add origin http://127.0.0.1:3000/sandbox/bookmarks.git
git push -u origin main --force
rm -rf "$TEMP_DIR"
```

If you are using an upstream GitHub or GitLab repository instead of Forgejo, push `seed-repo/` to your upstream repository as described in [docs/UPSTREAM.md](../../docs/UPSTREAM.md).

### 4. Copy project configuration and vision

Copy `project.conf` and `vision.md` into the new project's configuration directory under `projects/bookmarks/`:

```bash
cp examples/sample-project/project.conf projects/bookmarks/project.conf
cp examples/sample-project/vision.md projects/bookmarks/vision.md
```

`project.conf` instructs the gate container to use `python3 -m unittest discover -s tests` as its `TEST_CMD`, ensuring automated checks run without external package installations.

### 5. Start the project foreman service

If running multi-project systemd services, install and start the foreman service for `bookmarks`:

```bash
sudo scripts/install-services --project bookmarks
```

Alternatively, run the foreman directly in your terminal:

```bash
bin/foreman --project bookmarks
```

### 6. Queue the goal

Queue the goal specified in `vision.md` through the web dashboard (select `bookmarks` in the project picker, go to **Overview**, and click **New goal**) or via the command line:

```bash
bin/foreman --project bookmarks goal "Add a bookmarks search <tag> command that lists only the bookmarks having that tag, with a test that fails without it."
```

### 7. Watch the cycle run

Monitor the goal progress in the dashboard or via the CLI:

- **Dashboard:** The **Overview** tab displays goal progress; the **Tasks** tab renders the task board and dependency graph; the **Live** tab streams worker output in real time; and the goal details page lists acceptance criteria and review status.
- **CLI:** Check active status at any time:
  ```bash
  bin/foreman --project bookmarks status
  ```

## What to expect

Once queued, the foreman drives the goal through the standard lifecycle:

1. **Planning:** The orchestrator reads `vision.md` and repository code, breaking the goal into a small number of tasks (likely one backend task to implement the `search` subcommand plus its test).
2. **Dispatch:** The foreman assigns the task to an available worker container in an isolated sandbox with egress restricted to allowed hosts.
3. **Gating:** Once the worker commits its implementation, the foreman runs the project's test suite (`python3 -m unittest discover -s tests`) in a clean, fresh offline container. The gate ensures all unit tests pass, confirming the new `search` command works and existing `add`/`list` behavior remains intact.
4. **Review and Merge:** The orchestrator reviews the diff against acceptance criteria. Upon approval, the change is merged into `main` via a pull request.

This mirrors exactly what a real production goal looks like in Agent-Forge, scaled down so it finishes in minutes without external dependencies.

## What to do when it fails

If execution stops or an issue occurs, check the following:

- **Worker stays idle:** Run `bin/foreman --project bookmarks status` to see why the task is pending. Check `bin/killswitch status` to ensure the safety kill switch is off, and verify worker quota or container health with `bin/doctor`.
- **Gate test failure:** If a worker produces code that fails tests, the gate rejects the branch and returns the failure output to the worker for automatic rework. Inspect the task sheet and test output in the dashboard **Tasks** tab.
- **Service or repository errors:** Confirm Forgejo is responding on port 3000 and the project's foreman service is active:
  ```bash
  systemctl status agent-forge-foreman@bookmarks.service
  ```
