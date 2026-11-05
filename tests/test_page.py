"""Link-preview tags and the method data: python3 tests/test_page.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))
import _engine as E  # noqa: E402
import page as P  # noqa: E402

html = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "index.html"), encoding="utf-8").read()
assert P.START in html and P.END in html, "index.html needs the <!--meta--> block"
out = P.render(html, "AAPL", "", "https://x.app")
assert "<title>AAPL scored 0–10: Stock Scorecard</title>" in out and 'content="https://x.app/api/og?t=AAPL"' in out
assert out.count("<title>") == html.count("<title>") and "Any stock," in out
out = P.render(html, "AAPL", "MSFT", "https://x.app")
assert "AAPL vs MSFT" in out and "/api/og?t=AAPL&amp;vs=MSFT" in out and 'href="https://x.app/AAPL/vs/MSFT"' in out
assert P.norm("brk.b") == "BRK-B" and P.norm("<script>") == "SCRIPT" and P.norm("") == ""
print("page meta OK")

m = E.methodology()
assert sum(p["weight"] for p in m["models"]["operating"]) == 100 == sum(p["weight"] for p in m["models"]["financial"])
assert m["thresholds"]["software"]["gross_margin"] == (0.50, 0.82) and m["thresholds"]["other"]["gross_margin"] == (0.20, 0.60)
assert all(k in m["measures"] for p in m["models"]["operating"] for k in p["measures"])
assert "blank_check" not in m["thresholds"] and m["grades"][0]["grade"] == "A"
print("method data OK\nALL OK")
