#!/usr/bin/env python3
"""
Stock Scorecard engine: scores any company that files with the SEC, 0-10, from its filings and share price.

Sources (all free, read live when someone asks for a ticker):
  SEC EDGAR   ticker directory, company profile (industry code), XBRL financial statements
  Prices      Yahoo Finance, then Nasdaq, then Stooq (whichever answers first)

Standard library only, so the same file runs on Vercel's Python runtime and on the command line:
  python3 api/_engine.py AAPL            # prints the report
  python3 api/_engine.py AAPL --json     # prints the JSON the website uses

Set SEC_UA to "Your Name your@email" (the SEC asks every client to identify itself).
Not investment advice: the thresholds below are judgement calls, not back-tested.
"""
import datetime as dt
import gzip
import json
import math
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

SEC_UA = os.environ.get("SEC_UA") or "Stock Scorecard research contact@example.com"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
VERSION = "1"


class ScoreError(Exception):
    """A problem worth showing to the visitor (unknown ticker, no filings...)."""
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- HTTP and a small in-memory cache
def get(url, ua=BROWSER_UA, timeout=25, headers=None):
    h = {"User-Agent": ua, "Accept-Encoding": "gzip"}
    h.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding", "") == "gzip" or data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
    return data.decode("utf-8", "replace")


def sec_json(url):
    return json.loads(get(url, ua=SEC_UA, timeout=40))


_CACHE, _LOCK = {}, threading.Lock()


def cached(key, ttl, fn):
    """Keep results while the server stays warm, so repeat visits don't hit the SEC again."""
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
    value = fn()
    with _LOCK:
        _CACHE[key] = (now + ttl, value)
        if len(_CACHE) > 300:  # keep memory bounded
            for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:100]:
                _CACHE.pop(k, None)
    return value


# ---------------------------------------------------------------- ticker directory
def norm_ticker(t):
    return re.sub(r"[^A-Z0-9-]", "", (t or "").strip().upper().replace(".", "-").replace("/", "-"))[:12]


def directory():
    """{ticker: (cik, name, exchange)} for every company in the SEC's ticker file."""
    def load():
        j = sec_json("https://www.sec.gov/files/company_tickers_exchange.json")
        f = j["fields"]
        ci, ni, ti, ei = f.index("cik"), f.index("name"), f.index("ticker"), f.index("exchange")
        out = {}
        for row in j["data"]:
            t = norm_ticker(row[ti])
            if t and t not in out:
                out[t] = (int(row[ci]), row[ni], row[ei] or "")
        return out
    return cached("directory", 6 * 3600, load)


# ---------------------------------------------------------------- dates
def _date(s):
    return dt.date.fromisoformat(s[:10])


def days(a, b):
    return (_date(b) - _date(a)).days


# ---------------------------------------------------------------- SEC XBRL facts
FILING_FORMS = {"10-K", "10-Q", "20-F", "40-F", "10-KT", "10-QT"}


def _ok_form(form):
    return (form or "").split("/")[0] in FILING_FORMS


def reporting_currency(cf):
    """The currency of the company's statements (most US filers: USD)."""
    latest = {}
    for ns in ("us-gaap", "ifrs-full"):
        for tag in ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenue", "Assets", "NetIncomeLoss", "ProfitLoss"):
            for unit, items in cf.get("facts", {}).get(ns, {}).get(tag, {}).get("units", {}).items():
                if re.fullmatch(r"[A-Z]{3}", unit):
                    end = max((i["end"] for i in items if _ok_form(i.get("form"))), default="")
                    latest[unit] = max(latest.get(unit, ""), end)
    if not latest:
        return "USD"
    return max(latest, key=lambda u: (latest[u], u == "USD"))


def series(cf, tags, unit):
    """Facts for a concept that companies tag in different ways: {(start, end): value}.
    Within a tag the latest filing wins (restatements). Across tags, the tag the company uses now wins,
    and older tags fill in periods it doesn't cover (companies switch tags over the years)."""
    facts = cf.get("facts", {})
    cands = []
    for prio, tag in enumerate(tags):
        for ns in ("us-gaap", "ifrs-full"):
            items = facts.get(ns, {}).get(tag, {}).get("units", {}).get(unit)
            if not items:
                continue
            best = {}
            for it in items:
                if not _ok_form(it.get("form")):
                    continue
                k = (it.get("start"), it["end"])
                if k not in best or it.get("filed", "") > best[k].get("filed", ""):
                    best[k] = it
            if best:
                latest = max(k[1] for k in best)
                cands.append((latest, -prio, f"{ns}:{tag}", {k: v["val"] for k, v in best.items()}))
    out, used = {}, []
    for latest, _, name, vals in sorted(cands, key=lambda c: (c[0][:7], c[1]), reverse=True):
        added = False
        for k, v in vals.items():
            if k not in out:
                out[k], added = v, True
        if added:
            used.append(name)
    return out, used


def quarterly(s):
    """Quarter values {end: value} from 3-month facts, or by subtracting cumulative ones (YTD, full year)."""
    q = {e: v for (st, e), v in s.items() if st and 80 <= days(st, e) <= 100}
    for _ in range(4):
        grew = False
        for (st, e), v in s.items():
            if not st or e in q or not 170 <= days(st, e) <= 380:
                continue
            prev = [(e2, v2) for (s2, e2), v2 in s.items() if s2 == st and 80 <= days(e2, e) <= 100]
            if prev:
                q[e], grew = v - prev[0][1], True
                continue
            # full year with its first three quarters reported separately
            inner = sorted(x for x in q if st < x < e and days(st, x) >= 80)
            if days(st, e) >= 350 and len(inner) == 3 and 80 <= days(inner[-1], e) <= 100:
                q[e], grew = v - sum(q[x] for x in inner), True
        if not grew:
            break
    return q


def annual(s):
    return {e: v for (st, e), v in s.items() if st and 350 <= days(st, e) <= 380}


def chain(q, end, n=4):
    """n consecutive quarter ends finishing at end (newest first), or None."""
    out, cur = [end], end
    if end not in q:
        return None
    for _ in range(n - 1):
        prev = [e for e in q if 80 <= days(e, cur) <= 100]
        if not prev:
            return None
        cur = max(prev)
        out.append(cur)
    return out


def ttm(q, a, end):
    """Trailing twelve months to `end`: the annual figure at a year end, else four quarters."""
    if end in a:
        return a[end]
    c = chain(q, end)
    return sum(q[e] for e in c) if c else None


