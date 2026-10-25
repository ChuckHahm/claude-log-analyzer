# Claude Log Analyzer

Local analysis of Claude Code session logs (`~/.claude/projects/**/*.jsonl`). See `PLAN.md` for goals, milestones, and constraints.

## Rules
- **Inspect before assuming.** Log field names vary by Claude Code version. Run the `session-log-parsing` skill's probe step before writing code against any field.
- **Privacy first.** Logs contain file contents and command output. Process locally only. Never commit real logs. Use synthetic fixtures in `tests/fixtures/`. Reports redact matches by default.
- **Dedupe usage by message ID** before aggregating tokens.
- **Estimated vs. logged.** Label per-tool-result token figures as estimates.
- Python 3.11+, pandas. Keep functions small and tested against fixtures.

## Skills (in `.claude/skills/`)
- `session-log-parsing`: probe schema, normalize JSONL into tables
- `token-usage-analysis`: attribute tokens to actions and find optimizations
- `harness-inspection`: reconstruct the agent loop, threading, subagents, compaction
- `data-exposure-audit`: determine what local content entered model context

## Caveat to repeat in any exposure-related output
Logs approximate what was sent to the API but omit the system prompt, tool schemas, auto-loaded context, and telemetry. For exact traffic, use a logging proxy.
