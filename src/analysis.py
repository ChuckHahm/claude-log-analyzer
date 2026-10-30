"""View-level analysis on top of parse.ParseResult. All outputs are JSON-friendly.

Token figures for tool results are ESTIMATES (chars / 4); logged usage is a lower bound
on billed usage (see docs/schema-notes.md).
"""
from __future__ import annotations

import re

import pandas as pd

from attribute import attribute
from parse import USAGE_COLS, ParseResult

CHARS_PER_TOKEN = 4
LARGE_RESULT_CHARS = 20_000
SENSITIVE_PATH = re.compile(
    r"(\.env\b|\.pem$|\.key$|id_rsa|id_ed25519|credentials|secrets?\b|\.npmrc|\.netrc|\.aws/|\.ssh/|"
    r"kubeconfig|token|password|\.pgpass|claude_key)", re.I)
REDACTIONS = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "sk-…[redacted]"),
    (re.compile(r"AKIA[0-9A-Z]{12,}"), "AKIA…[redacted]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{12,}"), "gh_…[redacted]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{12,}"), r"\1[redacted]"),
    (re.compile(r"(?i)((?:password|passwd|secret|api[_-]?key|token)\s*[=:]\s*)\S+"), r"\1[redacted]"),
]


def redact(text):
    if not isinstance(text, str):
        return text
    for rx, rep in REDACTIONS:
        text = rx.sub(rep, text)
    return text


def _ctx(u: pd.DataFrame) -> pd.Series:
    return u.input_tokens + u.cache_creation_input_tokens + u.cache_read_input_tokens


def _clean(df: pd.DataFrame) -> list[dict]:
    return df.astype(object).where(df.notna(), None).to_dict("records")


def projects(r: ParseResult) -> list[dict]:
    if r.sessions.empty:
        return []
    g = r.sessions.groupby("project").agg(sessions=("session_id", "size"), last_ts=("last_ts", "max")).reset_index()
    return _clean(g.sort_values("last_ts", ascending=False))


def sessions(r: ParseResult, project: str | None) -> list[dict]:
    s = r.sessions
    if project:
        s = s[s.project == project]
    return _clean(s.sort_values("last_ts", ascending=False))


def _scope(r: ParseResult, project: str | None, session: str | None):
    def f(df):
        if df.empty:
            return df
        if project:
            df = df[df.project == project]
        if session:
            df = df[df.session_id == session]
        return df
    return f(r.messages), f(r.tool_calls), f(r.usage), f(r.cost_state)


