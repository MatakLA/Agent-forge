# Quickstart: Git Clone to Merged PR in Under 30 Minutes

This walkthrough guides a newcomer who just ran `git clone` through seeing Agent-Forge plan, dispatch, gate, and merge a real pull request within 30 minutes, using `examples/sample-project` as the worked example.

## Prerequisites

- Local clone of the `agent-forge` repository.
- Python 3.10+ (standard library only).
- A running Agent-Forge deployment with at least one active worker container (see [docs/SETUP.md](SETUP.md) for host setup and worker configuration).

## Step Budgets

The end-to-end pipeline operates under wall-clock step budgets defined in `tests/fixtures/quickstart_budget.STEP_BUDGETS` and `TOTAL_BUDGET`:

| Pipeline step | Approximate time range | Budget (seconds) |
|---|---|---|
| `install` | ≤5 min | 300s |
| `plan` | ≤3 min | 180s |
| `dispatch` | ≤2 min | 120s |
| `gate` | ≤5 min | 300s |
| `merge` | ≤1 min | 60s |
| **Total** | **≤30 min** | **1800s** |

When evaluated, timings are formatted matching `tests/fixtures/quickstart_budget.format_report`:

```text
merge: 60.0s (budget 60s) OK
```

## Step-by-Step Walkthrough

Follow these commands in order:

### 1. Install and setup

Rather than re-describing host installation from scratch, follow the documented setup steps in [examples/sample-project/README.md](../examples/sample-project/README.md#how-to-try-it-end-to-end):

First, verify that the seed repository passes its existing test suite locally:

```bash
cd examples/sample-project/seed-repo
python3 -m unittest discover -s tests
```

Next, follow steps 2–4 of [examples/sample-project/README.md](../examples/sample-project/README.md) and [docs/SETUP.md](SETUP.md) to initialize an Agent-Forge project pointing at `seed-repo/`, copy `project.conf` and `vision.md` into your project configuration directory, and ensure the foreman service is active.

### 2. Queue the goal

Queue the goal narrative defined in [examples/sample-project/vision.md](../examples/sample-project/vision.md) (`Goal narrative (what a new owner would type as `bin/foreman goal "..."`)`):

```bash
bin/foreman goal 'Add a `bookmarks search <tag>` command that lists only the bookmarks having that tag, with a test that fails without it.'
```

### 3. Monitor Plan, Dispatch, Gate, and Merge

Monitor execution from the CLI:

```bash
bin/foreman status
```

You can also monitor progress in the web dashboard at `http://127.0.0.1:8171`.

As execution proceeds, tasks advance through the lifecycle (`pending → gate → merge`):

#### Plan (≤3 min, budget 180s)
- **CLI (`bin/foreman status`):** The goal appears with status `[active]`, and the planned task appears with status `pending`:
  ```text
  foreman: active   orchestrator: Claude Code

  [active] <goal-id>: Add a `bookmarks search <tag>` command that lists only the bookmarks having that tag...
      pending  <task-id>  (<role>, attempt 1)
  ```
- **Dashboard:** The Overview and Goals tabs show the goal broken down into tasks. The Tasks board displays the new task card in the Pending column.

#### Dispatch (≤2 min, budget 120s)
- **CLI (`bin/foreman status`):** The task transitions to `running` with the assigned worker container:
  ```text
      running  <task-id>  (<role>, attempt 1, worker-1)
  ```
- **Dashboard:** The Tasks board moves the card to Running, and the Live tab streams the worker container's real-time transcript as it implements the code and tests.

#### Gate (≤5 min, budget 300s)
- **CLI (`bin/foreman status`):** Once the worker commits its branch, the task status advances to `gate`:
  ```text
      gate     <task-id>  (<role>, attempt 1, worker-1)
  ```
- **Dashboard:** The task sheet details the gate run. Tests execute in a clean, isolated container (`python3 -m unittest discover -s tests`) to verify the implementation before review.

#### Merge (≤1 min, budget 60s)
- **CLI (`bin/foreman status`):** Once the gate passes and the orchestrator review approves the change, the PR merges into `main` and the goal completes:
  ```text
  [done] <goal-id>: Add a `bookmarks search <tag>` command that lists only the bookmarks having that tag...
      done     <task-id>  (<role>, attempt 1)  PR #1
  ```
- **Dashboard:** The pull request is marked merged, the task moves to the Done column, and the goal status updates to Done.

Total pipeline execution finishes within ≤30 min (budget 1800s).

## Expected Result

The repository contains the new `bookmarks search <tag>` command, the new unit test passes alongside existing `add` and `list` tests, and the pull request is merged into `main`.

## What to Do When It Fails

- **Task stays pending or idle:** Check `bin/foreman status` and `bin/killswitch status` to see worker availability and killswitch state. Consult [docs/SETUP.md](SETUP.md) for worker troubleshooting.
- **Gate test failure:** If tests fail, the gate rejects the branch and returns test failure output to the worker for automatic rework. Review logs in `bin/foreman status` or the dashboard task sheet.
- **Service issues:** Run `bin/doctor` to verify component health. Consult [docs/SETUP.md](SETUP.md) for service fixes.

## How This Is Verified

This walkthrough is verified by `tests/test_quickstart_e2e.sh`. The verification script needs Node.js and uses no real API keys or Docker (`tools/fake-docker`, `tests/fixtures/fake-model-api.js`).