def year_before(keys, end, tol=20):
    """The period end closest to one year before `end`."""
    best = [k for k in keys if abs(days(k, end) - 365) <= tol]
    return min(best, key=lambda k: abs(days(k, end) - 365)) if best else None


def instants(cf, tags, unit):
    s, used = series(cf, tags, unit)
    return {e: v for (st, e), v in s.items() if not st}, used


def at(inst, end, window=100):
    """Value at `end`, or the latest one in the `window` days before it."""
    if end in inst:
        return inst[end]
    prior = [e for e in inst if 0 <= days(e, end) <= window]
    return inst[max(prior)] if prior else None


FLOW_TAGS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet",
                "SalesRevenueServicesNet", "RevenuesNetOfInterestExpense", "Revenue",
                "RevenueFromContractsWithCustomers"],
    "gross_profit": ["GrossProfit"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices",
                        "CostOfSales"],
    "operating_income": ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"],
    "net_income": ["NetIncomeLoss", "ProfitLossAttributableToOwnersOfParent", "ProfitLoss",
                   "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
              "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets"],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense",
            "AdjustmentsForSharebasedPayments"],
    "dna": ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
            "DepreciationAndAmortization", "Depreciation", "DepreciationAndAmortisationExpense"],
    "interest": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt",
                 "InterestPaidNet", "FinanceCosts"],
}
PRETAX_TAGS = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic", "ProfitLossBeforeTax"]
DEBT_ACTIVITY_TAGS = ["ProceedsFromIssuanceOfLongTermDebt", "RepaymentsOfLongTermDebt", "ProceedsFromIssuanceOfDebt",
                      "RepaymentsOfDebt", "ProceedsFromIssuanceOfSeniorLongTermDebt", "ProceedsFromRepaymentsOfShortTermDebt"]
# Companies whose per-share figures are in one class's terms while the ticker is another class
CLASS_FACTOR = {"BRK-B": 1500.0, "BRK-A": 1.0}
CLASS_NOTE = {"BRK-B": "Class A equivalent shares x 1,500 (each Class A share converts into 1,500 Class B)",
              "BRK-A": "Class A equivalent shares"}
INSTANT_TAGS = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"],
    "short_investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent",
                          "AvailableForSaleSecuritiesDebtSecuritiesCurrent", "OtherShortTermInvestments",
                          "CurrentFinancialAssetsAtFairValueThroughProfitOrLoss"],
    "assets": ["Assets"],
    "current_assets": ["AssetsCurrent", "CurrentAssets"],
    "current_liabilities": ["LiabilitiesCurrent", "CurrentLiabilities"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
               "EquityAttributableToOwnersOfParent", "Equity"],
}
# Ways companies tag total borrowings. The first way whose required tags are all filed at the balance-sheet
# date is used; optional tags are added when present. The last way sums separate instruments.
DEBT_WAYS = [
    (["LongTermDebt"], ["ShortTermBorrowings", "CommercialPaper"]),
    (["LongTermDebtNoncurrent", "DebtCurrent"], []),
    (["LongTermDebtNoncurrent"], ["LongTermDebtCurrent", "ShortTermBorrowings", "CommercialPaper"]),
    (["LongTermDebtAndCapitalLeaseObligations", "DebtCurrent"], []),
    (["LongTermDebtAndCapitalLeaseObligations"], ["LongTermDebtAndCapitalLeaseObligationsCurrent",
                                                 "ShortTermBorrowings", "CommercialPaper"]),
    (["DebtLongtermAndShorttermCombinedAmount"], []),
    (["LongTermNotesAndLoans", "NotesPayableCurrent"], []),
    (["LongTermNotesAndLoans", "DebtCurrent"], []),
    (["LongTermNotesAndLoans"], []),
    (["LongTermNotesPayable", "NotesPayableCurrent"], []),
    (["LongTermNotesPayable", "DebtCurrent"], []),
    (["Borrowings"], []),
    (["NoncurrentBorrowings"], ["CurrentBorrowings"]),
    ([], ["ConvertibleDebtNoncurrent", "ConvertibleDebtCurrent", "ConvertibleNotesPayable",
          "LinesOfCreditCurrent", "LongTermLineOfCredit", "SeniorNotes", "NotesPayable", "LongTermNotesPayable",
          "SecuredDebt", "UnsecuredDebt", "OtherLongTermDebtNoncurrent", "ShortTermBorrowings"]),
]


def total_debt(cf, unit, end):
    for required, optional in DEBT_WAYS:
        vals, used = {}, []
        for tag in required + optional:
            inst, u = instants(cf, [tag], unit)
            v = inst.get(end)
            if v is not None:
                vals[tag] = v
                used += u
        if required and all(t in vals for t in required):
            return sum(vals.values()), used
        if not required and vals:
            return sum(vals.values()), used
    return 0.0, []


def shares_now(cf):
    """Shares outstanding from the latest filing's cover page (all share classes added up)."""
    items = cf.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
    by = {}
    for it in items:
        if _ok_form(it.get("form")):
            by.setdefault((it.get("accn"), it["end"]), []).append(it["val"])
    if not by:
        return None, None
    (accn, end), vals = max(by.items(), key=lambda kv: (kv[0][1], kv[0][0] or ""))
    return float(sum(set(vals))), end


def weighted_shares(cf, tags):
    s, _ = series(cf, tags, "shares")
    q = {e: v for (st, e), v in s.items() if st and 80 <= days(st, e) <= 100}
    return q, annual(s)


# ---------------------------------------------------------------- prices
def _yahoo(host, sym):
    j = json.loads(get(f"https://{host}/v8/finance/chart/{sym}?range=2y&interval=1d", timeout=15))
    r = j["chart"]["result"][0]
    cur = (r.get("meta") or {}).get("currency")
    rows = [(dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat(), float(c)) for t, c in
            zip(r["timestamp"], r["indicators"]["quote"][0]["close"]) if c is not None]
    return rows, cur


def _nasdaq(sym):
    start = (dt.date.today() - dt.timedelta(days=740)).isoformat()
    txt = get(f"https://api.nasdaq.com/api/quote/{sym.replace('-', '.')}/historical?assetclass=stocks&fromdate={start}&limit=9999",
              timeout=15, headers={"Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com",
                                   "Referer": "https://www.nasdaq.com/", "Accept-Language": "en-US,en;q=0.9"})
    rows = []
    for r in json.loads(txt)["data"]["tradesTable"]["rows"]:
        m, d, y = r["date"].split("/")
        rows.append((f"{y}-{m}-{d}", float(r["close"].replace("$", "").replace(",", ""))))
    return sorted(rows), "USD"


