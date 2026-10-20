# The dashboard

The dashboard is a single web page served by `agent-forge-web` on `127.0.0.1:8171`. It needs no build step and loads nothing from
the internet. `forge url` prints the address and the SSH tunnel command; remote access and the optional sign-in token are covered
in [INSTALL.md](INSTALL.md#remote-access) and [SECURITY.md](../SECURITY.md). To look at it without installing anything, see
[DEMO.md](DEMO.md).

The dashboard can change settings, stop projects and create workers. Do not put it on a public address.

## Tabs

| Tab | What is on it |
|---|---|
| **Overview** | What needs you (approvals, questions, blocked tasks), the first steps of a new install, a form for new goals, the team (and why an idle worker is idle), the goals and the backlog. Drag backlog items to reorder them. |
| **Tasks** | The task board and the dependency graph with the critical path. A task's sheet shows what it is, what it cost, its runs (each one can be replayed) and only the actions that fit its state. |
| **Live** | The transcript of every running AI as it works, and the replay of any finished run. |
| **Timeline** | The last 24 hours of runs per worker. |
| **Insights** | **Team** (what gets done and how well, trends by week), **Money & quota** (spend, caps, forecasts), **Experiments** (A/B results on 95 % intervals, with a button to adopt the winner). |
| **System** | **Health** (the checks of `bin/doctor`, with a button for each safe fix), **Egress** (blocked hosts, allow or ignore them, alarms), **Disk** (usage and cleanups), **Activity** (the event log). |
| **Settings** | Workers (kill switch, the pool, add and remove), Runtimes (what can run, API keys, your model server), Models (who plans and reviews, routing, experiments), Project (where the code lives, approvals, tests, notifications), Money (budgets and prices), Knowledge (the team's documents and roles), Limits, Advanced. |

A goal has its own page: its plan and tasks, everything that happened to it in order, what it cost, its acceptance criteria with
the evidence, and all its changes in one diff. Search (`/` or the magnifier in the top bar) covers goals, tasks, events and worker
transcripts. Tasks, goals and runs have deep links (`#task=...`, `#goal=...`, `#run=...`).

## Autopilot and pause

Two switches on the Overview tab, each with a tooltip:

- **Autopilot** (`forge autopilot on|off`): when a worker is free, the orchestrator picks the next goal from your vision (`docs/vision.example.md` shows the format).
- **Pause / resume** (`forge pause` / `forge resume`): pausing stops new dispatches and lets running runs finish, the same as the
  CLI. Resume is blocked while the kill switch is on; the dashboard says so.

## Themes

The theme button (`◐`) in the top bar cycles through **Auto** (follows the system setting), **Light** (`☀`) and **Dark** (`☾`). The
choice is kept in the browser.

## Languages

The language button (`EN` / `SK`) in the top bar switches the interface without reloading the page. The choice is kept in the
browser.

Locales are JSON files in [`web/static/locales/`](../web/static/locales/). The repository ships English (`en`, `eng_us`) and
Slovak (`sk`, `sk_qwerty`). To add a language:

1. Copy `web/static/locales/en.json` to `web/static/locales/<code>.json` and translate the values.
2. In the dashboard, right-click the language button and choose the locale, enter its `<code>`, or upload a `.json` file. The
   dashboard loads `/static/locales/<code>.json`.

More in [web/static/locales/README.md](../web/static/locales/README.md). The interface is not complete in every language; a
missing key falls back to English.

## Keyboard

`/` opens the search. Dialogs and the System tab's fixes are ordinary buttons, so Tab and Enter work. If something is not reachable
without a mouse, please report it as a bug.
