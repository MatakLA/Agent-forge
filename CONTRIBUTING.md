# Contributing

Thanks for helping. Before you open a pull request:

1. `make check` passes (selftests, no Docker, no model calls); for supervisor changes also `make test`.
2. Code, comments and docs are in English. Keep the style of the surrounding code: standard library only, small functions,
   comments that say *why*.
3. Never add secrets, tokens, real personal data or logs to the repository.

## Contributor license agreement

Agent-Forge is source-available under the Sustainable Use License (`LICENSE`) and also offered under commercial licenses
(`LICENSE_EE.md`). To keep that possible, every contribution needs this agreement. By submitting a contribution (a pull
request, patch or any other change) you agree that:

- you wrote it, or you have the right to submit it under these terms;
- you grant the licensor named in `LICENSE`, and anyone who receives the software from the licensor, a perpetual, worldwide,
  non-exclusive, no-charge, royalty-free, irrevocable license to use, reproduce, modify, publicly display, sublicense and
  distribute your contribution and derivative works of it, under the Sustainable Use License, under commercial licenses,
  or under any other license the licensor chooses;
- you grant the same parties a patent license for your contribution on the same terms;
- you are not expected to provide support for your contribution.

Add this line to the description of your first pull request: `I agree to the contributor license agreement in CONTRIBUTING.md.`
