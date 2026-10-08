# Demo project

`scripts/install.sh --demo` copies `vision.md` to `docs/vision.md`, so the autopilot plans from it as soon as a worker is idle.
Or start by hand with goals like these (dashboard → Overview → New goal, or `bin/foreman goal "..."`):

1. `Create the tasks CLI: add, list, done and rm with a JSON store written atomically, with pytest tests.`
2. `Add due dates to tasks: sorting, overdue marks and a "today" command, with tests.`
3. `Write docs/USAGE.md: a short user guide with an example session.`

Goal 1 is planned into parallel tasks (store, CLI, tests); goals 2 and 3 can run at the same time once 1 is merged. Watch the
Timeline and Tasks tabs (Board, and the Graph with the critical path), and a goal's page (click a goal).

Want a project in another language? Set `TEST_CMD` and `GATE_IMAGE` in Settings → Project (e.g. `npm test --silent` and an image
with Node and your dependencies) and write the vision for it.