def _stooq(sym):
    txt = get(f"https://stooq.com/q/d/l/?s={sym.lower()}.us&i=d", timeout=15)
    if not txt.startswith("Date,"):
        raise ValueError("no CSV")
    cut = (dt.date.today() - dt.timedelta(days=740)).isoformat()
    return [(l.split(",")[0], float(l.split(",")[4])) for l in txt.strip().splitlines()[1:] if l.split(",")[0] >= cut], "USD"


def prices(sym):
    """Daily closes: latest price, 52-week range, 1-year return, 200-day average."""
    sources = [("Yahoo Finance", lambda: _yahoo("query1.finance.yahoo.com", sym)),
               ("Yahoo Finance", lambda: _yahoo("query2.finance.yahoo.com", sym)),
               ("Nasdaq", lambda: _nasdaq(sym)), ("Stooq", lambda: _stooq(sym))]
    errors = []
    for name, fetch in sources:
        try:
            rows, cur = fetch()
            if len(rows) < 20:
                raise ValueError(f"only {len(rows)} days")
            closes = [c for _, c in rows]
            yr = closes[-252:]
            return {"date": rows[-1][0], "price": closes[-1], "high_52w": max(yr), "low_52w": min(yr),
                    "return_1y": closes[-1] / closes[-253] - 1 if len(closes) >= 253 else None,
                    "ma200": sum(closes[-200:]) / 200 if len(closes) >= 200 else None,
                    "currency": cur or "USD", "source": name, "days": len(closes),
                    "history": [[d, round(c, 4)] for d, c in rows[-260:]]}
        except Exception as e:
            errors.append(f"{name}: {str(e)[:60]}")
    raise RuntimeError("; ".join(errors))


def fx_rate(cur, to="USD"):
    """Units of `to` per one `cur` (ECB reference rates via Frankfurter; open.er-api.com as backup)."""
    def load():
        for url, pick in ((f"https://api.frankfurter.app/latest?from={cur}&to={to}", lambda j: j["rates"][to]),
                          (f"https://open.er-api.com/v6/latest/{cur}", lambda j: j["rates"][to])):
            try:
                v = float(pick(json.loads(get(url, timeout=10))))
                if v > 0:
                    return v
            except Exception:
                continue
        return None
    return cached(f"fx:{cur}:{to}", 6 * 3600, load)


# ---------------------------------------------------------------- sectors (from the SEC industry code)
SECTORS = [  # key, label, SIC ranges (first match wins)
    ("blank_check", "Blank-check company", [(6770, 6770)]),
    ("consumer", "Consumer brands and services", [(2840, 2844), (3021, 3021), (4700, 4700), (4720, 4729)]),
    ("semis_hw", "Semiconductors and hardware", [(3559, 3559)]),          # incl. chip-making equipment
    ("health", "Health care", [(3826, 3826)]),                            # laboratory instruments
    ("utilities", "Utilities", [(4900, 4999)]),
    ("energy", "Energy and materials", [(4610, 4619)]),                   # pipelines
    ("software", "Software and internet", [(7370, 7379)]),
    ("health", "Health care", [(2833, 2836), (3841, 3851), (8000, 8099), (8731, 8731)]),
    ("semis_hw", "Semiconductors and hardware", [(3570, 3579), (3661, 3679), (3695, 3695), (3823, 3825), (3827, 3829)]),
    ("financial", "Banks, insurance and finance", [(6000, 6499)]),
    ("real_estate", "Real estate and homebuilding", [(1520, 1799), (6500, 6599), (6798, 6798)]),
    ("energy", "Energy and materials", [(1000, 1499), (2800, 2899), (2900, 2999), (3300, 3399)]),
    ("telecom_media", "Telecom and media", [(4800, 4899), (2700, 2799), (7800, 7899)]),
    ("retail", "Retail and wholesale", [(5000, 5999)]),
    ("consumer", "Consumer brands and services", [(2000, 2399), (3140, 3199), (3630, 3651), (3940, 3949),
                                                  (7000, 7299), (7900, 7999)]),
    ("industrial", "Industrials and transport", [(2400, 3999), (4000, 4799), (8700, 8799)]),
    ("other", "Other", [(0, 9999)]),
]


def sector_of(sic):
    try:
        n = int(sic)
    except (TypeError, ValueError):
        return "other", "Other"
    for key, label, ranges in SECTORS:
        if any(a <= n <= b for a, b in ranges):
            return key, label
    return "other", "Other"


