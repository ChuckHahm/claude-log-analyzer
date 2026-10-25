"""Parse Claude Code session JSONL logs into normalized pandas tables.

Field names follow docs/schema-notes.md (observed on Claude Code 2.1.294-2.1.310).
Parsing is defensive: unknown record types are counted, never fatal.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

DEFAULT_ROOT = Path.home() / ".claude" / "projects"
MESSAGE_TYPES = {"user", "assistant", "attachment", "system"}
KNOWN_TYPES = MESSAGE_TYPES | {
    "api-request", "api-request-blob", "api-request-shape", "cost-state",
    "file-history-snapshot", "file-history-delta", "queue-operation",
    "permission-mode", "mode", "atis-latch", "last-prompt", "ai-title",
}
BUILTIN_TOOLS = {n.lower(): n for n in (
    "Bash", "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "Task", "Agent",
    "Skill", "WebFetch", "WebSearch", "NotebookEdit", "TodoWrite",
)}
USAGE_COLS = ["input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"]


@dataclass
class ParseResult:
    messages: pd.DataFrame
    tool_calls: pd.DataFrame
    usage: pd.DataFrame
    cost_state: pd.DataFrame
    stats: dict = field(default_factory=dict)
    sessions: pd.DataFrame = field(default_factory=pd.DataFrame)

    def summary(self) -> str:
        s = self.stats
        lines = [
            f"files: {s['files']}  sessions: {s['sessions']}  records: {s['records']}",
            f"skipped (blank/invalid JSON): {s['skipped_lines']}",
            f"unknown record types: {dict(s['unknown_types']) or 'none'}",
            f"date range: {s['first_ts']} .. {s['last_ts']}",
            f"messages: {len(self.messages)}  tool_calls: {len(self.tool_calls)}  usage rows: {len(self.usage)}",
        ]
        return "\n".join(lines)


def canonical_tool(name: str | None) -> str | None:
    if name is None:
        return None
    return BUILTIN_TOOLS.get(name.lower(), name)


def iter_records(path: Path):
    """Yield (record, ok) per line; ok False for blank/invalid/non-object lines."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                yield None, False
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                yield None, False
                continue
            yield (rec, True) if isinstance(rec, dict) else (None, False)


