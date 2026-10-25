from pathlib import Path

import parse

FIX = Path(__file__).parent / "fixtures" / "session_basic.jsonl"


def result():
    return parse.parse_files([FIX], FIX.parent)


def test_usage_deduped_by_message_id():
    u = result().usage
    assert sorted(u.message_id) == ["msg_A", "msg_B"]
    assert u.output_tokens.sum() == 70  # msg_A counted once (50) + msg_B (20)


def test_tool_call_joined_to_result():
    t = result().tool_calls
    assert len(t) == 1
    row = t.iloc[0]
    assert row.tool == "Read" and row.result_chars > 0 and not row.is_error


def test_unknown_types_counted_not_fatal():
    r = result()
    assert r.stats["unknown_types"]["mystery-record"] == 1


def test_usage_matches_cost_state():
    v = parse.validate_usage(result())
    assert (v.delta == 0).all()


def test_skips_bad_lines(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('{"type":"user","uuid":"x","message":{"content":"hi"}}\n\nnot json\n{"type":"user"')
    r = parse.parse_files([p], tmp_path)
    assert r.stats["records"] == 1 and r.stats["skipped_lines"] == 3


def test_content_string_or_list():
    assert parse.blocks("hi")[0]["text"] == "hi"
    assert parse.blocks(None) == []
    assert parse.result_size([{"type": "image"}, {"type": "text", "text": "abc"}]) == (3, 1)
