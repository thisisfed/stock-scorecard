"""Synthetic company with known answers: run with python3 tests/test_engine.py"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))
import _engine as E  # noqa: E402


def fact(start, end, val, form="10-Q", filed=None, accn="a"):
    d = {"end": end, "val": val, "form": form, "filed": filed or end, "accn": accn}
    if start:
        d["start"] = start
    return d


# Calendar-year company. Quarterly revenue grows 5% a quarter from 100 (Q1 2023).
QE = ["03-31", "06-30", "09-30", "12-31"]
QS = ["01-01", "04-01", "07-01", "10-01"]
rev_old, rev_new, ocf, gp, op, ni, sbc, capex, dna, intr = [], [], [], [], [], [], [], [], [], []
quarters = []
v = 100.0
for y in (2023, 2024, 2025, 2026):
    ytd = {"rev": 0, "ocf": 0, "capex": 0}
    for i in range(4):
        if (y, i) > (2026, 1):
            break
        s, e = f"{y}-{QS[i]}", f"{y}-{QE[i]}"
        quarters.append((e, v))
        form = "10-K" if i == 3 else "10-Q"
        tag_list = rev_old if y < 2024 else rev_new  # the company switched revenue tags in 2024
        if i < 3:
            tag_list.append(fact(s, e, v, form))                     # 3-month revenue (no Q4: only the full year)
            gp.append(fact(s, e, v * 0.7, form)); op.append(fact(s, e, v * 0.2, form)); ni.append(fact(s, e, v * 0.15, form))
            sbc.append(fact(s, e, v * 0.05, form)); dna.append(fact(s, e, 3.0, form)); intr.append(fact(s, e, 1.0, form))
        ytd["rev"] += v
        ytd["ocf"] += v * 0.25
        ytd["capex"] += 5.0
        if i == 3:
            tag_list.append(fact(f"{y}-01-01", e, ytd["rev"], "10-K"))
            for lst, k in ((gp, 0.7), (op, 0.2), (ni, 0.15), (sbc, 0.05)):
                lst.append(fact(f"{y}-01-01", e, ytd["rev"] * k, "10-K"))
            dna.append(fact(f"{y}-01-01", e, 12.0, "10-K")); intr.append(fact(f"{y}-01-01", e, 4.0, "10-K"))
        ocf.append(fact(f"{y}-01-01", e, ytd["ocf"], form))          # cash flows are year-to-date only
        capex.append(fact(f"{y}-01-01", e, ytd["capex"], form))
        v *= 1.05

end = quarters[-1][0]  # 2026-06-30
us = {
    "SalesRevenueNet": {"units": {"USD": rev_old}},
    "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": rev_new}},
    "GrossProfit": {"units": {"USD": gp}}, "OperatingIncomeLoss": {"units": {"USD": op}},
    "NetIncomeLoss": {"units": {"USD": ni}}, "ShareBasedCompensation": {"units": {"USD": sbc}},
    "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": ocf}},
    "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": capex}},
    "DepreciationDepletionAndAmortization": {"units": {"USD": dna}}, "InterestExpense": {"units": {"USD": intr}},
    "Assets": {"units": {"USD": [fact(None, end, 2000.0)]}},
    "AssetsCurrent": {"units": {"USD": [fact(None, end, 600.0)]}},
    "LiabilitiesCurrent": {"units": {"USD": [fact(None, end, 300.0)]}},
    "StockholdersEquity": {"units": {"USD": [fact(None, "2025-06-30", 900.0), fact(None, end, 1000.0)]}},
    "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [fact(None, end, 150.0)]}},
    "ShortTermInvestments": {"units": {"USD": [fact(None, end, 50.0)]}},
    "LongTermDebt": {"units": {"USD": [fact(None, end, 400.0)]}},
    "ShortTermBorrowings": {"units": {"USD": [fact(None, end, 20.0)]}},
    "LongTermDebtNoncurrent": {"units": {"USD": [fact(None, end, 380.0)]}},   # must not be double counted
    "WeightedAverageNumberOfSharesOutstandingBasic": {"units": {"shares": [
        fact("2025-04-01", "2025-06-30", 100e6), fact(f"2026-04-01", end, 98e6)]}},
    "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
        fact("2025-04-01", "2025-06-30", 102e6), fact(f"2026-04-01", end, 99.96e6)]}},
}
dei = {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
    fact(None, "2026-07-25", 60e6, accn="q2"), fact(None, "2026-07-25", 38e6, accn="q2"),   # two share classes
    fact(None, "2026-04-25", 99e6, accn="q1")]}}}
cf = {"facts": {"us-gaap": us, "dei": dei}}

# --- building blocks
s, used = E.series(cf, E.FLOW_TAGS["revenue"], "USD")
assert used[0].endswith("RevenueFromContractWithCustomerExcludingAssessedTax") and len(used) == 2, used
q = E.quarterly(s)
for e, val in quarters:
    assert abs(q[e] - val) < 1e-6, (e, q.get(e), val)            # every quarter, Q4s from the full year
print("revenue quarters OK (tag switch, Q4 from full year)")
oq = E.quarterly(E.series(cf, E.FLOW_TAGS["ocf"], "USD")[0])
for e, val in quarters:
    assert abs(oq[e] - val * 0.25) < 1e-6, (e, oq.get(e))        # cash flow quarters from year-to-date figures
print("cash-flow quarters from year-to-date OK")
want_ttm = sum(val for _, val in quarters[-4:])
assert abs(E.ttm(q, E.annual(s), end) - want_ttm) < 1e-6
assert abs(E.ttm(q, E.annual(s), "2025-12-31") - sum(val for e, val in quarters if e.startswith("2025"))) < 1e-6
debt, dt_used = E.total_debt(cf, "USD", end)
assert debt == 420.0 and len(dt_used) == 2, (debt, dt_used)
shares, sd = E.shares_now(cf)
assert shares == 98e6 and sd == "2026-07-25", (shares, sd)
print("TTM, debt (no double counting), share classes OK")
assert E.sector_of("7372")[0] == "software" and E.sector_of("6531")[0] == "real_estate" and E.sector_of("6021")[0] == "financial"
assert E.sector_of("2834")[0] == "health" and E.sector_of("5331")[0] == "consumer" and E.sector_of("6770")[0] == "blank_check"
assert E.sector_of("3674")[0] == "semis_hw" and E.sector_of("1311")[0] == "energy" and E.sector_of(None)[0] == "other"
print("sectors OK")

# --- the whole score
px = {"date": "2026-11-03", "price": 50.0, "high_52w": 60.0, "low_52w": 30.0, "return_1y": 0.25, "ma200": 45.0,
      "currency": "USD", "source": "test", "history": []}
sub = {"sic": "7372", "sicDescription": "Services-Prepackaged Software"}
r = E.build("TEST", 1, "Test Co", "Nasdaq", sub, cf, px, [])
m = {i["key"]: i for p in r["score"]["pillars"] for i in p["metrics"]}
rev = want_ttm
assert abs(r["facts"]["revenue_ttm"] - rev) < 1e-6
assert abs(m["rev_q_yoy"]["value"] - (1.05 ** 4 - 1)) < 1e-9
assert abs(m["gross_margin"]["value"] - 0.7) < 1e-9 and abs(m["op_margin"]["value"] - 0.2) < 1e-9
fcf = rev * 0.25 - 20.0
assert abs(m["fcf_margin"]["value"] - fcf / rev) < 1e-9
mcap, ev = 50.0 * 98e6, 50.0 * 98e6 + 420.0 - 200.0
assert abs(r["facts"]["market_cap"] - mcap) < 1e-3 and abs(m["ev_sales"]["value"] - ev / rev) < 1e-9
assert abs(m["net_debt_ebitda"]["value"] - (420 - 200) / (rev * 0.2 + 12.0)) < 1e-9
assert abs(m["share_yoy"]["value"] - (98 / 100 - 1)) < 1e-9 and abs(m["dilution_itm"]["value"] - 0.02) < 1e-9
assert "dilution_all" not in m                                    # profitable: only in-the-money dilution
assert m["runway_q"]["text"] == "self-funding" and m["range_pos"]["text"] == "66.7%"
assert m["gross_margin"]["rule"] == "0 points at 50.0%, 10 points at 82.0%"   # software norms
print(f"score: {r['score']['overall']} {r['score']['grade']} - {r['score']['reason']}")
for p in r["score"]["pillars"]:
    print(f"   {p['name']:<14}{p['score']}")
assert r["quarters"][0]["end"] == end and abs(r["quarters"][0]["gross_margin"] - 0.7) < 1e-9

# --- no price: fundamentals only, with a warning
r2 = E.build("TEST", 1, "Test Co", "Nasdaq", sub, cf, None, ["price unavailable"])
names = {p["name"]: p["score"] for p in r2["score"]["pillars"]}
assert names["Valuation"] is None and names["Momentum"] is None and any("Not scored" in w for w in r2["warnings"])
# --- a bank uses the financial model
r3 = E.build("BANK", 1, "Bank Co", "NYSE", {"sic": "6021"}, cf, px, [])
assert r3["model"] == "financial" and [p["name"] for p in r3["score"]["pillars"]][1] == "Profitability"
# --- a blank-check company is refused politely
try:
    E.build("SPAC", 1, "Spac Co", "NYSE", {"sic": "6770"}, cf, px, [])
    raise AssertionError("SPAC should not be scored")
except E.ScoreError as e:
    assert "blank-check" in str(e)
print("no price, bank model, SPAC refusal OK")
assert E.norm_ticker(" brk.b ") == "BRK-B" and E.norm_ticker("aapl") == "AAPL"
print("ALL OK")
