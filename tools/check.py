"""Score a list of real companies and print what each one gets: python3 tools/check.py [TICKER ...]
Writes out/<TICKER>.json. Exits non-zero only on unexpected errors (bugs), not on 'can't score' answers."""
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))
import _engine as E  # noqa: E402

DEFAULT = ("AAPL MSFT NVDA GOOGL AMZN META TSLA OPEN KO WMT COST HD MCD NKE PG JPM BAC SOFI BRK-B V PYPL COIN "
           "XOM CVX NEE PFE LLY MRNA UNH PLTR SNOW SHOP AMD INTC TSM ASML BABA F BA CAT DIS T NFLX O DHI Z RIVN UBER "
           "AMAT LRCX ORCL CRM ADBE SBUX TGT GS MS SCHW AIG MET ABBV MRK GILD VRTX TMO ISRG GE HON LMT UPS FDX DE SO "
           "DUK LIN NEM FCX SPOT NVO SONY RY TD ENB CP LULU CMG HOOD AFRM RBLX DKNG ARM SMCI MSTR GME LCID NIO PDD "
           "ABNB CVNA EXPE").split()

tickers = sys.argv[1:] or DEFAULT
os.makedirs("out", exist_ok=True)
rows, bugs = [], 0
for t in tickers:
    t0 = time.time()
    try:
        r = E.score_ticker(t)
        E.report(r)
        with open(f"out/{E.norm_ticker(t)}.json", "w") as fh:
            json.dump(r, fh, default=str)
        s = r["score"]
        rows.append(f"{t:<6} {s['overall']:5.2f} {s['grade']}  {r['sector']['key']:<13} {r['currency']} "
                    + " ".join(f"{p['name'][:4]}={p['score']}" for p in s["pillars"])
                    + f"  px={(r.get('price') or {}).get('source')}  {time.time() - t0:.1f}s"
                    + (f"  !{len(r['warnings'])}" if r["warnings"] else ""))
    except E.ScoreError as e:
        rows.append(f"{t:<6}  --   {e}")
    except Exception:
        bugs += 1
        traceback.print_exc()
        rows.append(f"{t:<6}  BUG  {traceback.format_exc().strip().splitlines()[-1]}")
    time.sleep(0.3)  # stay well inside the SEC's 10 requests a second
print("\n==== SUMMARY ====")
print("\n".join(rows))
sys.exit(1 if bugs else 0)
