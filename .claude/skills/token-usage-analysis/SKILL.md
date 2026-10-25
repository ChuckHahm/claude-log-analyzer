---
name: token-usage-analysis
description: Attribute token usage in Claude Code sessions to specific actions and recommend optimizations. Use when the user asks about token costs, context bloat, cache efficiency, expensive tool calls, why sessions get long or costly, or how to reduce usage, even if they don't say "optimize".
---

# Token Usage Analysis

Requires normalized tables from the `session-log-parsing` skill.

## Core reports
1. **Largest tool results**: rank by result length (estimated tokens ≈ chars / 4, labeled as an estimate). Usually the most revealing view.
2. **Downstream cost**: for each tool call, the input-token delta on the next assistant turn. Large results are re-sent every later turn until compaction, so cumulative cost is roughly size × remaining turns.
3. **Redundancy**: identical or overlapping reads, repeated searches, commands re-run with unchanged output.
4. **Context growth**: input tokens per turn over time; mark compaction events.
5. **Cache efficiency**: cache_read vs. cache_creation vs. uncached input. Flag sessions where the cache keeps being rebuilt.
6. **Breakdown**: by project, tool name, and model. Note simple steps that ran on a larger model than needed.

## Recommendations to generate (tie each to evidence)
- Narrower reads: line ranges, grep before read, avoid whole-file reads of large files
- Output limits on commands (`head`, `tail`, filters)
- Deny/ignore rules for lockfiles, generated files, data dumps, build output
- Trim a large `CLAUDE.md` (it rides on every request)
- Compact or start fresh sessions sooner when context growth is steep

## Caveats
- Dedupe usage by message ID.
- Per-result token counts are estimates unless derived from deltas.
- Report numbers with the session count and date range they cover.

## Output
Markdown report with a top-10 table for each report, plus a "recommended changes" list ranked by estimated token savings.
