"""Local web UI for browsing and interpreting Claude Code session logs.

Binds to 127.0.0.1 only. Serves JSON from analysis.py plus one static page.
Run: python src/server.py [--root ~/.claude/projects] [--port 8765] [--no-redact]
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import analysis
import analyze_tokens
import parse

STATIC = Path(__file__).parent / "static"


class State:
    def __init__(self, root: Path, do_redact: bool):
        self.root, self.do_redact = root, do_redact
        self.lock = threading.Lock()
        self.result = None
        self.reload()

    def reload(self):
        with self.lock:
            self.result = parse.parse_dir(self.root)


def make_handler(state: State):
    r_kw = lambda: {"do_redact": state.do_redact}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep terminal quiet
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            project, session = q.get("project") or None, q.get("session") or None
            r = state.result
            try:
                if u.path in ("/", "/index.html"):
                    body = (STATIC / "index.html").read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif u.path == "/api/meta":
                    self._json({"stats": {**r.stats, "unknown_types": dict(r.stats["unknown_types"])},
                                "redact": state.do_redact, "root": str(state.root)})
                elif u.path == "/api/projects":
                    self._json(analysis.projects(r))
                elif u.path == "/api/sessions":
                    self._json(analysis.sessions(r, project))
                elif u.path == "/api/overview":
                    self._json(analysis.overview(r, project, session))
                elif u.path == "/api/insights":
                    self._json(analysis.insights(r, project, session))
                elif u.path == "/api/recommendations":
                    self._json(analyze_tokens.build(r, project, session, **r_kw())["recommendations"])
                elif u.path == "/api/context":
                    self._json(analysis.context_series(r, project, session))
                elif u.path == "/api/tools":
                    self._json(analysis.tool_summary(r, project, session))
                elif u.path == "/api/top":
                    self._json(analysis.top_results(r, project, session, **r_kw()))
                elif u.path == "/api/repeats":
                    self._json(analysis.repeated_calls(r, project, session, **r_kw()))
                elif u.path == "/api/trace":
                    self._json(analysis.trace(r, project, session, **r_kw()))
                elif u.path == "/api/exposure":
                    self._json(analysis.exposure(r, project, session, **r_kw()))
                elif u.path == "/api/reload":
                    state.reload()
                    self._json({"ok": True})
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:  # surface errors to the UI without crashing the server
                self._json({"error": type(e).__name__, "detail": str(e)}, 500)

    return Handler


def serve(root: Path, port: int, do_redact: bool = True) -> ThreadingHTTPServer:
    state = State(root, do_redact)
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(state))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=parse.DEFAULT_ROOT)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-redact", action="store_true", help="show raw targets/previews (local only)")
    a = ap.parse_args()
    srv = serve(a.root, a.port, not a.no_redact)
    print(f"Serving {a.root} at http://127.0.0.1:{a.port}  (redaction {'off' if a.no_redact else 'on'})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
