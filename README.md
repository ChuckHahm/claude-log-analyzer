# Claude Log Analyzer

Local tool that reads Claude Code session logs (`~/.claude/projects/<project>/<session>.jsonl`) and answers:

1. **Token optimization**: which operations consume the most tokens?
2. **Harness understanding**: how does the agent loop work (tools, threading, subagents, compaction)?
3. **Data exposure**: what local content entered the model's context?

See [PLAN.md](PLAN.md) for milestones and [CLAUDE.md](CLAUDE.md) for working rules.

## Setup
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
sudo apt install jq   # ad-hoc log inspection
pytest
```

## Privacy
Logs contain file contents and command output. Process locally, never commit real logs, and keep redaction on in reports.

## Status
M0 (schema discovery) is next. Run the `session-log-parsing` skill to probe real logs and write `docs/schema-notes.md`.
