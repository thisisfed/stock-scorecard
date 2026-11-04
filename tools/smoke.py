"""Test the live site: python3 tools/smoke.py https://your-site.vercel.app"""
import json
import sys
import time
import urllib.error
import urllib.request

base = sys.argv[1].rstrip("/")
fails = 0


def fetch(path):
    t0 = time.time()
    req = urllib.request.Request(base + path, headers={"User-Agent": "smoke-test"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, r.read(), dict(r.headers), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers), time.time() - t0


for path, want in (("/", b"Stock Scorecard"), ("/AAPL", b"Stock Scorecard"), ("/favicon.svg", b"<svg")):
    st, body, h, s = fetch(path)
    ok = st == 200 and want in body
    fails += not ok
    print(f"{'OK  ' if ok else 'FAIL'} {path:<14} {st} {s:.1f}s")
st, body, h, s = fetch("/api/tickers")
rows = json.loads(body or b"[]") if st == 200 else []
ok = st == 200 and len(rows) > 5000
fails += not ok
print(f"{'OK  ' if ok else 'FAIL'} /api/tickers   {st} {s:.1f}s {len(rows)} tickers, cache: {h.get('Cache-Control')}")
for t in ("AAPL", "JPM", "OPEN", "XOM", "RY", "NOPE123"):
    st, body, h, s = fetch(f"/api/score?t={t}")
    j = json.loads(body or b"{}")
    if t == "NOPE123":
        ok = st == 404 and "error" in j
        print(f"{'OK  ' if ok else 'FAIL'} {t:<8} {st} {s:.1f}s {j.get('error', '')[:70]}")
    else:
        ok = st == 200 and j.get("score", {}).get("overall") is not None
        px = (j.get("price") or {}).get("source")
        print(f"{'OK  ' if ok else 'FAIL'} {t:<8} {st} {s:.1f}s score {j.get('score', {}).get('overall')} price from {px} "
              f"warnings {len(j.get('warnings', []))} cache: {h.get('x-vercel-cache', h.get('X-Vercel-Cache'))} {j.get('error', '')}")
        if j.get("warnings"):
            print("       " + " | ".join(w[:90] for w in j["warnings"]))
    fails += not ok
sys.exit(1 if fails else 0)
