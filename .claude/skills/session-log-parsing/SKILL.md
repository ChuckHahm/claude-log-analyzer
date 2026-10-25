---
name: session-log-parsing
description: Inspect and parse Claude Code session logs (JSONL files under ~/.claude/projects/) into normalized pandas tables. Use whenever the task involves reading, probing, or loading Claude Code logs, session history, .jsonl transcripts, or when another log-analysis skill needs structured data, even if the user doesn't mention JSONL.
---

# Session Log Parsing

## Locations
- `~/.claude/projects/<project-dir>/<session-id>.jsonl`; the project dir is the working path with slashes turned into dashes.
- Retention defaults to 30 days; check `~/.claude/settings.json` for `cleanupPeriodDays`.

## Step 1: Probe the schema (always do this first)
Field names vary by version. Never hardcode without checking.
```bash
f=<path-to-session.jsonl>
jq -r '.type' "$f" | sort | uniq -c            # record types
jq -c 'keys' "$f" | sort | uniq -c             # field sets
head -n 1 "$f" | jq .                          # one full record
```
Record findings (version, types, key fields) in `docs/schema-notes.md`.

## Step 2: Normalize into three tables
- **messages**: one row per record: uuid, parentUuid, type/role, timestamp, sidechain flag, session, project.
- **tool_calls**: one row per `tool_use` block (id, name, input) joined to its `tool_result` by tool_use id (result text length, is_error).
- **usage**: one row per **unique assistant message ID**: model, input, output, cache_creation, cache_read.

## Rules
- Content may be a string or a list of blocks; handle both.
- A streamed response can appear as multiple records. Keep one usage row per message ID (take the last or max, and document which).
- Tolerate missing fields; never crash on an unknown record type. Count and report skipped records.
- Skip blank or truncated trailing lines.

## Validation
Compare summed usage against `ccusage` (if installed) or a hand check on one session before trusting aggregates.

## Output
Write tables to `data/` as parquet or CSV (gitignored). Print a summary: sessions, records, skipped records, date range.
