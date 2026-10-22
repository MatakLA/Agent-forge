<!--
Agent-Forge issue template: Documentation Update (GitLab Issue Template)

Labels to apply by hand:
- agent-forge: REQUIRED. bin/issues-intake polls issues matching label 'agent-forge' by default.
- docs: OPTIONAL. Triage label for people, not a filter and not read by bin/issues-intake.

Title recommendation:
Prefix the issue title with '[Doc]: ' (e.g., '[Doc]: Fix configuration path in docs/SETUP.md').

Grounding in bin/issues-intake:
- Goal formation: bin/issues-intake strips HTML tags (<[^>]+>), truncates the combined title and body
  to 4000 characters, and pipes the result directly to `bin/foreman goal -` over stdin.
- Section matching: bin/issues-intake does NOT parse or match individual body section names.
  Every section heading below is flagged as a human-only hint to structure documentation updates
  and context for Foreman's AI planner.
-->

## Affected documentation page

<!-- Human-only hint: bin/issues-intake does not read this section name; it forwards the full body into the goal text. -->
Which doc/page is wrong, missing, or confusing? Provide a repository path (e.g., `docs/SETUP.md`, `README.md`) or URL within this repo.

## What it currently says

<!-- Human-only hint: bin/issues-intake does not read this section name. -->
What does the documentation currently say (if anything)? Quote or describe the existing text. Leave blank if requesting new documentation.

## What it should say instead

<!-- Human-only hint: bin/issues-intake does not read this section name. -->
What should the documentation say instead? Provide the corrected explanation, updated code samples, or proposed text.

## Additional context

<!-- Human-only hint: bin/issues-intake does not read this section name; it strips HTML tags and truncates total goal text (title + body) to 4000 characters. -->
Optional target audience notes, related PRs, or constraints. Keep the total issue body concise (under 4000 characters) and avoid HTML tags.
