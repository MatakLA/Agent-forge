# Improvement Ideas

This document collects concrete improvement ideas to make Agent-Forge more versatile, observable, cost-effective, and extensible for teams using any AI provider or local model server. It functions as a proposal backlog for the project owner: each idea remains in `proposed` status until the owner explicitly reviews, prioritizes, and marks it `approved`, at which point the team can specify, schedule, and implement it.

## 1. Interactive First-Run Setup Wizard
- **Problem**: Initial configuration requires running multiple manual CLI commands and manually copying example files.
- **Proposal**: Add an interactive terminal wizard (`bin/setup`) that guides the user through provider selection, generates `foreman.conf` and `limits.conf`, sets up secrets, and launches the first worker.
- **Effort**: M (1–3 days)
- **Benefits**: New users of any AI provider can set up and validate a working installation in under five minutes.
- **Risks**: Interactive prompts could fail in headless or automated CI environments if non-interactive flags are not supported.
- **Status**: proposed

## 2. Docker-Only Self-Contained Demo Environment
- **Problem**: Evaluating Agent-Forge currently requires provisioning a dedicated Ubuntu virtual machine and running host-level setup scripts.
- **Proposal**: Provide a single `docker compose` demo bundle featuring the web dashboard, a mock model API, and an ephemeral git forge to test the full lifecycle.
- **Effort**: M (1–3 days)
- **Benefits**: Prospective users on macOS, Windows, or Linux can experience the orchestrator and worker loop without dedicating hardware.
- **Risks**: Running worker sandbox containers inside a Docker-based demo environment requires Docker-in-Docker or volume socket mounts.
- **Status**: proposed

## 3. Per-Task Token and Cost Breakdown
- **Problem**: Operators using multiple model providers cannot easily see token usage, cache hit ratios, and exact USD spend attributed to individual tasks and roles.
- **Proposal**: Record structured token metrics and calculate financial costs per task across all runtimes, rendering a detailed summary table in the dashboard and `bin/report`.
- **Effort**: M (1–3 days)
- **Benefits**: Teams can identify expensive tasks, evaluate prompt cache efficiency, and compare provider pricing on real tasks.
- **Risks**: Local models and runtimes that do not report token counts will require graceful handling for unknown costs.
- **Status**: proposed

## 4. Replayable LLM Interaction and Event Inspector
- **Problem**: Debugging worker hallucinations or syntax failures requires manually grepping raw container logs and supervisor transcripts.
- **Proposal**: Add a timeline-scrubbing interaction inspector in the dashboard to review each prompt, tool call, output, and diff step by step.
- **Effort**: M (1–3 days)
- **Benefits**: Developers can quickly pinpoint the exact prompt or tool output that triggered a failure across any agent runtime.
- **Risks**: Large transcript payloads can slow down browser rendering unless paginated or virtualized.
- **Status**: proposed

## 5. Per-Goal and Per-Project Spend Hard Caps
- **Problem**: Runaway agent rework loops can quickly exhaust API quotas and incur unexpected billing charges.
- **Proposal**: Add configurable spend limits in `etc/limits.conf` (`MAX_GOAL_COST_USD` and `MAX_DAILY_COST_USD`) that pause dispatch when exceeded.
- **Effort**: S (< 1 day)
- **Benefits**: Project owners avoid billing surprises across all paid model providers and can safely run unattended tasks.
- **Risks**: Urgent tasks might be unexpectedly paused mid-run if caps are configured too conservatively.
- **Status**: proposed

## 6. Pre-Flight Task Cost and Token Estimation
- **Problem**: Project owners approving goal plans have no estimate of expected token consumption or cost before execution begins.
- **Proposal**: Compute and display an estimated token and cost range for each task in the goal plan based on historical role and codebase metrics.
- **Effort**: M (1–3 days)
- **Benefits**: Owners can make informed trade-offs regarding task scope and model selection before committing funds.
- **Risks**: Novel tasks with unexpected refactoring requirements could deviate significantly from initial estimates.
- **Status**: proposed

