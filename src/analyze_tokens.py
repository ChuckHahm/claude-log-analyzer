"""Token reports and ranked optimization recommendations (M2).

Usage: python src/analyze_tokens.py [--root DIR] [--project P] [--out reports/tokens.md] [--no-redact]

Per-result figures are estimates (see attribute.py). Logged usage is a lower bound on billed
usage (see docs/schema-notes.md); project cost comes from cost-state records where present.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

import parse
from analysis import _scope, redact
from attribute import CHARS_PER_TOKEN, attribute, turns_table

LARGE_RESULT_CHARS = 20_000
TARGET_RESULT_CHARS = 5_000      # assumed size after narrowing a large result
BIG_CONTEXT = 150_000
BUST_SHARE = 0.5                 # a turn that rewrites >50% of context to cache is a cache bust
REREAD_TOOLS = {"Read", "Grep", "Glob"}
MODIFYING_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}  # repeats are expected, not waste
TOP = 10


def _trunc(s, n=90):
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def top_results(c: pd.DataFrame, n=20, do_redact=True) -> pd.DataFrame:
    t = c.sort_values("result_chars", ascending=False).head(n).copy()
    if do_redact:
        t["target"] = t.target.map(redact)
    return t[["project", "session_id", "tool", "target", "result_chars", "est_tokens",
              "next_ctx_delta", "remaining_turns", "carried_tokens"]]


def redundancy(c: pd.DataFrame, do_redact=True) -> pd.DataFrame:
    """Identical (session, tool, target) calls. Savings = carried tokens of every occurrence after the first."""
    d = c[c.target.notna() & ~c.tool.isin(MODIFYING_TOOLS)].sort_values(["session_id", "timestamp"]).copy()
    d["occurrence"] = d.groupby(["session_id", "tool", "target"]).cumcount()
    d["times"] = d.groupby(["session_id", "tool", "target"]).tool_use_id.transform("size")
    d = d[d.times > 1]
    if d.empty:
        return pd.DataFrame(columns=["session_id", "tool", "target", "times", "wasted_carried_tokens", "kind"])
    later = d[d.occurrence > 0]
    g = (later.groupby(["session_id", "tool", "target"])
         .agg(wasted_carried_tokens=("carried_tokens", "sum")).reset_index())
    g = g.merge(d.groupby(["session_id", "tool", "target"]).times.first().reset_index(), on=["session_id", "tool", "target"])
    g["kind"] = g.tool.map(lambda t: "re-read (output unlikely to change)" if t in REREAD_TOOLS
                           else "re-run (output may differ)")
    if do_redact:
        g["target"] = g.target.map(redact)
    return g.sort_values("wasted_carried_tokens", ascending=False)


def context_growth(usage: pd.DataFrame) -> pd.DataFrame:
    if usage.empty:
        return pd.DataFrame()
    u = turns_table(usage)
    g = u.groupby(["project", "session_id"], sort=False).agg(
        turns=("ctx", "size"), first_ctx=("ctx", "first"), peak_ctx=("ctx", "max"),
        last_ts=("timestamp", "max")).reset_index()
    g["growth_per_turn"] = ((g.peak_ctx - g.first_ctx) / (g.turns - 1).replace(0, pd.NA)).astype("float")
    over = u.assign(over=(u.ctx - BIG_CONTEXT).clip(lower=0)).groupby("session_id").over.sum()
    g["tokens_over_150k"] = g.session_id.map(over).fillna(0).astype(int)
    return g.sort_values("peak_ctx", ascending=False)


def cache_efficiency(usage: pd.DataFrame) -> pd.DataFrame:
    if usage.empty:
        return pd.DataFrame()
    u = turns_table(usage)
    u["bust"] = (u.turn > 0) & (u.cache_creation_input_tokens > BUST_SHARE * u.ctx)
    g = u.groupby(["project", "session_id"], sort=False).agg(
        turns=("ctx", "size"), uncached=("input_tokens", "sum"),
        cache_write=("cache_creation_input_tokens", "sum"), cache_read=("cache_read_input_tokens", "sum"),
        busts=("bust", "sum")).reset_index()
    tot = g.uncached + g.cache_write + g.cache_read
    g["hit_ratio"] = (g.cache_read / tot.replace(0, pd.NA)).astype("float")
    g["read_write_ratio"] = (g.cache_read / g.cache_write.replace(0, pd.NA)).astype("float")
    bust_write = u[u.bust].groupby("session_id").cache_creation_input_tokens.sum()
    g["bust_write_tokens"] = g.session_id.map(bust_write).fillna(0).astype(int)
    return g.sort_values("hit_ratio")


def breakdown(r: parse.ParseResult, c: pd.DataFrame, usage: pd.DataFrame, costs: pd.DataFrame) -> dict:
    out = {}
    last = costs.groupby("session_id").tail(1) if not costs.empty else costs
    proj = []
    for p, ug in usage.groupby("project"):
        sess = set(ug.session_id)
        cg = last[last.session_id.isin(sess)] if not last.empty else last
        cc = c[c.project == p] if not c.empty else c
        proj.append({"project": p, "sessions": len(sess), "sessions_with_cost": int(len(cg)),
                     "cost_state_usd": float(cg.total_cost_usd.fillna(0).sum()) if len(cg) else None,
                     "assistant_turns": len(ug), "output_tokens": int(ug.output_tokens.sum()),
                     "cache_read_tokens": int(ug.cache_read_input_tokens.sum()),
                     "tool_calls": len(cc), "est_result_tokens": int(cc.est_tokens.sum()) if len(cc) else 0})
    out["project"] = pd.DataFrame(proj).sort_values("cost_state_usd", ascending=False, na_position="last")
    if not c.empty:
        out["tool"] = (c.groupby("tool").agg(calls=("tool_use_id", "size"), errors=("is_error", "sum"),
                                              est_result_tokens=("est_tokens", "sum"),
                                              carried_tokens=("carried_tokens", "sum"))
                       .reset_index().sort_values("carried_tokens", ascending=False))
        tot = out["tool"].carried_tokens.sum()
        out["tool"]["share_of_carried"] = (out["tool"].carried_tokens / tot) if tot else 0.0
    out["model"] = (usage.groupby("model").agg(turns=("message_id", "size"), output_tokens=("output_tokens", "sum"),
                                               cache_read=("cache_read_input_tokens", "sum"),
                                               cache_write=("cache_creation_input_tokens", "sum"),
                                               uncached=("input_tokens", "sum")).reset_index())
    return out


def recommendations(c: pd.DataFrame, red: pd.DataFrame, growth: pd.DataFrame, cache: pd.DataFrame,
                    do_redact=True) -> list[dict]:
    """Ranked by estimated token savings; each tied to evidence."""
    recs = []
    if not c.empty:
        big = c[c.result_chars >= LARGE_RESULT_CHARS].copy()
        big["saving"] = ((big.result_chars - TARGET_RESULT_CHARS) / CHARS_PER_TOKEN) * big.remaining_turns.fillna(0)
        for kind, tools, action in (
            ("file/search", REREAD_TOOLS, "Read with offset/limit or grep first; add deny rules for lockfiles, generated files and data dumps."),
            ("command", {"Bash"}, "Pipe command output through head/tail/grep, or write it to a file and read a slice."),
            ("other-tool", None, "Find out why the result was large: a failed Edit or tool call can echo a big payload "
                                 "(e.g. a full settings schema). Verify names and arguments before calling, to avoid the error."),
        ):
            sel = big.tool.isin(tools) if tools else ~big.tool.isin(REREAD_TOOLS | {"Bash"})
            b = big[sel].sort_values("saving", ascending=False)
            if len(b):
                ev = [f"{r.tool} {_trunc(redact(r.target) if do_redact else r.target, 70)} "
                      f"({int(r.result_chars):,} chars, ~{int(r.est_tokens):,} tokens, carried {int(r.remaining_turns or 0)} turns)"
                      for r in b.head(3).itertuples()]
                recs.append({"title": f"Narrow {len(b)} large {kind} result(s) (over {LARGE_RESULT_CHARS:,} chars)",
                             "est_tokens_saved": int(b.saving.sum()),
                             "basis": f"assumes each is cut to ~{TARGET_RESULT_CHARS:,} chars; savings = tokens removed × turns carried",
                             "evidence": ev, "action": action})
    rr = red[red.kind.str.startswith("re-read")] if len(red) else red
    if len(rr):
        recs.append({"title": f"Stop re-reading unchanged files/searches ({len(rr)} repeated)",
                     "est_tokens_saved": int(rr.wasted_carried_tokens.sum()),
                     "basis": "carried tokens of every repeat after the first; ignores files edited between reads",
                     "evidence": [f"{r.tool} {_trunc(r.target, 70)} ×{r.times}" for r in rr.head(3).itertuples()],
                     "action": "Reuse the earlier result; re-read only after an edit or when a specific range is needed."})
    rn = red[red.kind.str.startswith("re-run")] if len(red) else red
    if len(rn):
        recs.append({"title": f"Review {len(rn)} re-run command(s) with identical text",
                     "est_tokens_saved": 0, "basis": "not counted: output may legitimately differ (tests, status checks)",
                     "evidence": [f"{r.tool} {_trunc(r.target, 70)} ×{r.times}" for r in rn.head(3).itertuples()],
                     "action": "Check whether the output was unchanged; if so, avoid the re-run."})
    if len(growth):
        g = growth[growth.tokens_over_150k > 0]
        if len(g):
            recs.append({"title": f"Compact or restart earlier in {len(g)} session(s) that exceeded {BIG_CONTEXT:,} tokens",
                         "est_tokens_saved": int(g.tokens_over_150k.sum()),
                         "basis": f"context tokens above {BIG_CONTEXT:,} summed over turns (upper bound; compaction keeps a summary)",
                         "evidence": [f"{r.session_id[:8]}: peak {int(r.peak_ctx):,} over {int(r.turns)} turns" for r in g.head(3).itertuples()],
                         "action": "Run /compact or start a fresh session at a task boundary before context passes ~150k."})
    if len(cache):
        b = cache[cache.busts > 0]
        if len(b):
            recs.append({"title": f"Avoid cache rebuilds in {len(b)} session(s) ({int(b.busts.sum())} bust turns)",
                         "est_tokens_saved": int(b.bust_write_tokens.sum()),
                         "basis": "tokens re-written to cache on bust turns (cache writes cost more than reads)",
                         "evidence": [f"{r.session_id[:8]}: {int(r.busts)} bust(s), hit ratio {r.hit_ratio:.0%}" for r in b.head(3).itertuples()],
                         "action": "Avoid editing CLAUDE.md, MCP/tool config, or switching models mid-session; resume within the cache TTL."})
    return sorted(recs, key=lambda x: x["est_tokens_saved"], reverse=True)


def build(r: parse.ParseResult, project=None, session=None, do_redact=True) -> dict:
    msgs, calls, usage, costs = _scope(r, project, session)
    c = attribute(calls, usage) if not calls.empty else calls
    red = redundancy(c, do_redact) if not c.empty else pd.DataFrame(columns=["kind"])
    growth, cache = context_growth(usage), cache_efficiency(usage)
    return {
        "scope": {"project": project or "all projects", "session": session or "all sessions",
                  "sessions": int(usage.session_id.nunique()) if len(usage) else 0,
                  "first_ts": usage.timestamp.min() if len(usage) else None,
                  "last_ts": usage.timestamp.max() if len(usage) else None,
                  "redacted": do_redact},
        "top_results": top_results(c, 20, do_redact) if not c.empty else pd.DataFrame(),
        "redundancy": red, "growth": growth, "cache": cache,
        "breakdown": breakdown(r, c, usage, costs) if len(usage) else {},
        "recommendations": recommendations(c, red, growth, cache, do_redact) if not c.empty else [],
    }


# ---- markdown rendering ----
def _md(df: pd.DataFrame, cols: list[tuple[str, str, str]], n=TOP) -> str:
    """cols: (header, column, format) where format is '', 'int', 'pct', 'usd', 'f1'."""
    if df is None or len(df) == 0:
        return "_Nothing to report._\n"
    def f(v, fmt):
        if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NA:
            return "–"
        return {"int": lambda x: f"{int(x):,}", "pct": lambda x: f"{x:.0%}", "usd": lambda x: f"${x:,.2f}",
                "f1": lambda x: f"{x:,.1f}"}.get(fmt, lambda x: str(x).replace("|", "\\|"))(v)
    rows = ["| " + " | ".join(h for h, _, _ in cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for rec in df.head(n).to_dict("records"):
        rows.append("| " + " | ".join(f(rec.get(k), fmt) if k != "target" else _trunc(f(rec.get(k), fmt), 70)
                                      for _, k, fmt in cols) + " |")
    return "\n".join(rows) + "\n"


def render_markdown(rep: dict) -> str:
    s = rep["scope"]
    L = [f"# Token usage report", "",
         f"Scope: **{s['project']}** / {s['session']} · {s['sessions']} session(s) · "
         f"{(s['first_ts'] or '–')[:10]} to {(s['last_ts'] or '–')[:10]} · redaction {'on' if s['redacted'] else 'OFF'}",
         "", "Per-result token figures are **estimates** (chars ÷ 4). Logged usage is a lower bound on billed usage; "
         "cost comes from `cost-state` records where present.",
         "", "Token counts are not equal in price: cache reads are much cheaper than cache writes or uncached input. "
         "Compare savings within a category rather than across categories.", ""]
    L += ["## Recommended changes (ranked by estimated tokens saved)", ""]
    if rep["recommendations"]:
        for i, rc in enumerate(rep["recommendations"], 1):
            L += [f"{i}. **{rc['title']}** — est. {rc['est_tokens_saved']:,} tokens",
                  f"   - Basis: {rc['basis']}", *[f"   - Evidence: {e}" for e in rc["evidence"]],
                  f"   - Action: {rc['action']}"]
    else:
        L.append("_No optimization candidates found._")
    L += ["", f"## Top {TOP} largest tool results", "",
          _md(rep["top_results"], [("Tool", "tool", ""), ("Target", "target", ""), ("Chars", "result_chars", "int"),
                                   ("Est. tokens", "est_tokens", "int"), ("Next-turn Δ", "next_ctx_delta", "int"),
                                   ("Turns carried", "remaining_turns", "int"), ("Carried tokens", "carried_tokens", "int")])]
    L += [f"## Redundant calls (top {TOP})", "",
          _md(rep["redundancy"], [("Tool", "tool", ""), ("Target", "target", ""), ("Times", "times", "int"),
                                  ("Wasted carried tokens", "wasted_carried_tokens", "int"), ("Kind", "kind", "")])]
    L += [f"## Context growth by session (top {TOP} by peak)", "",
          _md(rep["growth"], [("Project", "project", ""), ("Session", "session_id", ""), ("Turns", "turns", "int"),
                              ("First ctx", "first_ctx", "int"), ("Peak ctx", "peak_ctx", "int"),
                              ("Growth/turn", "growth_per_turn", "int"), ("Tokens over 150k", "tokens_over_150k", "int")])]
    L += [f"## Cache efficiency (worst {TOP} by hit ratio)", "",
          _md(rep["cache"], [("Project", "project", ""), ("Session", "session_id", ""), ("Turns", "turns", "int"),
                             ("Uncached", "uncached", "int"), ("Cache write", "cache_write", "int"),
                             ("Cache read", "cache_read", "int"), ("Hit ratio", "hit_ratio", "pct"),
                             ("Read:write", "read_write_ratio", "f1"), ("Busts", "busts", "int")])]
    b = rep["breakdown"]
    if b:
        L += ["## Cost and usage by project", "",
              _md(b["project"], [("Project", "project", ""), ("Sessions", "sessions", "int"),
                                 ("With cost-state", "sessions_with_cost", "int"), ("Cost (cost-state)", "cost_state_usd", "usd"),
                                 ("Turns", "assistant_turns", "int"), ("Output tok", "output_tokens", "int"),
                                 ("Tool calls", "tool_calls", "int"), ("Est. result tok", "est_result_tokens", "int")], 50),
              "_Cost covers only sessions that have a `cost-state` record._", ""]
        if "tool" in b:
            L += ["## By tool (share of carried tool-result tokens)", "",
                  _md(b["tool"], [("Tool", "tool", ""), ("Calls", "calls", "int"), ("Errors", "errors", "int"),
                                  ("Est. result tok", "est_result_tokens", "int"), ("Carried tok", "carried_tokens", "int"),
                                  ("Share", "share_of_carried", "pct")], 50)]
        L += ["## By model", "",
              _md(b["model"], [("Model", "model", ""), ("Turns", "turns", "int"), ("Output", "output_tokens", "int"),
                               ("Cache read", "cache_read", "int"), ("Cache write", "cache_write", "int"),
                               ("Uncached", "uncached", "int")], 50)]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=parse.DEFAULT_ROOT)
    ap.add_argument("--project")
    ap.add_argument("--out", type=Path, default=Path("reports") / f"token-report-{date.today()}.md")
    ap.add_argument("--no-redact", action="store_true")
    a = ap.parse_args()
    rep = build(parse.parse_dir(a.root), project=a.project, do_redact=not a.no_redact)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(render_markdown(rep))
    print(f"wrote {a.out}  ({rep['scope']['sessions']} sessions, {len(rep['recommendations'])} recommendations)")
    for rc in rep["recommendations"]:
        print(f"  ~{rc['est_tokens_saved']:>12,}  {rc['title']}")
