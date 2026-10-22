<!--
Agent-Forge issue template: New Feature (GitLab Issue Template)

Labels to apply by hand:
- agent-forge: REQUIRED. bin/issues-intake polls issues matching label 'agent-forge' by default.
- feature: OPTIONAL. Triage label for people, not a filter and not read by bin/issues-intake.

Title recommendation:
Prefix the issue title with '[Feature]: ' (e.g., '[Feature]: Add JSON output flag to bin/status').

Grounding in bin/issues-intake:
- Goal formation: bin/issues-intake strips HTML tags (<[^>]+>), truncates the combined title and body
  to 4000 characters, and pipes the result directly to `bin/foreman goal -` over stdin.
- Section matching: bin/issues-intake does NOT parse or match individual body section names.
  Every section heading below is flagged as a human-only hint to structure feature requirements
  and context for Foreman's AI planner.
-->

## Problem statement

<!-- Human-only hint: bin/issues-intake does not read this section name; it forwards the full body into the goal text. -->
What problem does this solve, and who is affected (e.g., new user, operator, contributor)?

## Proposed behaviour

<!-- Human-only hint: bin/issues-intake does not read this section name. -->
Describe the desired behaviour or outcome. How should the feature work once implemented?

## Affected area

<!-- Human-only hint: bin/issues-intake does not read this section name. -->
File paths, commands, or components affected (if known), e.g., `bin/foreman`, `web/dashboard/`, `scripts/install.sh`.

## Additional context

<!-- Human-only hint: bin/issues-intake does not read this section name; it strips HTML tags and truncates total goal text (title + body) to 4000 characters. -->
Optional constraints, alternatives considered, or acceptance criteria. Keep the total issue body concise (under 4000 characters) and avoid HTML tags.