# ---------------------------------------------------------------- scoring rules
# metric: (label, value scoring 0, value scoring 10, format). Linear in between, clipped.
RULES = {
    "rev_q_yoy":       ("Revenue, latest quarter vs a year earlier", -0.10, 0.20, "pct"),
    "rev_ttm_yoy":     ("Revenue, last 12 months vs the 12 before", -0.10, 0.20, "pct"),
    "rev_cagr_3y":     ("Revenue growth per year, last 3 years", -0.05, 0.15, "pct"),
    "gross_margin":    ("Gross margin, last 12 months", 0.20, 0.60, "share"),
    "op_margin":       ("Operating margin, last 12 months", 0.00, 0.20, "pct"),
    "op_margin_chg":   ("Operating margin vs a year earlier", -0.05, 0.05, "pp"),
    "fcf_margin":      ("Free cash flow margin, last 12 months", -0.05, 0.15, "pct"),
    "net_debt_ebitda": ("Net debt / EBITDA", 4.0, 0.0, "x"),
    "interest_cover":  ("Operating profit / interest expense", 1.5, 12.0, "x"),
    "current_ratio":   ("Current assets / current liabilities", 0.8, 2.0, "x"),
    "runway_q":        ("Cash runway at the current cash burn", 4.0, 16.0, "qtrs"),
    "share_yoy":       ("Share count vs a year earlier", 0.08, -0.03, "pct"),
    "sbc_pct":         ("Stock-based pay / revenue", 0.15, 0.01, "share"),
    "dilution_itm":    ("Extra shares from options and awards in the money", 0.06, 0.005, "share"),
    "dilution_all":    ("Potential new shares (awards, convertibles, warrants), all counted", 0.30, 0.03, "share"),
    "ev_sales":        ("Enterprise value / sales", 6.0, 1.0, "x"),
    "ev_ebit":         ("Enterprise value / operating profit", 40.0, 10.0, "x"),
    "fcf_yield":       ("Free cash flow yield", 0.0, 0.07, "pct"),
    "pe":              ("Price / earnings", 30.0, 8.0, "x"),
    "pb":              ("Price / book value", 3.0, 0.8, "x"),
    "pe_alt":          ("Price / earnings (debt isn't filed in a standard form)", 30.0, 8.0, "x"),
    "roe":             ("Return on equity, last 12 months", 0.0, 0.15, "pct"),
    "net_margin":      ("Net margin, last 12 months", 0.0, 0.25, "pct"),
    "equity_assets":   ("Equity / total assets", 0.05, 0.15, "share"),
    "ret_1y":          ("Share price, last 12 months", -0.40, 0.40, "pct"),
    "vs_ma200":        ("Price vs 200-day average", -0.20, 0.15, "pct"),
    "range_pos":       ("Position in 52-week range", 0.0, 1.0, "share"),
}
# Industry norms differ: a 20% gross margin is poor for software and normal for a retailer.
SECTOR_RULES = {
    "software":      {"gross_margin": (0.50, 0.82), "op_margin": (-0.10, 0.30), "fcf_margin": (-0.10, 0.30),
                      "ev_sales": (16.0, 3.0), "ev_ebit": (60.0, 15.0), "rev_q_yoy": (0.0, 0.30),
                      "rev_ttm_yoy": (0.0, 0.30), "rev_cagr_3y": (0.0, 0.25), "sbc_pct": (0.25, 0.03)},
    "semis_hw":      {"gross_margin": (0.25, 0.65), "op_margin": (0.0, 0.30), "fcf_margin": (-0.05, 0.25),
                      "ev_sales": (12.0, 1.5), "ev_ebit": (50.0, 12.0), "rev_q_yoy": (-0.10, 0.30),
                      "rev_ttm_yoy": (-0.10, 0.30), "rev_cagr_3y": (-0.05, 0.20)},
    "health":        {"gross_margin": (0.30, 0.80), "op_margin": (-0.20, 0.25), "fcf_margin": (-0.20, 0.20),
                      "ev_sales": (12.0, 2.0), "ev_ebit": (45.0, 12.0), "rev_q_yoy": (-0.05, 0.25),
                      "rev_ttm_yoy": (-0.05, 0.25)},
    "consumer":      {"gross_margin": (0.30, 0.62), "op_margin": (0.03, 0.22), "fcf_margin": (0.0, 0.15),
                      "ev_sales": (5.0, 1.0), "ev_ebit": (32.0, 10.0), "rev_q_yoy": (-0.05, 0.12),
                      "rev_ttm_yoy": (-0.05, 0.12), "rev_cagr_3y": (-0.03, 0.10)},
    "retail":        {"gross_margin": (0.10, 0.40), "op_margin": (0.0, 0.10), "fcf_margin": (-0.01, 0.06),
                      "ev_sales": (2.0, 0.3), "ev_ebit": (32.0, 9.0), "rev_q_yoy": (-0.03, 0.12),
                      "rev_ttm_yoy": (-0.03, 0.12), "rev_cagr_3y": (-0.02, 0.10)},
    "utilities":     {"gross_margin": (0.20, 0.60), "op_margin": (0.08, 0.30), "fcf_margin": (-0.25, 0.10),
                      "ev_sales": (8.0, 2.5), "ev_ebit": (30.0, 13.0), "rev_q_yoy": (-0.05, 0.08),
                      "rev_ttm_yoy": (-0.05, 0.08), "rev_cagr_3y": (-0.02, 0.07), "net_debt_ebitda": (7.0, 3.0),
                      "interest_cover": (1.5, 5.0), "current_ratio": (0.5, 1.2), "runway_q": (2.0, 8.0)},
    "industrial":    {"gross_margin": (0.15, 0.45), "op_margin": (0.0, 0.18), "fcf_margin": (-0.03, 0.12),
                      "ev_sales": (4.0, 0.8), "ev_ebit": (35.0, 10.0), "rev_q_yoy": (-0.05, 0.12),
                      "rev_ttm_yoy": (-0.05, 0.12), "rev_cagr_3y": (-0.03, 0.10)},
    "energy":        {"gross_margin": (0.10, 0.45), "op_margin": (0.0, 0.25), "fcf_margin": (-0.05, 0.15),
                      "ev_sales": (4.0, 0.8), "ev_ebit": (25.0, 7.0), "rev_q_yoy": (-0.15, 0.15),
                      "rev_ttm_yoy": (-0.15, 0.15), "rev_cagr_3y": (-0.05, 0.10), "net_debt_ebitda": (4.5, 0.5)},
    "telecom_media": {"gross_margin": (0.30, 0.65), "op_margin": (0.0, 0.25), "fcf_margin": (-0.03, 0.15),
                      "ev_sales": (5.0, 1.0), "ev_ebit": (35.0, 10.0), "rev_q_yoy": (-0.05, 0.12),
                      "rev_ttm_yoy": (-0.05, 0.12), "net_debt_ebitda": (5.0, 1.0)},
    "real_estate":   {"gross_margin": (0.05, 0.40), "op_margin": (0.0, 0.30), "fcf_margin": (-0.10, 0.15),
                      "ev_sales": (10.0, 1.0), "ev_ebit": (35.0, 10.0), "net_debt_ebitda": (8.0, 2.0)},
    "financial":     {"rev_q_yoy": (-0.05, 0.12), "rev_ttm_yoy": (-0.05, 0.12), "rev_cagr_3y": (-0.03, 0.10),
                      "equity_assets": (0.04, 0.12)},
}

