"""Local preview without the network: python3 tools/dev_server.py [port]
Serves the page (with its link-preview tags, as on Vercel), /api/score from out/<TICKER>.json, /api/tickers from
those files, and /api/rules. Run python3 tools/fixtures.py first for made-up companies to look at."""
import json
import os
import re
import sys
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(ROOT, "out")
sys.path.insert(0, os.path.join(ROOT, "api"))
import _engine as E  # noqa: E402
import page as P  # noqa: E402


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def send_body(self, data, ctype, status=200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, status=200):
        self.send_body(json.dumps(obj).encode(), "application/json", status)

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
        if u.path == "/api/rules":
            return self.send_json(E.methodology())
        m = re.match(r"^/([A-Za-z0-9-]{1,12})(?:/vs/([A-Za-z0-9-]{1,12}))?/?$", u.path)
        if m and u.path != "/api":
            html = P.render(open(os.path.join(ROOT, "index.html"), encoding="utf-8").read(), P.norm(m.group(1)),
                            P.norm(m.group(2) or ""), f"http://{self.headers.get('host')}")
            return self.send_body(html.encode(), "text/html; charset=utf-8")
        return super().do_GET()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
