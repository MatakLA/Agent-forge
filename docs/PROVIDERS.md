# Orchestrator providers

The orchestrator makes three kinds of decisions (plan, review, goal check). Each decision tries the providers listed in
`etc/foreman.conf` from left to right until one answers:

```ini
PLAN_PROVIDERS=claude-cli,agy-claude,agy-gemini
REVIEW_PROVIDERS=agy-claude,claude-cli
```
Gemini is not a default reviewer: in practice 64 % of its review calls produced no usable verdict. Antigravity calls now pass
`--json-schema`, which may change that - measure it in the dashboard's Metrics tab before adding `agy-gemini` back.

## Free with subscriptions you already have

| provider | what it uses | notes |
|---|---|---|
| `claude-cli` | Claude Code (`claude -p`) logged in on the Agent-Forge machine - your Claude subscription | best planner; the stable system prompt is cached |
| `agy-claude` | Claude (`AGY_PLAN_MODEL` / `AGY_REVIEW_MODEL`) via Antigravity inside a worker container | uses that worker account's **"Claude and GPT models"** quota; the account with the most left is chosen, exhausted ones are skipped |
| `agy-gemini` | Gemini (`AGY_FALLBACK_MODEL`) via Antigravity inside a worker container | the last resort; uses the same Gemini quota the workers code with |

**Quota reality check.** In practice the Antigravity "Claude and GPT models" bucket is small: on a 4-worker team, reviews and
planning alone used it up on all four accounts within two days. That is why reviews try it first (it is otherwise unused) but
Claude Code stays in the chain, and why workers code with Gemini rather than Claude.

## Paid per token (optional)

Use these if you have API credit and want the queue to keep moving when the subscriptions are exhausted, or if you do not use
Claude Code / Antigravity at all. They bill **per token** - a busy team can easily spend several dollars a day on reviews.

| provider | config | key (environment or `~/.config/sandbox/secrets/<NAME>`, mode 0600) |
|---|---|---|
| `anthropic-api` | `ANTHROPIC_MODEL=<a current Claude model id>` | `ANTHROPIC_API_KEY` |
| `openai-api` | `OPENAI_MODEL=<model>`, optional `OPENAI_API_BASE` (any OpenAI-compatible endpoint: OpenRouter, a local vLLM or Ollama server) | `OPENAI_API_KEY` |
| `gemini-api` | `GEMINI_MODEL=<model>` | `GEMINI_API_KEY` |

Example - subscriptions first, paid Anthropic API only as the last fallback:
```ini
PLAN_PROVIDERS=claude-cli,agy-claude,anthropic-api
REVIEW_PROVIDERS=agy-claude,claude-cli,anthropic-api
ANTHROPIC_MODEL=claude-sonnet-5
```

The paid adapters (`bin/lib/providers.py`) run in the foreman on the Agent-Forge machine, not in a worker, so their hosts do not
go into the worker allow-list, and keys travel in request headers only. Every call is logged to `runs/foreman/llm.jsonl` with
provider, duration and token usage, so you can see what you spend.

A local OpenAI-compatible server (e.g. Ollama with a strong local model) costs nothing per token; set `OPENAI_API_BASE` to it and
any non-empty `OPENAI_API_KEY`. Small local models are noticeably worse at planning and review.
