"""GET /api/rules -> pillars, weights, grades and every measure's thresholds by industry (cached for a day)."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _engine as E  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        data = json.dumps(E.methodology(), separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
