<!--
Agent-Forge issue template: Security Audit (GitLab Issue Template)

Labels to apply by hand:
- agent-forge: REQUIRED. bin/issues-intake polls issues matching label 'agent-forge' by default.
- security: OPTIONAL. Triage label, not a filter; bin/issues-intake maps it to the role 'security-tester' and records that role on the goal.

Title recommendation:
Prefix the issue title with '[Security Audit]: ' (e.g., '[Security Audit]: Audit argv handling in bin/dispatch').

Grounding in bin/issues-intake:
- Goal formation: bin/issues-intake strips HTML tags (<[^>]+>), truncates the combined title and body
  to 4000 characters, and pipes the result directly to `bin/foreman goal -` over stdin.
- Section matching: bin/issues-intake does NOT parse or match individual body section names.
  Every section heading below is flagged as a human-only hint to structure security audit requirements
  and context for Foreman's AI planner.

Roles and safety:
A security-audit goal is picked up by the `security-tester`/`security-developer` roles and no real exploit against anything outside this repository should ever be pasted into the issue (per SECURITY.md).
-->

> **Warning**: A security-audit goal is picked up by the `security-tester`/`security-developer` roles and no real exploit against anything outside this repository should ever be pasted into the issue (per SECURITY.md). Never paste real secrets, production credentials, or external exploit payloads into the issue.

## Component or module

<!-- Human-only hint: bin/issues-intake does not read this section name; it forwards the full body into the goal text. -->
Which component, module, or file path should be audited (e.g., `bin/foreman`, `bin/issues-intake`, `web/dashboard/`, `egress/`)?

## Suspected concern

<!-- Human-only hint: bin/issues-intake does not read this section name. -->
Describe the suspected or known security concern, boundary weakness, or threat model question to investigate. Warning: Do not paste real exploits or secrets.

## Rough severity guess

<!-- Human-only hint: bin/issues-intake does not read this section name. -->
Rough severity estimate: Low, Medium, High, or Critical.

## Additional context

<!-- Human-only hint: bin/issues-intake does not read this section name; it strips HTML tags and truncates total goal text (title + body) to 4000 characters. -->
Optional constraints, reproduction hints, or notes. Keep the total issue body concise (under 4000 characters), avoid HTML tags, and never paste real secrets or exploits.
