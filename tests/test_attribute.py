"""M2 tests on a synthetic session with hand-computable numbers."""
import json

import analyze_tokens as at
import parse
from attribute import attribute


def build_session(tmp_path):
    """4 assistant turns. ctx = input + cache_create + cache_read.

    t0: ctx 1000, out 100, calls Read(a.py) [8000 chars] and Bash(ls) [2000 chars] in parallel
    t1: ctx 1000+100+2500 = 3600 (results 10000 chars = 2500 tokens), out 50, re-reads a.py [8000 chars]
    t2: ctx 3600+50+2000 = 5650, out 10, Read(big.log) [40000 chars]
    t3: ctx 5650+10+10000 = 15660, out 20, end_turn
    """
    recs = []
    def asst(i, mid, ctx, out, blocks, ts, create=0):
        recs.append({"type": "assistant", "uuid": f"a{i}", "sessionId": "s1", "timestamp": ts,
                     "message": {"id": mid, "model": "m", "role": "assistant", "content": blocks,
                                 "stop_reason": "tool_use", "usage": {
                                     "input_tokens": 10, "output_tokens": out,
                                     "cache_creation_input_tokens": create, "cache_read_input_tokens": ctx - 10 - create}}})
    def res(i, results, ts):
        recs.append({"type": "user", "uuid": f"u{i}", "sessionId": "s1", "timestamp": ts,
                     "message": {"role": "user", "content": [
                         {"type": "tool_result", "tool_use_id": tid, "content": "x" * n} for tid, n in results]}})
    use = lambda tid, name, inp: {"type": "tool_use", "id": tid, "name": name, "input": inp}
    asst(0, "m0", 1000, 100, [use("t1", "Read", {"file_path": "/p/a.py"}), use("t2", "Bash", {"command": "ls"})], "2026-01-01T00:00:01Z")
    res(0, [("t1", 8000), ("t2", 2000)], "2026-01-01T00:00:02Z")
    asst(1, "m1", 3600, 50, [use("t3", "Read", {"file_path": "/p/a.py"})], "2026-01-01T00:00:03Z")
    res(1, [("t3", 8000)], "2026-01-01T00:00:04Z")
    asst(2, "m2", 5650, 10, [use("t4", "Read", {"file_path": "/p/big.log"})], "2026-01-01T00:00:05Z", create=4000)
    res(2, [("t4", 40000)], "2026-01-01T00:00:06Z")
    asst(3, "m3", 15660, 20, [{"type": "text", "text": "done"}], "2026-01-01T00:00:07Z")
    root = tmp_path / "projects" / "-p"
    root.mkdir(parents=True)
    (root / "s1.jsonl").write_text("\n".join(json.dumps(r) for r in recs))
    return parse.parse_dir(tmp_path / "projects")


def test_attribution_numbers(tmp_path):
    r = build_session(tmp_path)
    c = attribute(r.tool_calls, r.usage).set_index("tool_use_id")
    assert c.loc["t1", "est_tokens"] == 2000 and c.loc["t2", "est_tokens"] == 500
    # turn 0 -> 1: 3600 - 1000 - 100 (own output) = 2500, split 8000:2000 chars
    assert round(c.loc["t1", "next_ctx_delta"]) == 2000 and round(c.loc["t2", "next_ctx_delta"]) == 500
    assert c.loc["t1", "remaining_turns"] == 3 and c.loc["t1", "carried_tokens"] == 6000
    assert c.loc["t4", "remaining_turns"] == 1 and c.loc["t4", "carried_tokens"] == 10000


def test_redundancy_counts_only_later_occurrences(tmp_path):
    r = build_session(tmp_path)
    red = at.redundancy(attribute(r.tool_calls, r.usage))
    row = red[red.target == "/p/a.py"].iloc[0]
    assert row.times == 2 and row.wasted_carried_tokens == 2000 * 2  # second read: 2000 tok x 2 turns left
    assert row.kind.startswith("re-read")


def test_recommendations_ranked_and_evidenced(tmp_path):
    r = build_session(tmp_path)
    rep = at.build(r)
    recs = rep["recommendations"]
    assert recs == sorted(recs, key=lambda x: x["est_tokens_saved"], reverse=True)
    big = next(x for x in recs if x["title"].startswith("Narrow"))
    # big.log: (40000-5000)/4 chars->tokens = 8750 x 1 turn carried
    assert big["est_tokens_saved"] == 8750 and "big.log" in big["evidence"][0]
    assert any(x["title"].startswith("Stop re-reading") for x in recs)


def test_cache_bust_detected(tmp_path):
    r = build_session(tmp_path)
    cache = at.cache_efficiency(r.usage)
    assert int(cache.busts.iloc[0]) == 1 and int(cache.bust_write_tokens.iloc[0]) == 4000


def test_markdown_report_renders_and_redacts(tmp_path):
    r = build_session(tmp_path)
    md = at.render_markdown(at.build(r))
    for h in ("Recommended changes", "largest tool results", "Redundant calls", "Cache efficiency", "By tool"):
        assert h in md
    assert "estimates" in md


def test_edits_are_not_flagged_as_redundant(tmp_path):
    r = build_session(tmp_path)
    c = attribute(r.tool_calls, r.usage)
    c.loc[c.tool_use_id.isin(["t1", "t3"]), "tool"] = "Edit"  # same file touched twice by Edit
    assert at.redundancy(c).query("target == '/p/a.py'").empty


def test_large_result_from_other_tool_gets_a_recommendation(tmp_path):
    r = build_session(tmp_path)
    c = attribute(r.tool_calls, r.usage)
    c.loc[c.tool_use_id == "t4", "tool"] = "Edit"  # the 40k-char result now comes from an Edit
    recs = at.recommendations(c, at.redundancy(c), at.context_growth(r.usage), at.cache_efficiency(r.usage))
    other = next(x for x in recs if "other-tool" in x["title"])
    assert other["est_tokens_saved"] == 8750 and "Edit" in other["evidence"][0]
