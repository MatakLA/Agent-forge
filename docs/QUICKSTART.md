# Quickstart: Git Clone to Merged PR in Under 30 Minutes

This guide walks a newcomer through going from `git clone` to a merged pull request in under 30 minutes, using `examples/sample-project` as the worked example.

## Prerequisites

- Local clone of the `agent-forge` repository.
- Python 3.10+ (standard library only).
- A running Agent-Forge deployment with at least one active worker container (refer to [docs/SETUP.md](SETUP.md) for initial host installation and setup).

## Step Budgets

The end-to-end pipeline operates under strict wall-clock step budgets defined in `tests/fixtures/quickstart_budget.STEP_BUDGETS` and `TOTAL_BUDGET`:

| Pipeline step | Approximate time range | Budget (seconds) |
|---|---|---|
| `install` | ≤5 min | 300s |
| `plan` | ≤3 min | 180s |
| `dispatch` | ≤2 min | 120s |
| `gate` | ≤5 min | 300s |
| `merge` | ≤1 min | 60s |
| **Total** | **≤30 min** | **1800s** |

Each step is measured and evaluated against its budget limit. Here is an example of the budget report line format:

```text
merge: 60.0s (budget 60s) OK
```

## Step-by-Step Walkthrough

Follow these commands in order to execute the sample project through the autonomous lifecycle:

### 1. Install and setup

Rather than re-describing host installation from scratch, follow the setup steps in [examples/sample-project/README.md](../examples/sample-project/README.md):

First, verify that the seed repository passes its existing test suite locally:

```bash
cd examples/sample-project/seed-repo
python3 -m unittest discover -s tests
cd ../../..
```

Next, configure your Agent-Forge project with the sample project configuration and product vision (see [examples/sample-project/README.md](../examples/sample-project/README.md) and [docs/SETUP.md](SETUP.md)):

```bash
cp examples/sample-project/project.conf <project-dir>/project.conf
cp examples/sample-project/vision.md <project-dir>/vision.md
```

Ensure the foreman service is active for your project before queueing work.

### 2. Queue the goal

Queue the goal narrative from `examples/sample-project/vision.md` using the foreman CLI:

```bash
bin/foreman goal "Add a bookmarks search <tag> command that lists only the bookmarks having that tag, with a test that fails without it."
```

### 3. Monitor Plan, Dispatch, Gate, and Merge

Monitor execution from the terminal:

```bash
bin/foreman status
```

You can also monitor progress in the web dashboard (`http://127.0.0.1:8171`).

During execution, work advances through the lifecycle stages (`pending → gate → merge`):

#### Plan (≤3 min, budget 180s)
- **CLI (`bin/foreman status`):** The goal appears as planned, and the orchestrator creates a backend task in `pending` status:
  ```text
  [planned] G20261013-0001: Add a bookmarks search <tag> command that lists only the bookmarks having that tag...
      pending  0001-backend  (backend, attempt 1)
  ```
- **Dashboard:** The Overview and Goals tabs show the goal planned into tasks. The Tasks board displays the new task card in the Pending column.

#### Dispatch (≤2 min, budget 120s)
- **CLI (`bin/foreman status`):** The foreman assigns the task to an available worker container:
  ```text
  [planned] G20261013-0001: Add a bookmarks search <tag> command that lists only the bookmarks having that tag...
      running  0001-backend  (backend, attempt 1, worker-1)
  ```
- **Dashboard:** The Live tab displays the worker's real-time transcript as it implements `search` and writes the accompanying unit test.

#### Gate (≤5 min, budget 300s)
- **CLI (`bin/foreman status`):** Once the worker commits its branch, the task enters the gate:
  ```text
  [planned] G20261013-0001: Add a bookmarks search <tag> command that lists only the bookmarks having that tag...
      gate     0001-backend  (backend, attempt 1, worker-1)
  ```
- **Dashboard:** The task sheet details the gate execution. Tests run in a clean, isolated container (`python3 -m unittest discover -s tests`) to verify the implementation before review.

#### Merge (≤1 min, budget 60s)
- **CLI (`bin/foreman status`):** After the gate passes and the orchestrator approves the review, the change is merged into `main` via a pull request:
  ```text
  [done] G20261013-0001: Add a bookmarks search <tag> command that lists only the bookmarks having that tag...
      done     0001-backend  (backend, attempt 1)  PR #1
  ```
- **Dashboard:** The pull request is marked merged, the task moves to Done, and the goal status updates to Done.

Total pipeline execution completes within ≤30 min (budget 1800s).

## Expected Result

The repository contains the new `bookmarks search <tag>` command, the new unit test passes alongside existing `add` and `list` tests, and the pull request is merged into `main`.

## What to Do When It Fails

- **Task stays pending or idle:** Run `bin/foreman status` and `bin/killswitch status` to check worker availability and killswitch state. Consult [docs/SETUP.md](SETUP.md) for worker troubleshooting.
- **Gate test failure:** If tests fail, the gate rejects the branch and passes test output back to the worker for automatic rework. Review logs in `bin/foreman status` or the dashboard task sheet.
- **Service issues:** Run `bin/doctor` to verify environment and component health. Refer to [docs/SETUP.md](SETUP.md) for service fixes.

## How This Is Verified

This walkthrough is verified by `tests/test_quickstart_e2e.sh`. The verification script needs Node.js and uses no real API keys or Docker (`tools/fake-docker`, `tests/fixtures/fake-model-api.js`).