PILLARS = {  # name: (weight %, metrics, question)
    "operating": [
        ("Growth", 20, ["rev_q_yoy", "rev_ttm_yoy", "rev_cagr_3y"], "Is the business getting bigger?"),
        ("Profitability", 25, ["gross_margin", "op_margin", "fcf_margin", "op_margin_chg"],
         "Does it make money, and is that improving?"),
        ("Balance sheet", 15, ["net_debt_ebitda", "interest_cover", "current_ratio", "runway_q"],
         "Could it get through a bad year?"),
        ("Dilution", 10, ["share_yoy", "sbc_pct", "dilution_itm", "dilution_all"], "Is your slice of the company shrinking?"),
        ("Valuation", 20, ["ev_sales", "ev_ebit", "fcf_yield", "pe_alt"], "Are you paying a fair price for what exists today?"),
        ("Momentum", 10, ["ret_1y", "vs_ma200", "range_pos"], "What is the market saying?"),
    ],
    "financial": [
        ("Growth", 20, ["rev_q_yoy", "rev_ttm_yoy", "rev_cagr_3y"], "Is the business getting bigger?"),
        ("Profitability", 25, ["roe", "net_margin"], "Does it earn a good return on its capital?"),
        ("Balance sheet", 15, ["equity_assets"], "How much capital stands behind its assets?"),
        ("Dilution", 10, ["share_yoy", "sbc_pct", "dilution_itm"], "Is your slice of the company shrinking?"),
        ("Valuation", 20, ["pb", "pe"], "Are you paying a fair price for what exists today?"),
        ("Momentum", 10, ["ret_1y", "vs_ma200", "range_pos"], "What is the market saying?"),
    ],
}
PILLAR_WORDS = {  # (strength, weakness)
    "Growth": ("sales are growing", "sales are shrinking or stalling"),
    "Profitability": ("it is solidly profitable", "it struggles to make money"),
    "Balance sheet": ("the balance sheet is strong", "the balance sheet is stretched"),
    "Dilution": ("the share count is under control", "shareholders are being diluted"),
    "Valuation": ("the price looks reasonable", "the shares look expensive"),
    "Momentum": ("the share price is trending up", "the share price is in a downtrend"),
}
GRADES = [(8.0, "A", "Strong: the numbers back the price."),
          (6.5, "B", "Good: solid numbers with manageable risks."),
          (5.0, "C", "Mixed: strengths and weaknesses roughly balance."),
          (3.5, "D", "Weak: more red flags than green ones."),
          (0.0, "F", "Poor: the numbers do not support the price.")]


NO_DEBT, NET_CASH, SELF_FUNDING = 999.0, -1.0, 99.0  # special values with their own wording
OPTIONAL = {"dilution_itm", "dilution_all", "pe_alt"}  # measures shown only where they apply


def lin(x, bad, good):
    if x is None:
        return None
    return round(max(0.0, min(1.0, (x - bad) / (good - bad))) * 10, 1)


def rule(key, sector):
    label, bad, good, f = RULES[key]
    bad, good = SECTOR_RULES.get(sector, {}).get(key, (bad, good))
    return label, bad, good, f


# ---------------------------------------------------------------- the score
def _div(a, b):
    return a / b if a is not None and b not in (None, 0) else None


