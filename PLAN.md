# Claude Log Analyzer: Project Plan

## Purpose
Build a local tool that reads Claude Code session logs (`~/.claude/projects/<project>/<session>.jsonl`) and answers three questions:

1. **Token optimization**: which operations consume the most tokens, and what would reduce them?
2. **Harness understanding**: how does the agent loop actually work (tools, threading, subagents, compaction)?
3. **Data exposure**: what content from my machine entered the model's context (and so was sent to Anthropic)?

## Background and constraints (from the originating discussion)
- Logs are JSONL, one record per event: user messages, assistant messages, `tool_use` and `tool_result` blocks, timestamps, model, token usage (input, output, cache creation, cache read).
- Records link via fields like `uuid` / `parentUuid`; subagent work may be flagged (e.g. `isSidechain`). **Field names vary by Claude Code version. Always inspect real records before coding against a schema.**
- Logs are deleted after 30 days by default. Set `"deleteSessionsAfterDays"` in `~/.claude/settings.json` to retain them.
- A streamed assistant response may span several records. **Dedupe by message ID** before summing usage or you will double-count.
- Per-tool-result token counts are not logged. Estimate from text length, or infer from the input-token delta on the following turn.
- Logs are NOT a complete record of API traffic. They omit the system prompt, full tool schemas, auto-loaded context, hook/permission details, and telemetry. For wire-level truth use a logging proxy via `ANTHROPIC_BASE_URL`.
- Logs contain file contents and command output. Treat them as sensitive: process locally, never upload them, redact in any shared report.
- Prior art: `ccusage` (token/cost totals), `claude-code-log` (HTML/TUI browsing). This project's value is **per-action attribution**, not duplicating those.

## Architecture
```
src/
  schema_probe.py    # inspect logs, report record types and field sets per version
  parse.py           # JSONL -> normalized DataFrames (messages, tool_calls, usage)
  attribute.py       # join each tool call to its result size and downstream token delta
  analyze_tokens.py  # reports: top results by size, redundant calls, context growth, cache efficiency
  inspect_harness.py # loop/tool histograms, thread tree, subagent and compaction events
  audit_exposure.py  # search tool results for sensitive paths/patterns; summarize what was read
  report.py          # markdown/HTML output, with redaction option
tests/
  fixtures/          # small synthetic JSONL files (never real logs)
```
Stack: Python 3.11+, pandas, `jq` for ad-hoc inspection. Optional: matplotlib for context-growth plots.

## Milestones

### M0: Schema discovery
- Install `jq` (`sudo apt install jq`).
- Sample 3 to 5 real sessions; run the `schema_probe` skill; record record types, field names, and Claude Code version in `docs/schema-notes.md`.
- Build synthetic fixtures that mirror the observed schema.
- **Done when:** a schema note exists and fixtures parse.

### M1: Parser and normalization
- Parse into three tables: `messages`, `tool_calls` (one row per tool_use, joined to its tool_result), `usage` (one row per unique assistant message ID).
- Handle streamed duplicates, sidechains, and missing fields defensively.
- **Done when:** summed usage on a sample session matches an independent check (e.g. `ccusage`).

### M2: Token attribution
- For each tool call: result character length, estimated tokens, and next-turn input-token delta.
- Reports: top 20 largest results; repeated identical reads/searches; tokens per turn over time; cache read/write ratio; cost by project and by tool.
- **Done when:** report runs across all projects and flags concrete optimization candidates.

### M3: Harness inspection
- Tool-usage histogram; typical tool sequences (search, read, edit, test); conversation tree from `parentUuid`; subagent delegation summary; compaction events and what precedes them.
- **Done when:** one session can be rendered as a readable step-by-step trace.

### M4: Exposure audit
- Inventory of files read and commands run per session; configurable pattern list (client names, credential regexes, sensitive directories); report of which sessions surfaced matches.
- Output suggestions: deny rules, ignore patterns, directories to avoid launching from.
- **Done when:** audit flags a planted fake secret in a fixture and runs on real logs without printing the secret in full.

### M5 (optional): Proxy cross-check
- Run a session through a logging proxy (`ANTHROPIC_BASE_URL`), compare payloads against the JSONL log, and document what the logs miss.

## Risks and mitigations
| Risk | Mitigation |
|---|---|
| Schema changes between versions | Probe first; version-tolerant parsing; fixtures per version |
| Double-counted usage | Dedupe on message ID; validate against `ccusage` |
| Sensitive data leaking into reports | Redaction on by default; reports truncate matches |
| Logs deleted before analysis | Raise `deleteSessionsAfterDays`; optionally copy logs to a private archive |

## Open questions
- Which Claude Code versions are present in the existing logs?
- Is OpenTelemetry export available in the installed version, and would it be a better token source than log parsing?
- Which projects should be in scope for the first exposure audit?
