"""Made-up results for previewing the page offline: python3 tools/fixtures.py, then python3 tools/dev_server.py
Writes out/DEMO.json, out/SHOP2.json and out/BANKX.json from the synthetic company in tests/test_engine.py."""
import contextlib
import datetime as dt
import io
import json
import math
import os
import random
import runpy

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
with contextlib.redirect_stdout(io.StringIO()):
    g = runpy.run_path(os.path.join(ROOT, "tests", "test_engine.py"))
E, cf = g["E"], g["cf"]


def history(seed, start, drift, vol):
    rnd, v, rows, d = random.Random(seed), start, [], dt.date(2025, 11, 4)
    while len(rows) < 260:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            v *= math.exp(drift / 252 + vol / math.sqrt(252) * rnd.gauss(0, 1))
            rows.append([d.isoformat(), round(v, 4)])
    return rows


def px(hist):
    c = [p[1] for p in hist]
    return {"date": hist[-1][0], "price": c[-1], "high_52w": max(c[-252:]), "low_52w": min(c[-252:]),
            "return_1y": c[-1] / c[-253] - 1, "ma200": sum(c[-200:]) / 200, "currency": "USD", "source": "Made-up data",
            "days": len(c), "history": hist}


os.makedirs(os.path.join(ROOT, "out"), exist_ok=True)
for t, name, sic, h in (("DEMO", "Demo Software Inc.", "7372", history(1, 38, 0.35, 0.45)),
                        ("SHOP2", "Second Shop Corp.", "5331", history(7, 64, -0.25, 0.3)),
                        ("BANKX", "Bank Example Co", "6021", history(3, 50, 0.1, 0.25))):
    r = E.build(t, 1, name, "Nasdaq", {"sic": sic, "sicDescription": "made up"}, cf, px(h), [])
    r["generated"] = dt.datetime.now(dt.timezone.utc).isoformat()
    json.dump(r, open(os.path.join(ROOT, "out", t + ".json"), "w"), default=str)
    print(t, r["score"]["overall"], r["score"]["grade"], "-", r["score"]["summary"])