def _qlabel(end, fy_end=None):
    d = _date(end)
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def build(ticker, cik, name, exchange, sub, cf, px, warnings, fx=None):
    """Turn raw SEC data and prices into metrics, pillar scores and the overall score."""
    sic = sub.get("sic")
    sector, sector_label = sector_of(sic)
    if sector == "blank_check":
        raise ScoreError(f"{name} is a blank-check company (SPAC): it has no operating business to score yet.")
    model = "financial" if sector == "financial" else "operating"
    cur = reporting_currency(cf)
    tags = {}

    # income statement and cash flow items, merged across the tags companies use
    raw = {}
    for key, tl in FLOW_TAGS.items():
        raw[key], used = series(cf, tl, cur)
        if used:
            tags[key] = used
    if not raw["revenue"]:
        raise ScoreError(f"{name} hasn't filed revenue in a form this tool can read (no XBRL income statement).")
    # gross profit: reported, or revenue minus cost of revenue, period by period
    gp_raw = {}
    for k, r in raw["revenue"].items():
        if k in raw["gross_profit"]:
            gp_raw[k] = raw["gross_profit"][k]
        elif k in raw["cost_of_revenue"]:
            gp_raw[k] = r - raw["cost_of_revenue"][k]
    if len(gp_raw) > len(raw["gross_profit"]):
        tags["gross_profit"] = tags.get("gross_profit", []) + ["revenue minus cost of revenue where gross profit isn't reported"]
    raw["gross_profit"] = gp_raw
    # operating profit: reported, or profit before tax plus interest expense where it isn't (an EBIT estimate)
    pre, pre_used = series(cf, PRETAX_TAGS, cur)
    filled = 0
    for k, v in pre.items():
        if k not in raw["operating_income"]:
            raw["operating_income"][k] = v + raw["interest"].get(k, 0.0)
            filled += 1
    if filled:
        tags["operating_income"] = tags.get("operating_income", []) + [
            "profit before tax plus interest expense where operating profit isn't reported (" + ", ".join(pre_used) + ")"]
    flows = {k: (quarterly(v), annual(v)) for k, v in raw.items()}
    rq, ra = flows["revenue"]
    gq, ga = flows["gross_profit"]

    end = max(k[1] for k in raw["revenue"])  # latest reported period
    forms = set(((sub.get("filings") or {}).get("recent") or {}).get("form") or [])
    foreign = "20-F" in forms and "10-K" not in forms
    if days(end, dt.date.today().isoformat()) > (520 if foreign else 400):
        warnings.append(f"The latest financial statements in the SEC's data are for the period ending {_qlabel(end)}, "
                        "so the score may be out of date.")
    # twelve-month figures; a company with a short filing history uses its year to date, scaled to a year
    prev_end = year_before(list(rq) + list(ra), end)
    basis, ytd = ("ttm", None) if ttm(rq, ra, end) is not None else (None, None)
    if basis is None:
        spans = [k for k in raw["revenue"] if k[1] == end and k[0] and 80 <= days(k[0], end) <= 380]
        if not spans:
            raise ScoreError(f"Not enough of {name}'s figures are on file with the SEC to score it yet.")
        ytd, basis = min(spans, key=lambda k: k[0]), "ytd"
        months = round(days(ytd[0], end) / 30.4)
        warnings.append(f"Only {months} months of figures are on file under {name}'s current SEC registration, so "
                        "12-month figures are estimated from the year to date.")
    scale = 365.0 / days(ytd[0], end) if ytd else 1.0
    ytd_prev = None
    if ytd:
        span = days(ytd[0], ytd[1])
        ytd_prev = next((k for k in raw["revenue"] if k[0] and abs(days(k[1], end) - 365) <= 20
                         and abs(days(k[0], k[1]) - span) <= 10), None)

    def T(key, prev=False):
        if basis == "ttm":
            e = prev_end if prev else end
            return ttm(*flows[key], e) if e else None
        k = ytd_prev if prev else ytd
        v = raw[key].get(k) if k else None
        return v * scale if v is not None else None

    rev, rev_prev = T("revenue"), T("revenue", prev=True)
    gp, op, ni = T("gross_profit"), T("operating_income"), T("net_income")
    ocf, capex = T("ocf"), T("capex")
    fcf = ocf - (capex or 0) if ocf is not None else None
    sbc, dna, interest = T("sbc"), T("dna"), T("interest")
    op_prev = T("operating_income", prev=True)
    rev3 = None
    if basis == "ttm":
        end3 = next((k for k in sorted(set(rq) | set(ra), reverse=True) if abs(days(k, end) - 3 * 365) <= 25), None)
        rev3 = ttm(rq, ra, end3) if end3 else None

    # balance sheet at the latest date total assets were reported
    inst = {}
    for key, tl in INSTANT_TAGS.items():
        inst[key], used = instants(cf, tl, cur)
        if used:
            tags[key] = used
    bs_dates = sorted(inst["assets"]) or sorted(inst["equity"])
    bs_end = bs_dates[-1] if bs_dates else end
    bal = {k: at(v, bs_end) for k, v in inst.items()}
    cash = (bal["cash"] or 0) + (bal["short_investments"] or 0)
    debt, debt_tags = total_debt(cf, cur, bs_end)
    if debt_tags:
        tags["debt"] = debt_tags
    else:
        # no standard debt figure: a company that borrows or pays interest has debt filed some other way
        active = False
        for tag in DEBT_ACTIVITY_TAGS:
            ser, _ = series(cf, [tag], cur)
            if any(st and days(e, end) <= 450 and abs(v) > 0.01 * (rev or 0) for (st, e), v in ser.items()):
                active = True
                break
        if active or (interest and rev and interest > 0.003 * rev):
            debt = None
        if debt is None and model == "operating":
            warnings.append(f"{name} doesn't file its total debt in a standard form, so debt-based measures "
                            "(net debt, enterprise value) are left out and valuation uses price / earnings instead.")
    equity = bal["equity"]
    equity_prev = at(inst["equity"], year_before(list(inst["equity"]), bs_end) or "1900-01-01")

    # shares: the cover page count, checked against net income / earnings per share
    cover, cover_date = shares_now(cf)
    basic_q, basic_a = weighted_shares(cf, ["WeightedAverageNumberOfSharesOutstandingBasic", "WeightedAverageShares"])
    dil_q, dil_a = weighted_shares(cf, ["WeightedAverageNumberOfDilutedSharesOutstanding", "AdjustedWeightedAverageShares"])
    basic_end = max(basic_q) if basic_q else (max(basic_a) if basic_a else None)
    basic = (basic_q or basic_a).get(basic_end) if basic_end else None
    implied = None
    eps, _ = series(cf, ["EarningsPerShareBasic", "BasicEarningsLossPerShare"], cur + "/shares")
    nq = flows["net_income"][0]
    epq = {e: v for (st, e), v in eps.items() if st and 80 <= days(st, e) <= 100 and v}
    common = sorted(set(epq) & set(nq))
    if common and nq[common[-1]] and nq[common[-1]] / epq[common[-1]] > 0:
        implied = nq[common[-1]] / epq[common[-1]]
    shares, shares_src = None, ""
    if ticker in CLASS_FACTOR and basic:
        shares = basic * CLASS_FACTOR[ticker]
        shares_src = CLASS_NOTE[ticker]
    else:
        cands = []
        if cover:
            cands.append((cover, "cover page of the latest filing"))
        if basic:
            cands.append((basic, "weighted average for the latest quarter"))
            for mult, word in ((1e3, "thousands"), (1e6, "millions")):
                cands.append((basic * mult, f"weighted average, corrected: the filing gives it in {word} without saying so"))
        if implied and cover and abs(math.log(cover / implied)) <= math.log(1.5):
            shares, shares_src = cover, "cover page of the latest filing"   # rounded EPS can't separate close counts
        elif implied and cands:
            shares, shares_src = min(cands, key=lambda c: abs(math.log(c[0] / implied)))
        elif cover and (not basic or 0.5 <= cover / basic <= 2.0):
            shares, shares_src = cover, "cover page of the latest filing"
        elif basic:
            shares, shares_src = basic, "weighted average for the latest quarter"
    plausible = lambda r: r is not None and abs(math.log(max(r, 1e-9))) <= math.log(3)
    share_yoy = None
    bq = basic_q or basic_a  # companies that only report yearly: compare years
    if bq:
        e0 = max(bq)
        e1 = year_before(list(bq), e0)
        r = _div(bq[e0], bq.get(e1)) if e1 else None
        share_yoy = r - 1 if plausible(r) else None
    dilution_itm = None
    dq_ = dil_q if basic_q else dil_a
    if bq and dq_:
        e0 = max(set(bq) & set(dq_), default=None)
        if e0 and ni and ni > 0:
            r = dq_[e0] / bq[e0]
            dilution_itm = max(0.0, r - 1) if plausible(r) else None
    anti, anti_end = None, None
    s, _ = series(cf, ["AntidilutiveSecuritiesExcludedFromComputationOfEarningsPerShareAmount"], "shares")
    by_end = {}
    for (st, e), v in s.items():
        if st and 80 <= days(st, e) <= 100:
            by_end[e] = by_end.get(e, 0) + v
    if by_end:
        anti_end = max(by_end)
        anti = by_end[anti_end]
    dilution_all = _div(anti, shares) if (ni is not None and ni <= 0 and anti and anti_end and days(anti_end, end) <= 200) else None

    # market value (only when the price and the statements are in the same currency)
    price = px["price"] if px else None
    pcur = (px or {}).get("currency") or "USD"
    k = 1.0  # statement currency -> share price currency
    usd_ok = px is not None and pcur == cur
    if px and pcur != cur and not foreign:
        if fx:
            k, usd_ok = fx, True
            warnings.append(f"The statements are in {cur}; for valuation they're converted to {pcur} at today's "
                            f"rate (1 {cur} = {fx:.4f} {pcur}).")
        else:
            warnings.append(f"The statements are in {cur} and the share price in {pcur}, and the exchange rate couldn't "
                            "be fetched, so valuation measures are left out this time.")
    if foreign and px:
        warnings.append("This is a foreign company filing annual 20-F reports: its US-traded shares are often depositary "
                        "shares that each stand for several ordinary shares, so valuation measures are left out rather than guessed.")
    mcap = price * shares if (usd_ok and shares and not foreign) else None
    ev = mcap + (debt - cash) * k if mcap is not None and debt is not None else None
    ebitda = op + dna if op is not None and dna is not None else None

    burn = None
    if fcf is not None and fcf < 0:
        burn = -fcf / 4
    runway = (99.0 if fcf is not None and fcf >= 0 else (_div(cash, burn) if burn else None))

    m = {
        "rev_q_yoy": None, "rev_ttm_yoy": _div(rev, rev_prev) - 1 if rev and rev_prev and rev_prev > 0 else None,
        "rev_cagr_3y": (rev / rev3) ** (1 / 3) - 1 if rev and rev3 and rev3 > 0 and rev > 0 else None,
        "gross_margin": _div(gp, rev) if rev and rev > 0 else None,
        "op_margin": _div(op, rev) if rev and rev > 0 else None,
        "op_margin_chg": (op / rev - op_prev / rev_prev) if (op is not None and op_prev is not None and rev and rev_prev
                                                            and rev > 0 and rev_prev > 0) else None,
        "fcf_margin": _div(fcf, rev) if rev and rev > 0 else None,
        "net_debt_ebitda": ((debt - cash) / ebitda if debt is not None and ebitda and ebitda > 0 else None),
        "interest_cover": (_div(op, interest) if interest and interest > 0 and rev and abs(interest) > 0.002 * rev else None),
        "current_ratio": _div(bal["current_assets"], bal["current_liabilities"]),
        "runway_q": runway,
        "share_yoy": share_yoy,
        "sbc_pct": _div(sbc, rev) if rev and rev > 0 else None,
        "dilution_itm": dilution_itm,
        "dilution_all": dilution_all,
        "ev_sales": _div(ev, rev * k) if ev is not None and rev and rev > 0 else None,
        "ev_ebit": (_div(ev, op * k) if ev is not None and op and op > 0 else None),
        "fcf_yield": _div(fcf * k, mcap) if mcap and fcf is not None else None,
        "pe": (_div(mcap, ni * k) if mcap and ni and ni > 0 else None),
        "pe_alt": (_div(mcap, ni * k) if debt is None and mcap and ni and ni > 0 else None),
        "pb": (_div(mcap, equity * k) if mcap and equity and equity > 0 else None),
        "roe": (_div(ni, (equity + equity_prev) / 2 if equity_prev else equity) if ni is not None and equity and equity > 0 else None),
        "net_margin": _div(ni, rev) if rev and rev > 0 else None,
        "equity_assets": _div(equity, bal["assets"]),
        "ret_1y": px.get("return_1y") if px else None,
        "vs_ma200": (_div(price, px.get("ma200")) - 1) if px and px.get("ma200") else None,
        "range_pos": _div(price - px["low_52w"], px["high_52w"] - px["low_52w"]) if px and px["high_52w"] > px["low_52w"] else None,
    }
    if end in rq:
        e1 = year_before(list(rq), end)
        if e1 and rq[e1] and rq[e1] > 0:
            m["rev_q_yoy"] = rq[end] / rq[e1] - 1
    # a loss-maker with no debt cost: leverage measures don't apply; a profitable one with no interest: very safe
    if m["interest_cover"] is None and op is not None and op > 0 and (not interest or interest <= 0.002 * (rev or 1)) \
            and debt is not None and debt <= 0.05 * (rev or 1):
        m["interest_cover"] = NO_DEBT
    if ebitda is not None and ebitda <= 0 and debt is not None and debt - cash <= 0:
        m["net_debt_ebitda"] = NET_CASH  # net cash: no leverage at all

    notes = {}
    if m["interest_cover"] == NO_DEBT:
        notes["interest_cover"] = "Little or no debt"
    if m["net_debt_ebitda"] is not None and m["net_debt_ebitda"] < 0:
        notes["net_debt_ebitda"] = "More cash than debt"
        m["net_debt_ebitda"] = NET_CASH
    if m["runway_q"] == 99.0:
        notes["runway_q"] = "Generates cash"
    if rev is not None and rev <= 0:
        warnings.append("No revenue in the last 12 months: margin and sales-based measures are left out.")

    # pillars
    pillars, total, wsum = [], 0.0, 0
    for pname, w, keys, ask in PILLARS[model]:
        items = []
        for k in keys:
            label, bad, good, f = rule(k, sector)
            v = m.get(k)
            if k in OPTIONAL and v is None:
                continue  # only shown where it applies (e.g. profitable vs loss-making)
            items.append({"key": k, "label": label, "value": v, "text": fmt(v, f), "score": lin(v, bad, good), "fmt": f,
                          "bad": bad, "good": good, "rule": f"0 points at {fmt(bad, f)}, 10 points at {fmt(good, f)}",
                          "note": notes.get(k)})
        got = [i["score"] for i in items if i["score"] is not None]
        ps = round(sum(got) / len(got), 1) if got else None
        if ps is not None:
            total += ps * w
            wsum += w
        pillars.append({"name": pname, "weight": w, "question": ask, "score": ps, "metrics": items})
    if wsum < 50:
        raise ScoreError(f"Not enough data in {name}'s filings to score it fairly.")
    overall = round(total / wsum, 2)
    grade, verdict = next((g, v) for t, g, v in GRADES if overall >= t)
    ps = [p for p in pillars if p["score"] is not None]
    ups = sorted((p for p in ps if p["score"] >= 6.5), key=lambda p: -p["score"] * p["weight"])[:2]
    downs = sorted((p for p in ps if p["score"] < 4.5), key=lambda p: -(10 - p["score"]) * p["weight"])[:2]
    join = lambda xs: xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]
    good, bad = [PILLAR_WORDS[p["name"]][0] for p in ups], [PILLAR_WORDS[p["name"]][1] for p in downs]
    reason = f"{join(good)}, but {join(bad)}." if good and bad else (join(good or bad) + "." if good or bad else
                                                                    "No pillar stands out either way.")
    reason = reason[0].upper() + reason[1:]
    missing = [p["name"] for p in pillars if p["score"] is None]
    if missing:
        warnings.append("Not scored (no data): " + ", ".join(missing) + ". The overall score uses the other pillars.")

    # last eight quarters for the table
    qrows = []
    oq = flows["operating_income"][0]
    nq = flows["net_income"][0]
    fq = {e: v - flows["capex"][0].get(e, 0) for e, v in flows["ocf"][0].items()}
    for e in sorted(rq, reverse=True)[:8]:
        r = rq[e]
        qrows.append({"end": e, "revenue": r, "gross_margin": _div(gq.get(e), r) if r else None,
                      "op_margin": _div(oq.get(e), r) if r else None, "net_income": nq.get(e), "fcf": fq.get(e)})

    return {
        "ticker": ticker, "name": name, "cik": cik, "exchange": exchange, "version": VERSION,
        "sector": {"key": sector, "label": sector_label, "sic": sic, "sic_label": sub.get("sicDescription")},
        "model": model, "currency": cur,
        "score": {"overall": overall, "grade": grade, "verdict": verdict, "reason": reason, "pillars": pillars,
                  "weights_used": wsum},
        "price": {k: v for k, v in (px or {}).items() if k != "history"} or None,
        "history": (px or {}).get("history"),
        "facts": {"period_end": end, "balance_sheet_date": bs_end, "revenue_ttm": rev, "gross_profit_ttm": gp,
                  "operating_income_ttm": op, "net_income_ttm": ni, "fcf_ttm": fcf, "cash": cash, "debt": debt,
                  "equity": equity, "shares": shares, "shares_source": shares_src, "market_cap": mcap, "ev": ev,
                  "value_currency": pcur, "fx": k if k != 1.0 else None,
                  "potential_shares": anti},
        "quarters": qrows,
        "tags": tags,
        "warnings": warnings,
        "links": {"filings": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik:010d}&type=10-&dateb=&owner=include&count=40"},
    }


