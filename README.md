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

## Viewer
```bash
python src/server.py            # http://127.0.0.1:8765
python src/server.py --port 9000 --no-redact
```
Pick a project and session from the dropdowns. Tabs: Overview (cards, findings, context growth), Tools, Large & repeated, Trace, Exposure.
Binds to 127.0.0.1 only. Secrets in targets and previews are redacted unless `--no-redact`. Use **Reload logs** after new sessions are written.

## Privacy
Logs contain file contents and command output. Process locally, never commit real logs, and keep redaction on in reports.

## Status
M0 (schema discovery), M1 (parser: `src/parse.py`) and a first-cut viewer (`src/server.py`, `src/analysis.py`) are done. Run `python src/parse.py` to write tables to `data/`. M2 (token attribution) is next.
