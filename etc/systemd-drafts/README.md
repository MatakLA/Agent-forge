# systemd timer drafts (NOT installed, NOT enabled)
Nothing here is active. Scheduling is only a design; installing or enabling any of it is a separate owner decision.

- `sandbox-report.service` / `.timer`: the read-only daily report (`bin/report daily`, which also rotates the proxy logs) once a day as a user timer, e.g. `systemctl --user enable --now sandbox-report.timer` after
  `loginctl enable-linger dev`. Copy to `~/.config/systemd/user/` yourself if you want it.
- `sandbox-unattended.service` / `.timer` (added 2026-09-26 after the bounded unattended pilot): would start ONE bounded unattended orchestrator session (`bin/unattended-run`, profile `dontAsk`, budget and timeout caps, kill switch respected) at most once a day.
  It does NOT work today on purpose (the prompt file it names does not exist) and has prerequisites listed in the service file and in `v7/UNATTENDED.md`. `Persistent=false`: no catch-up after a reboot.
- Never add a timer that runs `bin/dispatch` directly, and never one that starts anything without the caps of `bin/unattended-run`.