def blocks(content) -> list[dict]:
    """Normalize message content (string or list) to a list of block dicts."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def result_size(content) -> tuple[int, int]:
    """Return (text_chars, image_blocks) for a tool_result content value."""
    if isinstance(content, str):
        return len(content), 0
    chars = images = 0
    for b in blocks(content):
        if b.get("type") == "image":
            images += 1
        else:
            t = b.get("text")
            chars += len(t) if isinstance(t, str) else len(json.dumps(b))
    return chars, images


PREVIEW_CHARS = 200


def tool_target(name: str | None, inp) -> str | None:
    """Short description of what a tool call acts on (path, command, pattern)."""
    if not isinstance(inp, dict):
        return None
    for key in ("file_path", "path", "notebook_path", "command", "pattern", "url", "query", "skill"):
        v = inp.get(key)
        if isinstance(v, str):
            return v[:PREVIEW_CHARS]
    return None


def preview_of(rtype: str, content: list[dict]) -> str:
    """One-line, truncated description of a record. Never the full content."""
    for b in content:
        t = b.get("type")
        if t == "text" and isinstance(b.get("text"), str):
            return b["text"].strip().replace("\n", " ")[:PREVIEW_CHARS]
        if t == "tool_use":
            return f"{canonical_tool(b.get('name'))}: {tool_target(b.get('name'), b.get('input')) or ''}"[:PREVIEW_CHARS]
        if t == "tool_result":
            return "(tool result)"
        if t == "thinking":
            return "(thinking)"
    return rtype


def _tur_chars(tur, key: str) -> int | None:
    return len(tur[key]) if isinstance(tur, dict) and isinstance(tur.get(key), str) else None


def parse_files(paths: list[Path], root: Path | None = None) -> ParseResult:
    root = root or DEFAULT_ROOT
    msgs, usage_rows, uses, results, costs = [], [], {}, {}, []
    seen_msg_ids: set[str] = set()
    stats = {"files": 0, "records": 0, "skipped_lines": 0, "unknown_types": Counter()}
    sessions, timestamps, titles = set(), [], {}

    for path in paths:
        stats["files"] += 1
        try:
            project = path.relative_to(root).parts[0]
        except ValueError:
            project = path.parent.name
        for rec, ok in iter_records(path):
            if not ok:
                stats["skipped_lines"] += 1
                continue
            stats["records"] += 1
            rtype = rec.get("type")
            if rtype not in KNOWN_TYPES:
                stats["unknown_types"][rtype] += 1
            session = rec.get("sessionId") or rec.get("session_id") or path.stem
            sessions.add(session)
            ts = rec.get("timestamp")
            if ts:
                timestamps.append(ts)

            if rtype == "ai-title" and rec.get("aiTitle"):
                titles[session] = rec["aiTitle"]
            if rtype == "cost-state":
                costs.append({"session_id": session, "project": project,
                              "total_cost_usd": rec.get("totalCostUSD"),
                              "model_usage": rec.get("modelUsage") or {}})
                continue
            if rtype not in MESSAGE_TYPES:
                continue

            msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
            content = blocks(msg.get("content"))
            msg_id = msg.get("id")
            msgs.append({
                "uuid": rec.get("uuid"), "parent_uuid": rec.get("parentUuid"), "type": rtype,
                "role": msg.get("role"), "timestamp": ts,
                "is_sidechain": bool(rec.get("isSidechain")), "session_id": session,
                "project": project, "version": rec.get("version"), "message_id": msg_id,
                "block_types": ",".join(sorted({b.get("type", "?") for b in content})),
                "preview": preview_of(rtype, content),
            })

            if rtype == "assistant":
                if msg_id and msg_id not in seen_msg_ids:  # first record wins; usage identical across blocks
                    seen_msg_ids.add(msg_id)
                    u = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
                    usage_rows.append({
                        "session_id": session, "project": project, "message_id": msg_id,
                        "model": msg.get("model"), "timestamp": ts,
                        "stop_reason": msg.get("stop_reason"),
                        "is_sidechain": bool(rec.get("isSidechain")),
                        **{c: u.get(c) or 0 for c in USAGE_COLS},
                    })
                for b in content:
                    if b.get("type") == "tool_use" and b.get("id"):
                        uses[b["id"]] = {
                            "tool_use_id": b["id"], "tool_raw": b.get("name"),
                            "tool": canonical_tool(b.get("name")), "input": b.get("input"),
                            "target": tool_target(b.get("name"), b.get("input")),
                            "session_id": session, "project": project, "message_id": msg_id,
                            "timestamp": ts, "is_sidechain": bool(rec.get("isSidechain")),
                        }
            elif rtype == "user":
                tur = rec.get("toolUseResult")
                for b in content:
                    if b.get("type") == "tool_result" and b.get("tool_use_id"):
                        chars, images = result_size(b.get("content"))
                        results[b["tool_use_id"]] = {
                            "result_chars": chars, "result_images": images,
                            "is_error": bool(b.get("is_error")),
                            "stdout_chars": _tur_chars(tur, "stdout"),
                            "stderr_chars": _tur_chars(tur, "stderr"),
                            "result_timestamp": ts,
                        }

    calls = pd.DataFrame(list(uses.values()))
    res = pd.DataFrame.from_dict(results, orient="index").rename_axis("tool_use_id").reset_index()
    if calls.empty:
        tool_calls = calls
    else:
        tool_calls = calls.merge(res, on="tool_use_id", how="left") if not res.empty else calls
        tool_calls["has_result"] = tool_calls.get("result_chars").notna() if "result_chars" in tool_calls else False

    stats.update(sessions=len(sessions), unknown_types=stats["unknown_types"],
                 first_ts=min(timestamps, default=None), last_ts=max(timestamps, default=None))
    messages = pd.DataFrame(msgs)
    sess = pd.DataFrame()
    if not messages.empty:
        sess = (messages.groupby(["project", "session_id"])
                .agg(first_ts=("timestamp", "min"), last_ts=("timestamp", "max"),
                     records=("uuid", "size"), version=("version", "last")).reset_index())
        sess["title"] = sess.session_id.map(titles)
    return ParseResult(messages, tool_calls, pd.DataFrame(usage_rows), pd.DataFrame(costs), stats, sess)


def parse_dir(root: Path | None = None) -> ParseResult:
    root = Path(root) if root else DEFAULT_ROOT
    return parse_files(sorted(root.rglob("*.jsonl")), root)


def validate_usage(result: ParseResult) -> pd.DataFrame:
    """Compare summed deduped usage per session against the last cost-state record.

    cost-state reports per-model totals; differences are expected when it covers
    models/requests with no assistant record (e.g. small helper models), so compare
    per model and review the deltas rather than demanding equality.
    """
    rows = []
    mapping = {"inputTokens": "input_tokens", "outputTokens": "output_tokens",
               "cacheReadInputTokens": "cache_read_input_tokens",
               "cacheCreationInputTokens": "cache_creation_input_tokens"}
    if result.cost_state.empty or result.usage.empty:
        return pd.DataFrame()
    last = result.cost_state.groupby("session_id").tail(1)
    sums = result.usage.groupby(["session_id", "model"])[USAGE_COLS].sum()
    for _, c in last.iterrows():
        for model, mu in c["model_usage"].items():
            for k, col in mapping.items():
                parsed = int(sums.loc[(c["session_id"], model), col]) if (c["session_id"], model) in sums.index else 0
                rows.append({"session_id": c["session_id"], "model": model, "field": col,
                             "cost_state": mu.get(k, 0), "parsed": parsed,
                             "delta": parsed - mu.get(k, 0)})
    return pd.DataFrame(rows)


def write_tables(result: ParseResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("messages", "tool_calls", "usage"):
        df = getattr(result, name).copy()
        if "input" in df:
            df["input"] = df["input"].map(lambda v: json.dumps(v, default=str))
        df.to_csv(out_dir / f"{name}.csv", index=False)


if __name__ == "__main__":
    import sys
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    r = parse_dir(root)
    print(r.summary())
    write_tables(r, Path("data"))