## 7. Web UI Egress Allow-List Manager and Live Audit
- **Problem**: Managing outbound egress host allow-lists and diagnosing blocked network connections requires SSH access and manual configuration file edits.
- **Proposal**: Provide a dashboard panel displaying real-time blocked connection attempts with one-click actions to allow or deny hostnames.
- **Effort**: M (1–3 days)
- **Benefits**: Administrators can securely unblock necessary package registries or API endpoints without terminal access.
- **Risks**: Accidental operator approval of overly broad domain patterns could weaken sandbox isolation.
- **Status**: proposed

## 8. Automated Pre-Commit Secret and Credential Scanning
- **Problem**: Worker agents might inadvertently commit sensitive API keys, private tokens, or credentials into repository branches.
- **Proposal**: Add an offline pattern-matching secret scanner to `bin/review-branch` and the merge gate that rejects commits containing credentials.
- **Effort**: S (< 1 day)
- **Benefits**: Protects against accidental credential leaks to Forgejo or upstream repositories across all worker agents.
- **Risks**: False positives on test fixtures or dummy secrets could block valid merges without an ignore mechanism.
- **Status**: proposed

## 9. Golden Task Benchmark Suite for Runtimes
- **Problem**: There is no standardized way to verify whether a new model provider or runtime can reliably edit code and pass repository gates.
- **Proposal**: Build a suite of reproducible coding tasks that runs against any runtime to benchmark task success rate, execution time, and token efficiency.
- **Effort**: L (> 3 days)
- **Benefits**: Provides objective, vendor-neutral benchmarks to help users choose the best AI models for their budget and stack.
- **Risks**: Maintaining deterministic benchmark test harnesses across evolving model versions requires constant maintenance.
- **Status**: proposed

## 10. Regression Suite for Role Playbooks
- **Problem**: Modifying instructions in `roles/*.md` can cause subtle regressions in worker role boundaries and deliverable quality.
- **Proposal**: Introduce an automated evaluation test that runs sample tasks against updated role playbooks to verify adherence to constraints and definitions of done.
- **Effort**: M (1–3 days)
- **Benefits**: Playbook authors can improve prompts and role guidelines with confidence that existing behaviors do not regress.
- **Risks**: Evaluating prompt compliance without human review may require an automated LLM evaluation step.
- **Status**: proposed

## 11. GitHub and GitLab Issue-to-Goal Importer
- **Problem**: Project owners must manually transfer issues from external issue trackers into Agent-Forge goals via the dashboard or CLI.
- **Proposal**: Add an issue synchronization worker that converts tagged GitHub or GitLab issues into foreman goals and posts back status comments on completion.
- **Effort**: L (> 3 days)
- **Benefits**: Integrates Agent-Forge into existing development workflows, allowing teams to trigger autonomous work directly from their issue tracker.
- **Risks**: Malicious or unauthorized issues on public repositories could trigger unwanted runs without strict author permission checks.
- **Status**: proposed

## 12. Interactive Chat Notifications for Blocked Tasks
- **Problem**: When a task blocks on an owner question, operators receive only passive notifications and must open the dashboard to reply.
- **Proposal**: Enhance Slack and Discord webhooks with interactive action buttons and slash commands to answer questions and approve plans directly.
- **Effort**: M (1–3 days)
- **Benefits**: Project owners can unblock tasks quickly from their phones or desktop chat clients without opening the dashboard.
- **Risks**: Handling webhook callbacks requires an accessible ingress endpoint and signature verification.
- **Status**: proposed

## 13. Pre-Built Project Starter Templates
- **Problem**: New projects require manual authoring of `vision.md`, `team-rules.md`, gate commands, and role overrides for common tech stacks.
- **Proposal**: Ship pre-configured project templates for popular stacks (e.g. Python FastAPI, Node.js React, Go CLI, Rust) with tailored rules and gate configurations.
- **Effort**: S (< 1 day)
- **Benefits**: Users can initialize new repositories with best-practice AI guidelines and ready-to-use testing commands in seconds.
- **Risks**: Stale dependencies or test commands in templates could cause initial gate failures if not kept up to date.
- **Status**: proposed

