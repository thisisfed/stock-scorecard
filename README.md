# Stock Scorecard

Type a ticker, get an independent 0–10 score for the company, built on the spot from its SEC filings and its
share price. Free to run: a static page plus two small Python functions on Vercel's free Hobby plan.

Not investment advice: a structured summary of public numbers, not a forecast or a recommendation.

## How it works

When someone asks for a ticker, `api/score.py`:

1. finds the company in the SEC's ticker list;
2. reads its financial statements (XBRL "company facts") and industry code from SEC EDGAR;
3. fetches a year of daily share prices (Yahoo Finance, then Nasdaq, then Stooq);
4. works out quarterly and trailing-12-month figures, even where companies only file year-to-date numbers
   or switch the labels they use, and scores six pillars against norms for the company's industry.

Results are cached for an hour at Vercel's edge, so repeat visits are instant and the SEC sees few requests.

| Pillar | Weight | Measures |
|---|---|---|
| Growth | 20% | revenue vs a year earlier (quarter and 12 months), 3-year revenue growth |
| Profitability | 25% | gross, operating and free-cash-flow margins; operating margin vs a year earlier |
| Balance sheet | 15% | net debt / EBITDA, interest cover, current ratio, cash runway |
| Dilution | 10% | share count vs a year earlier, stock pay / revenue, extra shares from awards and convertibles |
| Valuation | 20% | EV / sales, EV / operating profit, free-cash-flow yield |
| Momentum | 10% | 1-year return, price vs 200-day average, place in 52-week range |

Banks and insurers use return on equity, net margin, equity / assets, price / book and price / earnings instead.
Thresholds differ by industry (`SECTOR_RULES` in `api/_engine.py`). Grades: A 8+, B 6.5+, C 5+, D 3.5+, F below.

## Put it online (once, about 3 minutes)

1. Go to **vercel.com**, choose **Continue with GitHub**, and pick the free **Hobby** plan.
2. **Add New → Project**, then **Import** `stock-scorecard`. Leave every build setting as it is.
3. Before clicking **Deploy**, open **Environment Variables** and add `SEC_UA` with your name and email,
   for example `Jane Smith jane@example.com`. The SEC asks every client to identify itself; the value stays private.
4. Click **Deploy**. Your site is at `https://stock-scorecard.vercel.app` (or the address Vercel shows).

Every push to `main` redeploys automatically. To check scores on real companies after a change, the
**Check scores on real companies** workflow scores about 45 companies across every sector; add the same `SEC_UA`
as a repository secret (**Settings → Secrets and variables → Actions**) for it.

## Run it on a computer

```
SEC_UA="Jane Smith jane@example.com" python3 api/_engine.py AAPL NVDA KO
python3 tests/test_engine.py           # checks with a made-up company, no network
python3 tools/dev_server.py            # preview the page with saved results from out/
```

Standard library only; no packages to install.
