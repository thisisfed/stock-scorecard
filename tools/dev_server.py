"""Local preview without the network: python3 tools/dev_server.py [port]
Serves the page, /api/score from out/<TICKER>.json, and /api/tickers from those files."""
import json
import os
import sys
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "out")


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def send_json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/score":
            t = (urllib.parse.parse_qs(u.query).get("t") or [""])[0].upper()
            f = os.path.join(OUT, t + ".json")
            return self.send_json(json.load(open(f))) if os.path.exists(f) else \
                self.send_json({"error": f"{t} isn't in the SEC's list of companies."}, 404)
        if u.path == "/api/tickers":
            rows = []
            for fn in sorted(os.listdir(OUT)) if os.path.isdir(OUT) else []:
                if fn.endswith(".json"):
                    j = json.load(open(os.path.join(OUT, fn)))
                    rows.append([j["ticker"], j["name"], j["exchange"]])
            return self.send_json(rows)
        if u.path not in ("/", "/favicon.svg") and "." not in u.path:
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
