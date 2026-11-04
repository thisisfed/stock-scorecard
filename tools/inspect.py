"""Show what a company files: python3 tools/inspect.py TICKER [regex]
Lists every XBRL item matching the regex (default: debt, shares, revenue, operating income) with its latest values."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))
import _engine as E  # noqa: E402

t = E.norm_ticker(sys.argv[1])
pat = re.compile(sys.argv[2] if len(sys.argv) > 2 else
                 r"Debt|Borrow|Notes|Credit|SharesOutstanding|Revenue|OperatingIncome|EarningsPerShare|BeforeIncomeTax", re.I)
cik, name, ex = E.directory()[t]
cf = E.sec_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
sub = E.sec_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
forms = sub["filings"]["recent"]["form"]
print(f"{name} CIK {cik} SIC {sub.get('sic')} {sub.get('sicDescription')}; recent forms: "
      + ", ".join(sorted({f for f in forms if f in ('10-K', '10-Q', '20-F', '40-F', '6-K', '8-K')})))
print("currency:", E.reporting_currency(cf))
for ns, tags in cf["facts"].items():
    for tag, node in sorted(tags.items()):
        if not pat.search(tag):
            continue
        for unit, items in node["units"].items():
            items = [i for i in items if E._ok_form(i.get("form"))]
            if not items:
                continue
            latest = max(i["end"] for i in items)
            last = sorted([i for i in items if i["end"] == latest], key=lambda i: i.get("filed", ""))[-3:]
            vals = "; ".join(f"{i.get('start', '')}..{i['end']}={i['val']:,} ({i['form']} {i.get('filed')})" for i in last)
            print(f"{ns}:{tag} [{unit}] {vals}")
