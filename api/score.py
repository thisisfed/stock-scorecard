"""GET /api/score?t=AAPL -> the scorecard for one ticker, as JSON (cached at the edge for an hour)."""
import json
import os
import sys
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _engine as E  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        t = (q.get("t") or q.get("ticker") or [""])[0]
        try:
            body, status = E.score_ticker(t), 200
            cache = "public, max-age=300, s-maxage=3600, stale-while-revalidate=86400"
        except E.ScoreError as e:
            body, status = {"error": str(e)}, e.status
            cache = "public, s-maxage=900" if status in (400, 404, 422) else "no-store"
        except Exception:
            traceback.print_exc()
            body, status = {"error": "Something went wrong while scoring this ticker. Try again in a minute."}, 500
            cache = "no-store"
        data = json.dumps(body, default=str, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
