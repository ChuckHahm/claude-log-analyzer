"""Per-tool-call token attribution (M2).

All per-result token figures are ESTIMATES:
  est_tokens      = result_chars / 4
  next_ctx_delta  = growth of context on the next assistant turn, minus the model's own
                    output on the calling turn (which re-enters context as input). Split across
                    parallel calls in one message by result size. Can be noisy or negative.
  carried_tokens  = est_tokens * remaining turns in the session: a result is re-sent on every
                    later turn (mostly as cheap cache reads) until the session ends or compacts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CHARS_PER_TOKEN = 4


def context_tokens(u: pd.DataFrame) -> pd.Series:
    """Total input-side tokens of a request: uncached + cache write + cache read."""
    return u.input_tokens + u.cache_creation_input_tokens + u.cache_read_input_tokens


def turns_table(usage: pd.DataFrame) -> pd.DataFrame:
    """One row per assistant message, ordered within session, with turn index and next-turn context."""
    u = usage.sort_values(["session_id", "timestamp"], kind="stable").reset_index(drop=True).copy()
    u["ctx"] = context_tokens(u)
    g = u.groupby("session_id", sort=False)
    u["turn"] = g.cumcount()
    u["n_turns"] = g.turn.transform("size")
    u["next_ctx"] = g.ctx.shift(-1)
    u["msg_delta"] = u.next_ctx - u.ctx - u.output_tokens
    return u


def attribute(calls: pd.DataFrame, usage: pd.DataFrame) -> pd.DataFrame:
    """Return calls with est_tokens, next_ctx_delta, remaining_turns, carried_tokens added."""
    if calls.empty:
        return calls
    c = calls.copy()
    if "result_chars" not in c:
        c["result_chars"] = 0
    c["result_chars"] = c.result_chars.fillna(0).astype(int)
    c["est_tokens"] = (c.result_chars / CHARS_PER_TOKEN).round().astype(int)
    c["next_ctx_delta"] = np.nan
    c["remaining_turns"] = np.nan
    c["carried_tokens"] = np.nan
    if usage.empty:
        return c
    t = turns_table(usage)[["session_id", "message_id", "turn", "n_turns", "msg_delta"]]
    c = c.drop(columns=["next_ctx_delta", "remaining_turns", "carried_tokens"]).merge(
        t, on=["session_id", "message_id"], how="left")
    key = ["session_id", "message_id"]
    chars_in_msg = c.groupby(key).result_chars.transform("sum")
    calls_in_msg = c.groupby(key).tool_use_id.transform("size")
    share = np.where(chars_in_msg > 0, c.result_chars / chars_in_msg.replace(0, 1), 1 / calls_in_msg)
    c["next_ctx_delta"] = c.msg_delta * share
    c["remaining_turns"] = c.n_turns - c.turn - 1
    c["carried_tokens"] = c.est_tokens * c.remaining_turns
    return c.drop(columns=["msg_delta", "n_turns", "turn"])
