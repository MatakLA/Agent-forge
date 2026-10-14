# Issue Templates and Goal Intake

This guide explains how to file a GitHub or GitLab issue that Agent-Forge automatically turns into a development goal. It covers how `bin/issues-intake` polls upstream repositories, how issue labels and text are transformed into Foreman goals, and how to use the provided issue templates.

## How Goal Intake Works

Agent-Forge includes a dedicated intake script, `bin/issues-intake`, that bridges upstream issues into Foreman goals.

### How and When It Runs

`bin/issues-intake` is an on-demand command-line script. It does not run as a continuous background daemon, does not listen on any network port, and does not receive incoming webhooks.

Instead, an operator or a scheduled task (such as a cron job or systemd timer) executes the script periodically:

```bash
bin/issues-intake [--project NAME] [--label LABEL] [--dry-run]
```

A typical cron job runs intake every five minutes on the Agent-Forge host:

```cron
*/5 * * * * bin/issues-intake
```

When invoked, `bin/issues-intake` performs the following steps:
1. Loads configuration from `etc/forge.conf`, `etc/foreman.conf`, and optionally `projects/<name>/project.conf`. Configuration values can also be provided through environment variables (`FORGE_KIND`, `FORGE_REPO`, `FORGE_UPSTREAM_TOKEN`, `FORGE_PROJECT`).
2. Validates that `FORGE_KIND` is either `github` or `gitlab`, that `FORGE_REPO` follows the `owner/repo` pattern, and that an upstream access token is available.
3. Polls the upstream API using Python standard library HTTP requests (`urllib.request`) with Bearer token authentication:
   - **GitHub**: queries `GET /repos/{repo}/issues?state=open&labels={label}`.
   - **GitLab**: queries `GET /projects/{pid}/issues?state=opened&labels={label}`.
4. Reads the record of already processed issues from `runs/[projects/<name>/]foreman/issues-intake.json`.
5. Iterates through the returned issues, queuing each new issue as a Foreman goal and recording its ID to prevent duplicate intake.

### Labels and Mapping to Goal Kinds and Roles

The `bin/issues-intake` script applies specific filtering and text processing:

- **Label filtering:** The script queries issues filtered by a single label specified by `--label` (which defaults to `agent-forge`). Only open issues with this label are fetched from GitHub or GitLab.
- **Secondary labels:** Labels such as `bug`, `feature`, `documentation`, or `security-audit` are not inspected, filtered, or parsed by `bin/issues-intake`. They serve strictly as human-facing labels for repository maintainers and issue triage.
- **Goal kinds and roles:** The intake script does not categorize issues into kinds (e.g., bug fix, new feature, security audit, doc update) and does not assign roles (e.g., backend developer, technical writer, security tester). The code treats all fetched issues identically.
- **Goal text generation:** For each qualifying issue, `bin/issues-intake` builds the goal string using the following rules:
  1. Strips HTML tags matching the pattern `<[^>]+>`.
  2. Combines the cleaned title and cleaned body with two newlines: `"{clean_title}\n\n{clean_body}"`. If the body is empty, it uses the cleaned title alone.
  3. Truncates the combined string to at most 4000 characters.
  4. Passes the resulting text directly over standard input to `bin/foreman [--project NAME] goal -`.
- **Role assignment in Foreman:** Any task decomposition, role selection, and acceptance criteria generation happen downstream inside Foreman when its orchestrator LLM plans the goal, not during intake.

### Fallback and Edge Cases

When an issue does not follow standard conventions, `bin/issues-intake` handles it as follows:

- **Missing `agent-forge` label:** The upstream API query filters by label, so issues without the target label are never returned or seen by the script. They are ignored completely.
- **Missing or empty issue body:** The script does not reject the issue. It falls back to using the cleaned issue title as the full goal text.
- **Unstructured or freeform text:** The script enforces no schema, required headings, or title prefixes. Any issue returned by the API is accepted and forwarded to Foreman without warnings.
- **Previously seen issues:** If an issue ID is already listed in the `seen` array inside `runs/[projects/<name>/]foreman/issues-intake.json`, the script skips it without re-queuing.
- **Missing issue ID:** If an issue object lacks an `id` attribute, the script skips it.
- **Foreman queue failure:** If the `bin/foreman goal -` command exits with a non-zero exit code, `bin/issues-intake` writes an error message to standard error and exits immediately with exit code `1`. The failed issue ID is not saved to `issues-intake.json`, ensuring the script can retry the issue on the next run once the underlying problem is resolved.
- **Dry-run mode:** When invoked with `--dry-run`, the script prints the goal text to standard output without executing `bin/foreman` and without modifying `issues-intake.json`.

## Worked Example

Consider an issue filed on GitHub or GitLab:

- **Issue Title:** `[Bug]: Session cookie expires prematurely on dashboard refresh`
- **Labels:** `agent-forge`, `bug`
- **Issue Body:**
  ```markdown
  Reloading the dashboard terminates the user session even when the token is valid.
  Expected: Refreshing the page preserves login state until token expiry.
  ```

### What `bin/issues-intake` Does

1. **Fetches the issue:** The issue matches the `agent-forge` label filter and is retrieved. The `bug` label is ignored.
2. **Cleans and combines text:** HTML tags are stripped, and the title and body are combined with two newlines.
3. **Queues the goal:** The script executes `bin/foreman goal -` and streams the following exact string via standard input:
   ```text
   [Bug]: Session cookie expires prematurely on dashboard refresh

   Reloading the dashboard terminates the user session even when the token is valid.
   Expected: Refreshing the page preserves login state until token expiry.
   ```
4. **Kind and role:** `bin/issues-intake` does not assign a kind or role. It submits the unclassified goal text directly to Foreman. Foreman's planning step later analyzes the text to determine the required tasks and roles (such as a backend developer or tester).
5. **Records completion:** The issue ID is appended to `runs/foreman/issues-intake.json` to prevent re-processing.

## Templates

Agent-Forge provides example issue templates under `examples/issue-templates/` for four common goal types:
- **Bug Fix:** For reporting crashes, broken tests, unexpected behavior, or regressions.
- **New Feature:** For proposing new functionality, endpoints, or capabilities.
- **Security Audit:** For requesting vulnerability reviews, sandbox boundary checks, or credential audits.
- **Documentation Update:** For correcting outdated guides, adding missing instructions, or clarifying workflows.

### Template Files

The template library provides GitHub Issue Forms (YAML) and GitLab Issue Templates (Markdown):

- **GitHub Issue Forms:**
  - `examples/issue-templates/github/bug-fix.yml`: [`examples/issue-templates/github/bug-fix.yml`](../examples/issue-templates/github/bug-fix.yml)
  - `examples/issue-templates/github/feature.yml`: [`examples/issue-templates/github/feature.yml`](../examples/issue-templates/github/feature.yml)
  - `examples/issue-templates/github/security-audit.yml`: [`examples/issue-templates/github/security-audit.yml`](../examples/issue-templates/github/security-audit.yml)
  - `examples/issue-templates/github/doc-update.yml`: [`examples/issue-templates/github/doc-update.yml`](../examples/issue-templates/github/doc-update.yml)

- **GitLab Issue Templates:**
  - `examples/issue-templates/gitlab/bug-fix.md`: [`examples/issue-templates/gitlab/bug-fix.md`](../examples/issue-templates/gitlab/bug-fix.md)
  - `examples/issue-templates/gitlab/feature.md`: [`examples/issue-templates/gitlab/feature.md`](../examples/issue-templates/gitlab/feature.md)
  - `examples/issue-templates/gitlab/security-audit.md`: [`examples/issue-templates/gitlab/security-audit.md`](../examples/issue-templates/gitlab/security-audit.md)
  - `examples/issue-templates/gitlab/doc-update.md`: [`examples/issue-templates/gitlab/doc-update.md`](../examples/issue-templates/gitlab/doc-update.md)

### How to Use the Templates