def score_ticker(raw):
    """Everything the page needs for one ticker. Raises ScoreError for problems to show the visitor."""
    t = norm_ticker(raw)
    if not t:
        raise ScoreError("Type a ticker, such as AAPL.", 400)
    d = directory()
    if t not in d:
        alt = [k for k in (t.replace("-", ""), t + "-A", t + "-B") if k in d]
        if not alt:
            raise ScoreError(f"{t} isn't in the SEC's list of companies. This tool covers companies that file with "
                             "the SEC (US-listed stocks and many foreign ones listed in the US).", 404)
        t = alt[0]
    cik, name, exchange = d[t]

    def fetch():
        warnings = []
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_sub = pool.submit(sec_json, f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
            f_cf = pool.submit(sec_json, f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
            f_px = pool.submit(prices, t)
            try:
                sub = f_sub.result()
            except Exception as e:
                raise ScoreError(f"The SEC didn't answer ({str(e)[:60]}). Try again in a minute.", 503)
            try:
                cf = f_cf.result()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise ScoreError(f"{name} has no financial statements in the SEC's machine-readable (XBRL) "
                                     "format, so it can't be scored.")
                raise ScoreError(f"The SEC didn't answer ({e.code}). Try again in a minute.", 503)
            except Exception as e:
                raise ScoreError(f"The SEC didn't answer ({str(e)[:60]}). Try again in a minute.", 503)
            try:
                px = f_px.result()
            except Exception as e:
                px = None
                warnings.append("The share price couldn't be fetched just now, so valuation and momentum are left "
                                "out. Try again later for the full score.")
        cur, pcur = reporting_currency(cf), (px or {}).get("currency") or "USD"
        forms = set(((sub.get("filings") or {}).get("recent") or {}).get("form") or [])
        foreign = "20-F" in forms and "10-K" not in forms   # valuation is left out for these anyway
        fx = fx_rate(cur, pcur) if px and cur != pcur and not foreign else None
        out = build(t, cik, name, exchange, sub, cf, px, warnings, fx=fx)
        out["generated"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        return out
    return cached(f"score:{t}", 1800, fetch)


# ---------------------------------------------------------------- command line
def fmt(v, kind):
    if v is None:
        return "n/a"
    if kind == "pct":
        return f"{v * 100:+.1f}%"
    if kind == "share":
        return f"{v * 100:.1f}%"
    if kind == "pp":
        return f"{v * 100:+.1f} pts"
    if kind == "qtrs":
        return "self-funding" if v >= SELF_FUNDING else f"{v:.1f} qtrs"
    if v == NO_DEBT:
        return "no debt"
    if v == NET_CASH:
        return "net cash"
    return f"{v:.1f}x"


def report(r):
    s, px, f = r["score"], r.get("price") or {}, r["facts"]
    print(f"\n{r['name']} ({r['ticker']}, {r['exchange']}) - {r['sector']['label']} [SIC {r['sector']['sic']} "
          f"{r['sector']['sic_label']}], model {r['model']}, {r['currency']}")
    print(f"Overall {s['overall']:.2f}/10 {s['grade']} - {s['verdict']}  {s['reason']}")
    if px:
        print(f"Price ${px['price']:.2f} on {px['date']} ({px['source']}); market cap "
              f"{(f['market_cap'] or 0) / 1e9:,.1f}bn; EV {(f['ev'] or 0) / 1e9:,.1f}bn")
    print(f"Period {f['period_end']}, revenue TTM {(f['revenue_ttm'] or 0) / 1e9:,.2f}bn, op income "
          f"{(f['operating_income_ttm'] or 0) / 1e9:,.2f}bn, FCF {(f['fcf_ttm'] or 0) / 1e9:,.2f}bn, cash "
          f"{(f['cash'] or 0) / 1e9:,.2f}bn, debt {(f['debt'] or 0) / 1e9:,.2f}bn, shares {(f['shares'] or 0) / 1e6:,.0f}M")
    for p in s["pillars"]:
        print(f"  {p['name']:<14} {str(p['score']):>5}  ({p['weight']}%)")
        for i in p["metrics"]:
            print(f"      {i['label']:<58} {i['text']:>12} {str(i['score']):>5}")
    for w in r["warnings"]:
        print("  ! " + w)
    print("  tags: " + "; ".join(f"{k}={'+'.join(v)}" for k, v in r["tags"].items()))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    for a in args:
        try:
            res = score_ticker(a)
            print(json.dumps(res, indent=1, default=str)) if "--json" in sys.argv else report(res)
        except ScoreError as e:
            print(f"\n{a}: {e}")
