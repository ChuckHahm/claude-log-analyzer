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

## Token report (M2)
```bash
python src/analyze_tokens.py                    # writes reports/token-report-<date>.md
python src/analyze_tokens.py --project -home-chuck-claude-BrianMedical
```
Ranked recommendations with evidence, largest results, redundant calls, context growth, cache efficiency, and cost/usage by project, tool and model. Per-result token figures are estimates (chars ÷ 4); "carried tokens" = estimate × turns the result stays in context.

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
M0 (schema), M1 (parser) and M2 (token attribution: `src/attribute.py`, `src/analyze_tokens.py`) are done, plus a first-cut viewer. M3 (harness inspection) is next. Run `python src/parse.py` to write tables to `data/`. M2 (token attribution) is next.