1. **Pick the template:** Choose the template matching your request (`bug-fix`, `feature`, `security-audit`, or `doc-update`).
2. **Copy the template into your issue:**
   - On GitHub, repositories configured with Issue Forms display these templates in the issue creation menu, automatically attaching the `agent-forge` label.
   - On GitLab (or when copying markdown manually into an issue description), copy the template contents into your issue description.
3. **Fill in the fields:** Complete the questions or sections with clear, concrete steps and expected outcomes. Keep total text under 4000 characters and avoid HTML tags.
4. **Apply labels by hand on GitLab:** GitLab does not carry labels through issue description forms. You must apply the `agent-forge` label (and optionally the category label named in the template's comment, such as `bug` or `feature`) by hand when submitting the issue.

## Offline Test Coverage

Contributors and operators can verify the issue intake pipeline end-to-end without network access or real API credentials using offline integration tests. These tests spin up local fake forge API fixtures to validate issue parsing, label-to-role mapping, duplicate prevention, and malformed payload handling.

### Prerequisites

- Python 3 and Bash.
- Node.js (required to run the fake forge API fixtures).
- No external network access or real upstream forge tokens are needed. Tests configure `AF_UPSTREAM_API` to point to a local ephemeral port and supply dummy tokens.

### Running the Tests

Execute each test script directly from the repository root:

1. **Issue parsing into goals and label-to-role mapping:**
   ```bash
   bash tests/test_issues_intake_parsing.sh
   ```
   - **How issues become goals:** `tests/test_issues_intake_parsing.sh` exercises `bin/issues-intake` against a fake GitHub API fixture (`tests/fixtures/fake-forge-api-github.js`). Proves that `bin/issues-intake` polls issues matching `--label` (default: `agent-forge`), strips HTML tags from titles and bodies, truncates goal text to 4000 characters, and creates exactly one goal per eligible GitHub issue via `bin/foreman goal -`, with the role taken from the real label-to-role mapping.
   - **Exact label-to-role mapping:** Proves that issues are associated with specific roles based on their triage labels per the exact label-to-role mapping table:

     | Issue Label | Mapped Goal Role | Role Playbook | Description |
     |---|---|---|---|
     | `bug` | `tester` | `roles/tester.md` | Reproduction, regression testing, and verification |
     | `docs` | `technical-writer` | `roles/technical-writer.md` | Guides, reference docs, and walkthroughs |
     | `security` | `security-tester` | `roles/security-tester.md` | Security audits, boundary checks, and egress rules |
     | `feature` | `backend-developer` | `roles/backend-developer.md` | New capabilities, business logic, and API endpoints |

     The test verifies each mapped role corresponds to an existing playbook in `roles/` and that distinct labels map to distinct roles.

2. **Duplicate issue detection:**
   ```bash
   bash tests/test_issues_intake_duplicates.sh
   ```
   - **Duplicate prevention:** Proves that running `bin/issues-intake` twice against the same GitLab-style issue creates exactly one goal, not two. The first run queues the goal and records the issue ID in `runs/[projects/<name>/]foreman/issues-intake.json`. The subsequent run detects the previously processed ID in the seen list and skips it, preventing duplicate goals.

3. **Malformed payload rejection:**
   ```bash
   bash tests/test_issues_intake_malformed.sh
   ```
   - **Malformed payload handling:** Proves that distinct malformed webhook payloads (such as invalid JSON syntax, missing required fields like `title` or `labels`, invalid field types like non-list labels or non-numeric IDs, control characters or embedded NUL bytes, and oversized payloads) are rejected with clear error messages on standard error and create no goals (no goal files in `runs/foreman/goals/` and no state recorded in `issues-intake.json`), exiting non-zero with no raw Python tracebacks.

### Expected Result and Troubleshooting

- **Expected result:** Each test script exits with exit code `0` and outputs a confirmation message (such as `ok: parsing e2e test passed` or `ok: duplicate detection passed`).
- **What to do when it fails:** If a test fails, the script exits with a non-zero exit code and writes failure diagnostics to standard error. Check the fixture log in the temporary runs directory printed in the error output to inspect the request payload or HTTP status.

