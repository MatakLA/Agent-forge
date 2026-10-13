# Quickstart: Git Clone to Merged PR in Under 30 Minutes

This walkthrough guides a newcomer who just ran `git clone` through seeing Agent-Forge plan, dispatch, gate, and merge a real pull request within 30 minutes, using `examples/sample-project` as the worked example.

## Prerequisites

- Local clone of the `agent-forge` repository.
- Python 3.10+ (standard library only).
- A running Agent-Forge deployment with at least one active worker container, following the setup steps in [examples/sample-project/README.md](../examples/sample-project/README.md).

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
<step>: <N>s (budget <N>s) OK
```

## Step-by-Step Walkthrough

Follow these commands in order:

### 1. Install and setup

Rather than re-describing host installation from scratch, follow the documented setup steps in [examples/sample-project/README.md](../examples/sample-project/README.md):

First, verify that the seed repository passes its existing test suite locally:

```bash
cd examples/sample-project/seed-repo
python3 -m unittest discover -s tests
```

Next, follow the setup steps in [examples/sample-project/README.md](../examples/sample-project/README.md) to initialize the project pointing at `seed-repo/`, copy `project.conf` and `vision.md` into your project configuration directory, and ensure the foreman service is active.

### 2. Queue the goal

Queue the goal narrative defined in [examples/sample-project/vision.md](../examples/sample-project/vision.md):

```bash
bin/foreman goal 'Add a `bookmarks search <tag>` command that lists only the bookmarks having that tag, with a test that fails without it.'
```

### 3. Monitor Plan, Dispatch, Gate, and Merge

Monitor execution from the CLI:

```bash
bin/foreman status
```

You can also monitor progress in the web dashboard.

As execution proceeds, tasks advance through the lifecycle (`pending → gate → merge`):

#### Plan (≤3 min, budget 180s)
- **CLI (`bin/foreman status`):** The orchestrator plans the queued goal into tasks, with the new task appearing in `pending` status.
- **Dashboard:** The goal and its planned task appear in the overview and task board in pending state.

#### Dispatch (≤2 min, budget 120s)
- **CLI (`bin/foreman status`):** The task transitions to running with its assigned worker container.
- **Dashboard:** The task moves to running state on the board, and the live stream displays the worker container's execution transcript as it implements the code and tests.

#### Gate (≤5 min, budget 300s)
- **CLI (`bin/foreman status`):** Once the worker commits its branch, the task status advances to `gate`.
- **Dashboard:** The gate run verifies the implementation by executing the project test suite in a clean, isolated container before orchestrator review.

#### Merge (≤1 min, budget 60s)
- **CLI (`bin/foreman status`):** Once the gate passes and the orchestrator review approves the change, the PR merges into `main` and the task and goal advance to completed status.
- **Dashboard:** The pull request is marked merged into `main`, and the task and goal show as complete.

Total pipeline execution finishes within ≤30 min (budget 1800s).

## Expected Result

The repository contains the new `bookmarks search <tag>` command, the new unit test passes alongside existing `add` and `list` tests, and the pull request is merged into `main`.

## What to Do When It Fails

- **Budget exceeded:** If any step exceeds its budget limit, the runner exits non-zero and reports the offending step along with its elapsed time and budget ceiling.
- **Test or gate failure:** If tests fail during the gate step, inspect the test output returned by the gate runner and verify that `seed-repo/tests` pass locally (`python3 -m unittest discover -s tests`).

## How This Is Verified

This walkthrough is verified by `tests/test_quickstart_e2e.sh`. The verification script needs Node.js and uses no real API keys or Docker (`tools/fake-docker`, `tests/fixtures/fake-model-api.js`).
