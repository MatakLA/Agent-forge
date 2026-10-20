## What and why

<!-- What does this change, and why is it needed? Link the issue or discussion. -->

## How it was checked

- [ ] `make check` passes (`make lint` too, if you touched scripts)
- [ ] I added or updated a test (a bug fix has a test that fails without the fix)
- [ ] I ran `make test` for changes to the supervisor, runtimes or dashboard (needs Docker) - or say why not

## Housekeeping

- [ ] Docs and the `Unreleased` section of `CHANGELOG.md` are updated if behaviour changed
- [ ] Commits are signed off (`git commit -s`) under the Apache-2.0 licence, see CONTRIBUTING.md

## Safety checklist

- [ ] No secrets, tokens, logs or personal data, in files or in command lines
- [ ] `egress/allowlist.txt` is unchanged, or the new host is explained above
- [ ] Container hardening is unchanged (`cap_drop: ALL`, no docker socket, internal network only)
