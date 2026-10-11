# Roles

Every task gets exactly one role. A role is a playbook in `roles/<slug>.md`: mission, what the role owns, how it works, what it never
does, and its definition of done. The planner sees the catalogue below and picks roles; the worker receives the full playbook as part
of its task; the reviewer receives the role's definition of done and judges the deliverable by it.

Roles are not limited to code: a goal like "write the user guide and translate it" is planned as technical-writer + translator
tasks, reviewed for fitness for its readers, and merged like any other branch.

## Engineering

| role | mission |
|---|---|
| [`backend-developer`](../roles/backend-developer.md) - Backend developer | build server-side logic, data storage and APIs that are correct, small and fully tested. |
| [`frontend-developer`](../roles/frontend-developer.md) - Frontend developer | build the user interface - fast, accessible, mobile-first, and wired to the real API shapes. |
| [`fullstack-developer`](../roles/fullstack-developer.md) - Fullstack developer | deliver a feature end to end - storage, API and UI - as one coherent slice. |
| [`devops-engineer`](../roles/devops-engineer.md) - DevOps engineer | make the project easy and safe to build, run, configure and deploy. |
| [`performance-engineer`](../roles/performance-engineer.md) - Performance engineer | make it measurably faster or lighter without changing behaviour. |
| [`refactoring-specialist`](../roles/refactoring-specialist.md) - Refactoring specialist | improve structure and readability with zero behaviour change. |

## Quality and security

| role | mission |
|---|---|
| [`tester`](../roles/tester.md) - Tester (QA engineer) | find the bugs before users do and pin correct behaviour down with tests. |
| [`security-developer`](../roles/security-developer.md) - Security developer | make the code safe by construction - fix vulnerabilities with the smallest correct change and prove each fix with a test. |
| [`security-tester`](../roles/security-tester.md) - Security tester (penetration tester) | attack the module like an adversary and leave behind tests that prove what holds and what does not. |
| [`accessibility-specialist`](../roles/accessibility-specialist.md) - Accessibility specialist | make every screen usable with keyboard, screen reader, zoom and low vision. |
| [`code-reviewer`](../roles/code-reviewer.md) - Code reviewer | judge whether a change does what its task asked, safely, with honest tests - this is the role the orchestrator plays. |

## Product, design and content

| role | mission |
|---|---|
| [`product-manager`](../roles/product-manager.md) - Product manager (spec writer) | turn a fuzzy wish into a precise, testable specification the team can build without guessing. |
| [`ux-designer`](../roles/ux-designer.md) - UX / UI designer | design flows and screens people understand at first glance, delivered as artefacts the frontend can build from. |
| [`technical-writer`](../roles/technical-writer.md) - Technical writer | write documentation people can follow without help. |
| [`copywriter`](../roles/copywriter.md) - Copywriter (UX writing / content) | write the words users read - clear, kind, short, in the product's voice. |
| [`editor`](../roles/editor.md) - Editor / proofreader | make existing text correct, consistent and easy to read without changing its meaning. |
| [`translator`](../roles/translator.md) - Translator / localiser | make the product read as if it were written in the target language. |

## Analysis

| role | mission |
|---|---|
| [`researcher`](../roles/researcher.md) - Researcher (offline analyst) | answer a question with evidence from what is in the repository - code, data, docs - and say how sure you are. |
| [`data-analyst`](../roles/data-analyst.md) - Data analyst | turn data in the repository into correct numbers and clear conclusions. |

## Adding or changing a role

Create `roles/<slug>.md` with this shape - the first line is the title, the `**Mission:**` line feeds the catalogue, and the
`## Definition of done` section is what the reviewer checks:

```markdown
# Data engineer
**Mission:** move data between systems reliably and prove it with tests.

## You own
...
## How you work
1. ...
## Never
...
## Definition of done
- ...
```

The planner can use it on the next call; no restart is needed. Keep playbooks short and concrete - every line is sent with every
task of that role.
