"""GET /api/tickers -> [[ticker, company, exchange], ...] for the search suggestions (cached for a day)."""
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _engine as E  # noqa: E402

LISTED = {"Nasdaq", "NYSE", "NYSE American", "NYSE Arca", "CBOE", "BATS"}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            rows = [[t, n, x] for t, (c, n, x) in E.directory().items() if x in LISTED]
            rows.sort(key=lambda r: (len(r[0]), r[0]))
            data, status, cache = json.dumps(rows, separators=(",", ":")).encode(), 200, \
                "public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800"
        except Exception:
            traceback.print_exc()
            data, status, cache = b"[]", 503, "no-store"
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