def overview(r: ParseResult, project=None, session=None) -> dict:
    msgs, calls, usage, costs = _scope(r, project, session)
    out = {"records": len(msgs), "tool_calls": len(calls), "assistant_messages": len(usage)}
    if not usage.empty:
        t = usage[USAGE_COLS].sum()
        ctx_total = int(t.input_tokens + t.cache_creation_input_tokens + t.cache_read_input_tokens)
        out.update(
            input_tokens=int(t.input_tokens), output_tokens=int(t.output_tokens),
            cache_creation=int(t.cache_creation_input_tokens), cache_read=int(t.cache_read_input_tokens),
            cache_hit_ratio=round(float(t.cache_read_input_tokens) / ctx_total, 3) if ctx_total else None,
            models=usage.model.value_counts().to_dict(),
            first_ts=usage.timestamp.min(), last_ts=usage.timestamp.max())
    if not costs.empty:
        last = costs.groupby("session_id").tail(1)
        out["cost_state_usd"] = round(float(last.total_cost_usd.fillna(0).sum()), 4)
        out["cost_state_sessions"] = int(len(last))
        # like-for-like: logged output tokens only for sessions that have a cost-state record
        out["logged_output_in_costed"] = int(usage[usage.session_id.isin(last.session_id)].output_tokens.sum())
        agg = {"inputTokens": 0, "outputTokens": 0, "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0}
        for mu in last.model_usage:
            for mdl in mu.values():
                for k in agg:
                    agg[k] += mdl.get(k, 0)
        out["cost_state_tokens"] = agg
    return out


def context_series(r: ParseResult, project=None, session=None) -> list[dict]:
    _, _, usage, _ = _scope(r, project, session)
    if usage.empty:
        return []
    u = usage.sort_values("timestamp").copy()
    u["context"] = _ctx(u)
    return _clean(u[["session_id", "timestamp", "context", "output_tokens", "cache_read_input_tokens",
                     "cache_creation_input_tokens", "input_tokens"]])


def tool_summary(r: ParseResult, project=None, session=None) -> list[dict]:
    _, calls, usage, _ = _scope(r, project, session)
    if calls.empty:
        return []
    c = attribute(calls, usage)
    g = c.groupby("tool").agg(calls=("tool_use_id", "size"), errors=("is_error", "sum"),
                              result_chars=("result_chars", "sum"), est_tokens=("est_tokens", "sum"),
                              max_chars=("result_chars", "max")).reset_index()
    return _clean(g.sort_values("est_tokens", ascending=False))


def top_results(r: ParseResult, project=None, session=None, n=20, do_redact=True) -> list[dict]:
    _, calls, usage, _ = _scope(r, project, session)
    if calls.empty:
        return []
    c = attribute(calls, usage).sort_values("result_chars", ascending=False).head(n)
    c["target"] = c.target.map(redact) if do_redact else c.target
    return _clean(c[["session_id", "timestamp", "tool", "target", "result_chars", "est_tokens",
                     "next_ctx_delta", "remaining_turns", "carried_tokens", "is_error"]])


def repeated_calls(r: ParseResult, project=None, session=None, do_redact=True) -> list[dict]:
    _, calls, _, _ = _scope(r, project, session)
    if calls.empty:
        return []
    c = calls[calls.target.notna()].copy()
    c["result_chars"] = c.get("result_chars", 0)
    g = (c.groupby(["session_id", "tool", "target"])
         .agg(times=("tool_use_id", "size"), result_chars=("result_chars", "sum")).reset_index())
    g = g[g.times > 1].copy()
    g["wasted_est_tokens"] = ((g.result_chars * (g.times - 1) / g.times) / CHARS_PER_TOKEN).round().astype(int)
    if do_redact:
        g["target"] = g.target.map(redact)
    return _clean(g.sort_values("wasted_est_tokens", ascending=False).head(25))


def trace(r: ParseResult, project=None, session=None, limit=400, do_redact=True) -> list[dict]:
    msgs, calls, _, _ = _scope(r, project, session)
    if msgs.empty:
        return []
    m = msgs[msgs.type.isin(["user", "assistant", "system"])].sort_values("timestamp").head(limit).copy()
    m["preview"] = m.preview.map(redact) if do_redact else m.preview
    return _clean(m[["timestamp", "type", "block_types", "preview", "is_sidechain", "message_id"]])


def exposure(r: ParseResult, project=None, session=None, do_redact=True) -> dict:
    """Paths the model read via file tools, plus sensitive-path hits across all tool targets.

    Path-based only: tool result text is not retained, so content is not scanned here.
    """
    _, calls, _, _ = _scope(r, project, session)
    if calls.empty:
        return {"files_read": [], "flagged": [], "tool_targets": 0}
    reads = calls[calls.tool.isin(["Read", "NotebookEdit"]) & calls.target.notna()]
    fr = (reads.groupby("target").agg(times=("tool_use_id", "size"),
                                      sessions=("session_id", "nunique")).reset_index()
          .sort_values("times", ascending=False).head(100))
    t = calls[calls.target.notna()]
    hit = t[t.target.map(lambda x: bool(SENSITIVE_PATH.search(x)))]
    fl = (hit.groupby(["tool", "target"]).agg(times=("tool_use_id", "size")).reset_index()
          .sort_values("times", ascending=False).head(50))
    if do_redact:
        fr["target"] = fr.target.map(redact)
        fl["target"] = fl.target.map(redact)
    return {"files_read": _clean(fr), "flagged": _clean(fl), "tool_targets": int(len(t)),
            "caveat": "Logs approximate what was sent to the API but omit the system prompt, tool schemas, "
                      "auto-loaded context, and telemetry. For exact traffic, use a logging proxy."}


def insights(r: ParseResult, project=None, session=None) -> list[dict]:
    """Plain-language findings, each with a severity and a suggested action."""
    out = []
    ov = overview(r, project, session)
    _, calls, usage, _ = _scope(r, project, session)
    if ov.get("cache_hit_ratio") is not None:
        ratio = ov["cache_hit_ratio"]
        out.append({"severity": "info" if ratio >= .8 else "warn", "title": f"Cache hit ratio {ratio:.0%}",
                    "detail": "Share of context tokens served from cache. High is good and cheap; "
                              "low means prompts keep changing early in the context.",
                    "action": None if ratio >= .8 else "Avoid editing CLAUDE.md or tool config mid-session."})
    if not calls.empty:
        c = attribute(calls, usage)
        big = c[c.result_chars >= LARGE_RESULT_CHARS]
        if len(big):
            share = big.est_tokens.sum() / max(c.est_tokens.sum(), 1)
            out.append({"severity": "warn", "title": f"{len(big)} tool result(s) over {LARGE_RESULT_CHARS:,} chars",
                        "detail": f"They make up ~{share:.0%} of estimated tool-result tokens, and every later turn re-reads them.",
                        "action": "Narrow Read ranges, use grep/head/tail, or pipe command output through filters."})
        rep = repeated_calls(r, project, session)
        if rep:
            w = sum(x["wasted_est_tokens"] for x in rep)
            out.append({"severity": "warn", "title": f"{len(rep)} repeated identical call(s)",
                        "detail": f"About {w:,} estimated tokens were re-fetched. Top: {rep[0]['tool']} {rep[0]['target']!s:.80}",
                        "action": "Reuse earlier results; avoid re-reading unchanged files."})
        err = int(c.is_error.sum())
        if err:
            out.append({"severity": "info", "title": f"{err} tool call(s) returned errors",
                        "detail": "Failed calls still cost tokens and a turn.", "action": None})
        ex = exposure(r, project, session)
        if ex["flagged"]:
            out.append({"severity": "alert", "title": f"{len(ex['flagged'])} sensitive-looking path(s) touched",
                        "detail": "Paths matching .env/key/credential patterns appeared in tool calls, "
                                  "so their contents may have entered model context.",
                        "action": "Add deny rules for these paths in settings permissions."})
    if not usage.empty:
        u = usage.sort_values("timestamp")
        ctx = _ctx(u)
        if ctx.max() > 150_000:
            out.append({"severity": "warn", "title": f"Context reached {int(ctx.max()):,} tokens",
                        "detail": "Large contexts raise per-turn cost.", "action": "Start a fresh session or /compact earlier."})
    cs, lg = ov.get("cost_state_tokens"), ov.get("logged_output_in_costed")
    if cs and lg and cs["outputTokens"] > lg:
        gap = 1 - lg / cs["outputTokens"]
        out.append({"severity": "info", "title": "Logs under-report usage",
                    "detail": f"Logged output tokens are {gap:.0%} below the session cost-state totals "
                              "(helper-model and unlogged calls are missing from the logs).",
                    "action": None})
    return out
