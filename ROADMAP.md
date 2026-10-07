# Roadmap

Where Agent-Forge is going, roughly in order. Ideas and votes welcome in the issues / discussions.

## Before 1.0 - proof
- [ ] **Real-world validation, published.** One end-to-end run per runtime (Claude Code, Codex, Gemini CLI with real keys) and
      per forge (GitHub, GitLab), with the numbers: success rate, cost and time per merged task.
- [ ] **A public repository built entirely by Agent-Forge** (the `examples/demo` tasks CLI), with its pull requests, reviews
      and reworks visible - the best demonstration of what the team does and does not do well.
- [ ] **A benchmark.** A fixed set of goals (or a small SWE-bench-style subset) run regularly: first-pass rate, cost per merged
      task, against a single-agent baseline.
- [ ] **Try it without a VM.** A Docker-only demo mode (`docker compose up`) with sample data and one worker, clearly marked as
      less isolated, for a five-minute look.
- [ ] `bin/foreman` (2,900 lines) and `web/server.py` (2,100 lines, page included) split into modules and static files, so
      contributions are easy.

## Next - capabilities
- [ ] **GitHub / GitLab issues as goals:** issues with a label become goals; the pull requests link back and close them.
- [ ] **Local and OpenAI-compatible models** for workers (Ollama, vLLM, OpenRouter) through the Codex runtime's provider
      settings - zero-cost runs for experiments.
- [ ] More agent runtimes (Aider, OpenHands, Cursor CLI) behind the same runtime interface (`bin/lib/runtimes.py`).
- [ ] Budgets per project and per worker, not only per install; a monthly cap.
- [ ] Human-in-the-loop modes per project: approve the plan, approve risky tasks, approve merges (one click in the dashboard).
- [ ] Monorepos and multi-repository projects (a task may span two repositories).
- [ ] Planner quality: learn task sizes from history, predict cost before dispatch, pick the cheapest model that passes.

## Dashboard ideas
Collected while building it; roughly by value.

**See what is happening**
- Live updates pushed by the server (Server-Sent Events) instead of polling every 2.5 s - instant and lighter.
- "Why is this worker idle?" on every worker card: cooldown and its reason, no ready task, waiting for dependencies, file
  conflicts with a running task.
- Replay of finished runs (not only live ones): step through a transcript, filter tool calls, copy commands.
- A goal page: its plan, tasks, merges and reworks on one timeline; time and money spent; all diffs combined; the evidence for
  each acceptance criterion.
- Project-wide dependency graph with the critical path highlighted; a forecast of when the backlog will be done at the current
  throughput.
- Search across goals, tasks, events and transcripts; a command palette (Ctrl+K): new goal, jump to task, kill switch.

**Steer it**
- Drag-and-drop backlog; edit a goal before it starts; priorities.
- Per task: retry, cancel, "send back with my feedback" (a human review comment), reassign to another worker or model.
- Per goal: pause, resume, cancel, raise priority.
- Edit the vision, the team rules, the decisions and the lessons in the browser; view and adapt the role playbooks.
- Onboarding checklist on first start (where the code lives → workers → notifications → first goal) with progress.

**Money and quota**
- Spend per day and week, per runtime, model and project; "the budget runs out at ..." and "quota resets in ..." forecasts.
- Per-task cost breakdown; trends of first-pass rate and cost per merged task over time.
- A/B results with confidence intervals and a "make this the default" button.

**Operations**
- `bin/doctor` in the browser, with a fix button where a fix is safe (restart a service, recreate the proxy, re-login a worker).
- Egress view: allowed and denied hosts per worker, alarms with an acknowledge button.
- Disk usage of runs and images; one-click cleanup.

**Reach**
- Installable app (PWA) with browser push notifications; a phone layout with a bottom tab bar.
- Multi-user: sign-in, roles (viewer / operator / admin), an audit log of who changed what - a candidate for the commercial
  edition.
- Accessibility pass (keyboard navigation, ARIA labels, contrast) and a light/dark switch.
- Translations, if the community wants them (strings in separate files, not in code).

## Maintainers: release checklist
- [ ] `LICENSE`: the copyright holder's legal name.
- [ ] New public repository; `tools/publish-snapshot "Agent-Forge x.y.z"` then `git push <public> public:main` (no private
      history, no private e-mail).
- [ ] The `install` workflow green on GitHub (a fresh VM installs it end to end).
- [ ] Screenshots current (`tools/screenshots.sh`), CHANGELOG and VERSION updated, a GitHub release with notes.
