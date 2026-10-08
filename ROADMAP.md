# Roadmap

Where Agent-Forge is going, roughly in order. Ideas and votes welcome in the issues / discussions.

## Before 1.0 - proof
- [ ] **Real-world validation, published.** One end-to-end run per runtime (Claude Code, Codex, Gemini CLI, a local model with
      real keys / a real server) and per forge (GitHub, GitLab), with the numbers: success rate, cost and time per merged task.
- [ ] **A public repository built entirely by Agent-Forge** (the `examples/demo` tasks CLI), with its pull requests, reviews
      and reworks visible - the best demonstration of what the team does and does not do well.
- [ ] **A benchmark.** A fixed set of goals (or a small SWE-bench-style subset) run regularly: first-pass rate, cost per merged
      task, against a single-agent baseline.
- [ ] **Try it without a VM.** A Docker-only demo mode (`docker compose up`) with sample data and one worker, clearly marked as
      less isolated, for a five-minute look.
- [x] `bin/foreman` and `web/server.py` split into modules and static files (0.11: `bin/lib/foreman/`, `web/dashboard/`,
      `web/static/`).

## Next - capabilities
- [ ] **GitHub / GitLab issues as goals:** issues with a label become goals; the pull requests link back and close them.
- [x] **Local and OpenAI-compatible models** for workers (Ollama, vLLM, LM Studio, OpenRouter): runtimes `compat` (Codex CLI)
      and `aider` (0.11).
- [x] More agent runtimes behind the runtime interface: Aider (0.11). Not yet: Cursor CLI (its backend hosts are not
      documented, so egress cannot be pinned) and OpenHands (brings its own sandbox) - contributions welcome, see
      docs/RUNTIMES.md "Adding a runtime".
- [x] Budgets per project and per worker, a monthly cap (0.11).
- [x] Human-in-the-loop per project: approve the plan, risky tasks, merges (0.11, `APPROVE`).
- [ ] Monorepos and multi-repository projects (a task may span two repositories).
- [x] Planner quality: task sizes learned from history, an estimate before dispatch, `MODEL_ROUTING=auto` (0.11).
- [ ] Cost-aware dispatch: use the estimate to keep a task that would not fit the remaining budget off an API worker.
- [ ] Routing across runtimes, not only Antigravity models (e.g. a cheap local model for docs, Claude for risky code).

## Dashboard
Done in 0.11: live push (Server-Sent Events), why a worker is idle, run replays, goal pages, the project graph with the critical
path and a done-by forecast, search, the "needs you" inbox, task and goal actions, backlog drag-and-drop and editing, the team's
documents and role playbooks in the browser, an onboarding checklist, money and quota with forecasts, per-task cost, weekly
trends, A/B verdicts with intervals and "adopt the winner", health checks with fix buttons, the egress report, disk cleanup.

Still open, roughly by value:
- Installable app (PWA) with browser push notifications, and a phone layout with a bottom tab bar.
- A command palette (Ctrl+K): new goal, jump to a task, kill switch (search already opens with `/`).
- Side-by-side diffs with syntax highlighting; a comment on a diff line goes back to the worker with the line.
- Multi-user: sign-in, roles (viewer / operator / admin), an audit log of who changed what - a candidate for the commercial
  edition.
- Accessibility pass (keyboard navigation, ARIA labels, contrast) and a light/dark switch (now: the system's setting).
- Translations, if the community wants them (strings in separate files, not in code).

## Maintainers: release checklist
- [ ] `LICENSE`: the copyright holder's legal name.
- [ ] New public repository; `tools/publish-snapshot "Agent-Forge x.y.z"` then `git push <public> public:main` (no private
      history, no private e-mail).
- [ ] The `install` workflow green on GitHub (a fresh VM installs it end to end, incl. tests/test_ui.sh).
- [ ] Screenshots current (`tools/screenshots.sh`), CHANGELOG and VERSION updated, a GitHub release with notes.
