import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return [json.loads(line) for line in (FIXTURES / name).read_text().splitlines() if line.strip()]


def test_fixtures_are_valid_jsonl():
    for path in FIXTURES.glob("*.jsonl"):
        assert load(path.name)


def test_basic_fixture_has_streamed_duplicate_usage():
    ids = [r["message"]["id"] for r in load("session_basic.jsonl") if r["type"] == "assistant"]
    assert ids.count("msg_A") == 2


def test_basic_fixture_tool_use_matches_result():
    recs = load("session_basic.jsonl")
    uses = {b["id"] for r in recs if r["type"] == "assistant" for b in r["message"]["content"] if b["type"] == "tool_use"}
    results = {b["tool_use_id"] for r in recs if r["type"] == "user" and isinstance(r["message"]["content"], list)
               for b in r["message"]["content"] if b["type"] == "tool_result"}
    assert uses == results
