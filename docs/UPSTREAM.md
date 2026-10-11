# GitHub and GitLab

By default a project lives only on the internal Forgejo. If its real home is GitHub or GitLab (cloud or self-hosted),
connect it - in the dashboard (it asks once for a new project; later Settings → Project → "Where the code lives"), or by hand:

```ini
# projects/<name>/project.conf, or etc/foreman.conf for the default project
FORGE_KIND=github                # forgejo | github | gitlab
UPSTREAM_REPO=acme/shop          # GitLab: group/project, also nested groups
UPSTREAM_URL=https://github.com  # https://gitlab.com, or your own server
UPSTREAM_BRANCH=main
UPSTREAM_MERGE=merge             # merge | squash | rebase
```
```bash
printf '%s' "$TOKEN" | bin/secret-set github.token     # or gitlab.token
bin/upstream check                                      # the token reaches the repository and may push
bin/upstream import                                     # upstream main becomes the internal main
```

## How it works
Workers never talk to GitHub or GitLab and never hold that token - their egress allow-list does not change. They keep working
on the internal Forgejo, and the gate tests every branch there as before. The orchestrator, on the Agent-Forge machine:
1. syncs upstream `main` into the internal `main` every minute (fast-forward only - a diverged internal main is never
   overwritten; you get an alert and `bin/upstream status`);
2. after a branch passed the gate and the review, pushes it upstream and opens a pull request (merge request), with the review
   and the gate output in its description;
3. merges it (`AUTO_MERGE=1`). Upstream's rules apply as for any contributor: required checks, required reviews, CODEOWNERS.
   If upstream cannot merge yet, the task waits in "Merging" and is checked every two minutes; GitLab merges by itself when the
   pipeline succeeds. With `AUTO_MERGE=0` the pull request waits for you.
   When main moved upstream and the branch conflicts, it is rebased and tested again; a pull request closed without merging
   blocks the task.

Dependent tasks start only after the merge, so they always build on the merged code.

## Tokens (least privilege)
- **GitHub**: a fine-grained personal access token (or a GitHub App token) for **only this repository**, permissions
  *Contents: read and write* and *Pull requests: read and write*. If main is protected with required reviews, the token's
  user cannot approve its own pull requests - use `AUTO_MERGE=0` (you merge) or let a reviewer approve.
- **GitLab**: a project access token, role *Developer* (or *Maintainer* if main only allows maintainers to merge), scopes
  *api* and *write_repository*.

The token is stored in the secrets directory (0600) and given to git through environment variables, never on a command line.