## 14. Custom Role Playbook Templates per Project
- **Problem**: Specialized projects lack domain-specific roles and must redefine them manually in each repository.
- **Proposal**: Provide a template directory of domain-specific roles (e.g. `data-engineer`, `api-designer`, `docs-curator`) that can be imported into project role directories.
- **Effort**: S (< 1 day)
- **Benefits**: Enables specialized engineering teams to adopt tailored roles with precise definitions of done immediately.
- **Risks**: A proliferation of niche roles can confuse the planner when assigning tasks if role boundaries overlap.
- **Status**: proposed

## 15. Configurable Lifecycle Hooks and Post-Merge Webhooks
- **Problem**: Users cannot easily trigger custom scripts or external CI/CD pipelines when tasks finish or branches are merged into main.
- **Proposal**: Add execution hooks in `etc/foreman.conf` (`POST_TASK_HOOK`, `POST_MERGE_HOOK`) that run project scripts with task metadata passed via environment variables.
- **Effort**: M (1–3 days)
- **Benefits**: Allows custom integration with external continuous deployment pipelines, linter checks, and notification systems.
- **Risks**: Poorly written or hanging hook scripts could block the foreman loop unless bounded by strict timeouts.
- **Status**: proposed

## 16. Custom Project Gate Scripts and Test Environments
- **Problem**: Projects with complex polyglot builds or multi-service test suites cannot easily configure customized offline verification gates.
- **Proposal**: Allow projects to specify arbitrary custom gate scripts and container mount requirements via `GATE_SCRIPT` in project configuration.
- **Effort**: M (1–3 days)
- **Benefits**: Supports complex verification workflows including integration test databases, linters, and typecheckers before branch merge.
- **Risks**: Slow gate scripts will increase task turnaround time and queue latency across the worker pool.
- **Status**: proposed

## 17. Cost-Aware Dynamic Model Selection
- **Problem**: Small, mechanical tasks use the same expensive frontier models as complex architectural tasks, wasting budget.
- **Proposal**: Implement a dynamic router that assigns tasks to models based on task complexity and role, using lightweight or local models for small changes.
- **Effort**: L (> 3 days)
- **Benefits**: Reduces overall operating expenses significantly across multi-provider setups while preserving quality on complex code changes.
- **Risks**: Incorrect complexity classification could assign difficult tasks to smaller models, resulting in rework loops.
- **Status**: proposed

## 18. Local Model Auto-Discovery and Configuration
- **Problem**: Configuring local inference servers (Ollama, vLLM, LM Studio) requires manual network discovery, endpoint typing, and parameter tuning.
- **Proposal**: Add a `bin/model-discover` utility and dashboard detection tool that queries local network endpoints for running model servers and configures worker runtimes automatically.
- **Effort**: M (1–3 days)
- **Benefits**: Enables users with local GPUs to onboard zero-cost, private models with a single click.
- **Risks**: Local models with insufficient context lengths or weak instruction following may struggle without minimum capability warnings.
- **Status**: proposed

## 19. Mobile-Responsive Task Management and Quick Actions
- **Problem**: The web dashboard is difficult to use on mobile devices, making it hard to monitor progress or approve changes on the go.
- **Proposal**: Redesign dashboard views with responsive mobile CSS, touch-friendly task cards, and bottom quick-action sheets for approvals and emergency stops.
- **Effort**: M (1–3 days)
- **Benefits**: Solo owners and operators can monitor team progress and unblock tasks comfortably from mobile phones.
- **Risks**: Adapting dense information like multi-column diffs and real-time logs to small screens requires careful responsive design.
- **Status**: proposed

## 20. Live Terminal and Worker Shell Viewer in Dashboard
- **Problem**: Diagnosing container-level environment issues or stuck processes requires switching away from the browser to run manual docker exec commands.
- **Proposal**: Embed a read-only terminal viewer in the dashboard's Live tab that streams the container's TTY output directly over WebSockets or SSE.
- **Effort**: M (1–3 days)
- **Benefits**: Operators can inspect live terminal output, command progress, and environment diagnostics without SSH access.
- **Risks**: Streaming raw terminal control codes requires client-side ANSI parsing to avoid corrupted UI displays.
- **Status**: proposed
