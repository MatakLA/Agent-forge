# Sample Project: Bookmarks CLI

This directory (`examples/sample-project/`) contains a tiny, runnable example project designed for evaluating Agent-Forge on an existing codebase.

## What this is

The sample project pairs three files that represent a complete, miniature Agent-Forge setup:

- `seed-repo/`: A clean starting repository containing `bookmarks`, an executable Python 3 CLI tool that uses only the standard library. The tool already implements `add` and `list` subcommands, persisting bookmark entries to a JSON file. It includes a passing offline test suite in `seed-repo/tests/`. You can inspect and run the tests locally with `python3 -m unittest discover -s tests` inside `seed-repo/`. The seed repository deliberately lacks a search command.
- `vision.md`: A product vision document following the standard format described in [docs/vision.example.md](../../docs/vision.example.md). It outlines the purpose of `bookmarks`, its architectural constraints (standard library only, no external dependencies), and the target goal: adding a `bookmarks search <tag>` subcommand with test coverage.
- `project.conf`: A project configuration file that sets the test runner command (`TEST_CMD=python3 -m unittest discover -s tests`), gate container image, and language settings.

## Prerequisites

Before trying this example end to end, ensure you have a running Agent-Forge deployment with at least one active worker container. Refer to [docs/SETUP.md](../../docs/SETUP.md) for initial host setup and installation instructions.

## How to try it end to end

Follow these steps to run the sample project through the complete planning, dispatch, gating, and merge cycle:

### 1. Verify the seed repository locally

Check that the seed code and offline test suite pass before handing the repository to Agent-Forge:

```bash
cd examples/sample-project/seed-repo
python3 -m unittest discover -s tests
```

The test runner discovers and passes all existing tests, verifying that `add` and `list` function correctly and confirming that `search` does not exist yet. Return to the repository root when finished.

### 2. Point a fresh Agent-Forge project at seed-repo

Set up a fresh Agent-Forge project and point its repository at a copy of `seed-repo/`. For the exact project initialization, repository setup, and worker pool steps, refer to [docs/SETUP.md](../../docs/SETUP.md).

### 3. Copy project configuration and vision

Copy `vision.md` and `project.conf` into your project's configuration directory:

```bash
cp examples/sample-project/project.conf <project-dir>/project.conf
cp examples/sample-project/vision.md <project-dir>/vision.md
```

`project.conf` sets `TEST_CMD=python3 -m unittest discover -s tests`, instructing the gate container how to run the project's test suite without external dependencies. `vision.md` defines the roadmap and constraints following the format described in [docs/vision.example.md](../../docs/vision.example.md).

### 4. Ensure the foreman service is active

Verify or start the foreman service for your project as documented in [docs/SETUP.md](../../docs/SETUP.md).

### 5. Run the goal

Run the goal written in `vision.md` through the web dashboard or CLI (see [docs/SETUP.md](../../docs/SETUP.md) for submitting goals):

```text
Add a bookmarks search <tag> command that lists only the bookmarks having that tag, with a test that fails without it.
```

Watch the foreman plan, dispatch, gate, and merge the goal in real time through the dashboard or via `bin/foreman status`.

## What to expect

Once queued, the foreman drives the goal through the standard lifecycle:

1. **Planning:** The orchestrator reads `vision.md` and repository code, breaking the goal into a small number of tasks (likely one backend task to implement the `search` subcommand plus its test).
2. **Dispatch:** The foreman assigns the task to an available worker container in an isolated sandbox with egress restricted to allowed hosts.
3. **Gating:** Once the worker commits its implementation, the foreman runs the project's test suite (`python3 -m unittest discover -s tests`) in a clean, fresh offline container. The gate ensures all unit tests pass, confirming the new `search` command works and existing `add`/`list` behavior remains intact.
4. **Review and Merge:** The orchestrator reviews the diff against acceptance criteria. Upon approval, the change is merged into `main` via a pull request.

This mirrors exactly what a real production goal looks like in Agent-Forge, scaled down so it finishes in minutes without external dependencies.

## What to do when it fails

If execution stops or an unexpected result occurs, check the following:

- **Worker stays idle:** Check `bin/foreman status` and `bin/killswitch status` to see why tasks are pending or if the kill switch is engaged. Refer to the common problems guide in [docs/SETUP.md](../../docs/SETUP.md) to inspect worker quotas and container states.
- **Gate test failure:** If a worker's change fails the test suite, the gate rejects the branch and returns failure output to the worker for automatic rework. You can review test feedback in the dashboard task sheet.
- **Service or environment issues:** Run `bin/doctor` to verify component health across tools, configuration, and worker containers. Consult [docs/SETUP.md](../../docs/SETUP.md) for diagnostics and fixes.
