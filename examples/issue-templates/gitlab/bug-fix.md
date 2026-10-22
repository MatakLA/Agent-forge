<!--
Agent-Forge issue template: Bug Fix (GitLab Issue Template)

Labels to apply by hand:
- agent-forge: REQUIRED. bin/issues-intake polls issues matching label 'agent-forge' by default.
- bug: OPTIONAL. Triage label for people, not a filter and not read by bin/issues-intake.

Title recommendation:
Prefix the issue title with '[Bug]: ' (e.g., '[Bug]: bin/status fails with KeyError').

Grounding in bin/issues-intake:
- Goal formation: bin/issues-intake strips HTML tags (<[^>]+>), truncates the combined title and body
  to 4000 characters, and pipes the result directly to `bin/foreman goal -` over stdin.
- Section matching: bin/issues-intake does NOT parse or match individual body section names.
  Every section heading below is flagged as a human-only hint to structure reproduction details
  and context for Foreman's AI planner.
-->

## Observed behaviour

<!-- Human-only hint: bin/issues-intake does not read this section name; it forwards the full body into the goal text. -->
What happened? Describe the unexpected behaviour, traceback, or failure.

## Steps to reproduce

<!-- Human-only hint: bin/issues-intake does not read this section name. -->
Numbered steps to reproduce the issue:
1. 
2. 
3. 

## Expected behaviour

<!-- Human-only hint: bin/issues-intake does not read this section name. -->
What should have happened instead.

## Affected area

<!-- Human-only hint: bin/issues-intake does not read this section name. -->
File paths, commands, or components affected (if known), e.g., `bin/foreman`, `bin/lib/foreman/pool.py`.

## Additional context

<!-- Human-only hint: bin/issues-intake does not read this section name; it strips HTML tags and truncates total goal text (title + body) to 4000 characters. -->
Optional logs, stack traces, or environment notes. Keep the total issue body concise (under 4000 characters) and avoid HTML tags.
