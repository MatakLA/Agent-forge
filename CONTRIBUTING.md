# Contributing

Thanks for helping. Agent-Forge is a safety-first project: changes that make the sandbox, the egress proxy or the gate weaker
need a very good reason. Everything else - bug fixes, docs, runtimes, translations, tests - is welcome.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Questions go to
[Discussions](https://github.com/MatakLA/Agent-forge/discussions) (see [SUPPORT.md](SUPPORT.md)); security problems go to a
[private advisory](SECURITY.md), never to a public issue.

## Getting set up

You do not need Docker, an AI account or a VM to work on most of the code.

```bash
git clone https://github.com/MatakLA/Agent-forge.git
cd Agent-forge
make check          # about a minute; no Docker, no model calls, no network
```

`make check` (`bash tests/check.sh`) is what CI runs: syntax checks of every script, the foreman self-test, and the unit and
shell tests with fake Docker and fake model APIs. It needs Python 3.10+ and bash. If `node` is installed, the dashboard
JavaScript and the issue-intake tests run too.

Heavier tests need Docker and are run by the nightly/weekly workflows; run them on a throw-away VM, not on a machine you care
about:

| command | what it covers | needs |
|---|---|---|
| `make test` | supervisor, real agent CLIs against a fake model API, the dashboard page in a real DOM, the demo container | Docker |
| `./forge install --check` | prints what the installer would do, changes nothing | - |
| `./forge doctor` | end-to-end health check of an installed copy | an install |

## Making a change

1. Open an issue or a discussion first for anything bigger than a small fix, so you do not build something we cannot merge.
2. Branch from `main` and keep the change focused: one topic per pull request.
3. Add or update a test. A bug fix comes with a test that fails without the fix. Tests live in `tests/`, are registered in
   `tests/check.sh`, and must not need the network, Docker or secrets unless they are in the `make test` group.
4. Run `make check`. For changes to the supervisor, runtimes or dashboard also run `make test` where you can.
5. Update the docs and the `Unreleased` section of [CHANGELOG.md](CHANGELOG.md) when behaviour changes.
6. Open the pull request and fill in its checklist.

## Style

- Code, comments, commit messages and docs are in English. Comments say *why*, not *what*.
- Python: standard library only (the dashboard and the foreman must run on a bare Python install), small functions, no new
  dependencies without a discussion.
- Shell: `bash`, `set -euo pipefail` where it makes sense, quote everything, no `curl | sh`. `shellcheck -S warning` is clean
  for the scripts that CI lints.
- Never add secrets, tokens, real personal data, logs or `etc/*.conf` to the repository. Never print a secret to a terminal or
  a log; scripts that handle credentials read them from a file with mode 600 or from hidden input.
- Anything that can run inside a worker must keep the container hardening (`cap_drop: ALL`, no docker socket, internal
  network only). The test `tests/test_egress_security.sh` and `tests/test_install_security.sh` guard the main invariants.

## Commits and licence

Agent-Forge is open source under the Apache License 2.0 ([LICENSE](LICENSE)). By submitting a contribution you agree that it is
licensed under the same licence (section 5), and that you have the right to submit it. Confirm that with a sign-off on each
commit (Developer Certificate of Origin):

```bash
git commit -s -m "fix: do not retry a 401 forever"
```

Commit messages: a short imperative subject (`fix:`, `feat:`, `docs:`, `test:`, `chore:` prefixes are welcome), then a body that
explains why.

## Using AI to write your contribution

Fine - the project itself is written that way. You remain the author: you have read and understood every line, you ran the
tests, and the pull request description is yours. Do not paste secrets or private data into any AI tool while working on a
contribution. Pull requests that look like unreviewed bulk output will be closed.

## Releases

Maintainers cut a release like this:

1. Rename `Unreleased` in [CHANGELOG.md](CHANGELOG.md) to `X.Y.Z - YYYY-MM-DD`, set the same number in `VERSION`, commit.
2. `git tag vX.Y.Z && git push origin main vX.Y.Z`.

The release workflow then refuses a tag that does not match `VERSION`, runs the checks, publishes the demo image
`ghcr.io/matakla/agent-forge-demo` (`:X.Y.Z`, `:X.Y`, `:latest`) with an SBOM and signed build provenance, and creates the GitHub
release from that CHANGELOG section with the SBOM attached. To rehearse the image build without publishing anything, run the
`release` workflow by hand on a branch (Actions → release → Run workflow): it builds both platforms and pushes nothing.
`./forge update` follows `main`, not the tags: tags are for people who want a fixed version (`FORGE_REF=vX.Y.Z` with the
installer).
