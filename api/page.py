"""GET /AAPL or /AAPL/vs/MSFT (rewritten to /api/page) -> the page, with link-preview tags for that score.
Chat apps and social sites don't run the page's script, so the title and share image are put in the HTML here."""
import html
import os
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler

OK = re.compile(r"^[A-Z0-9-]{1,12}$")
START, END = "<!--meta-->", "<!--/meta-->"


def _read_page():
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, "..", "index.html"), os.path.join(os.getcwd(), "index.html")):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                return fh.read()
    return None


def norm(t):
    t = re.sub(r"[^A-Z0-9-]", "", (t or "").strip().upper().replace(".", "-").replace("/", "-"))[:12]
    return t if OK.match(t) else ""


def meta(t, vs, origin):
    if vs:
        title = f"{t} vs {vs}: Stock Scorecard"
        desc = f"{t} and {vs} scored side by side, 0–10 on growth, profitability, balance sheet, dilution, valuation and momentum."
        path, img = f"/{t}/vs/{vs}", f"/api/og?t={t}&vs={vs}"
    else:
        title = f"{t} scored 0–10: Stock Scorecard"
        desc = f"An independent 0–10 score for {t}, built from its SEC filings and share price. Not investment advice."
        path, img = f"/{t}", f"/api/og?t={t}"
    e = lambda s: html.escape(s, quote=True)
    return (f'<title>{e(title)}</title>\n<meta name="description" content="{e(desc)}">\n'
            f'<link rel="canonical" href="{e(origin + path)}">\n'
            f'<meta property="og:type" content="website"><meta property="og:site_name" content="Stock Scorecard">\n'
            f'<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">\n'
            f'<meta property="og:url" content="{e(origin + path)}"><meta property="og:image" content="{e(origin + img)}">\n'
            f'<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">\n'
            f'<meta name="twitter:card" content="summary_large_image"><meta name="twitter:image" content="{e(origin + img)}">')


def render(page, t, vs, origin):
    a, b = page.find(START), page.find(END)
    if a < 0 or b < a:
        return page
    return page[:a] + START + "\n" + meta(t, vs, origin) + "\n" + page[b:]


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        t, vs = norm((q.get("t") or [""])[0]), norm((q.get("vs") or [""])[0])
        page = _read_page()
        if not page or not t:  # can't build it here: let the static page do the work
            self.send_response(302)
            self.send_header("Location", "/" + ("?t=" + t + ("&vs=" + vs if vs else "") if t else ""))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        host = self.headers.get("x-forwarded-host") or self.headers.get("host") or "stock-scorecard-eosin.vercel.app"
        proto = self.headers.get("x-forwarded-proto") or "https"
        data = render(page, t, vs if vs and vs != t else "", f"{proto}://{host}").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "public, max-age=0, s-maxage=3600, stale-while-revalidate=86400")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
