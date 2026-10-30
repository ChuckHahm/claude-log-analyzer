import json
import threading
import urllib.request
from pathlib import Path

import pytest

import analysis
import parse
import server

ROOT = Path(__file__).parent / "fixtures" / "projects"


@pytest.fixture(scope="module")
def base():
    srv = server.serve(ROOT, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address
    assert host == "127.0.0.1"
    yield f"http://{host}:{port}"
    srv.shutdown()


def get(base, path):
    with urllib.request.urlopen(base + path) as r:
        return json.loads(r.read())


def test_project_and_session_dropdown_data(base):
    projs = get(base, "/api/projects")
    assert [p["project"] for p in projs] == ["-home-test-proj"]
    sess = get(base, "/api/sessions?project=-home-test-proj")
    assert [s["session_id"] for s in sess] == ["fix-1"]
    assert get(base, "/api/sessions?project=nope") == []


def test_overview_dedupes_usage(base):
    o = get(base, "/api/overview?project=-home-test-proj")
    assert o["assistant_messages"] == 2 and o["output_tokens"] == 70


def test_every_endpoint_responds(base):
    for ep in ("meta", "insights", "recommendations", "context", "tools", "top", "repeats", "trace", "exposure"):
        get(base, f"/api/{ep}?project=-home-test-proj&session=fix-1")  # a 500 raises HTTPError


def test_sensitive_path_flagged(base):
    e = get(base, "/api/exposure?project=-home-test-proj")
    assert any(".env" in f["target"] for f in e["flagged"])
    assert "logging proxy" in e["caveat"]


def test_index_served(base):
    with urllib.request.urlopen(base + "/") as r:
        assert b"Claude Log Analyzer" in r.read()


def test_redaction():
    assert "FAKE" not in analysis.redact("API_KEY=sk-FAKE-0000000000000000")
    assert "hunter2" not in analysis.redact("password=hunter2")
    assert analysis.redact("plain text") == "plain text"
