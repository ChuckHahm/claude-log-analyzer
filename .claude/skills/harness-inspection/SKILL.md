---
name: harness-inspection
description: Reconstruct how the Claude Code agent loop behaves from session logs: tool sequences, message threading, subagent delegation, compaction, and per-step behavior. Use when the user wants to understand how Claude Code works, trace what happened in a session, see which tools get used and how they chain, or study agent harness design.
---

# Harness Inspection

Requires normalized tables from `session-log-parsing`.

## The loop to confirm in the data
user message → assistant message (text + `tool_use` blocks) → user-role message with matching `tool_result` blocks → repeat until an assistant message has no tool call.

## Analyses
- **Tool histogram**: counts by tool name, per session and overall.
- **Common sequences**: n-grams of tool names (e.g. search → read → edit → run tests) and their frequencies.
- **Thread tree**: build from `uuid`/`parentUuid`; identify branches (rewinds, resumes).
- **Subagents**: detect sidechain records (e.g. `isSidechain`); summarize what was delegated, how many steps it took, and what returned to the parent.
- **Compaction/summary records**: locate them, show context size before and after, list what preceded them.
- **Session trace**: render one session as a numbered step-by-step narrative (prompt → tool calls → results (truncated) → reply).
- **Metadata**: working directory, version, git branch if present.

## Known blind spots (state these in any output)
System prompt, full tool schemas, auto-loaded `CLAUDE.md` content, hook execution, and permission prompts are not reliably in the logs. For exact request/response payloads, use a logging proxy via `ANTHROPIC_BASE_URL`.

## Output
A markdown trace plus summary tables. Truncate long tool results (first ~300 chars) and apply redaction by default.
