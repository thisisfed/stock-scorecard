# Stock Scorecard

Type a ticker, get an independent 0–10 score for the company, built on the spot from its SEC filings and its
share price. Free to run: a static page plus a few small functions on Vercel's free Hobby plan.

Not investment advice: a structured summary of public numbers, not a forecast or a recommendation.

## How it works

When someone asks for a ticker, `api/score.py`:

1. finds the company in the SEC's ticker list;
2. reads its financial statements (XBRL "company facts") and industry code from SEC EDGAR;
3. fetches a year of daily share prices (Yahoo Finance, then Nasdaq, then Stooq);
4. works out quarterly and trailing-12-month figures, even where companies only file year-to-date numbers
   or switch the labels they use, and scores six pillars against norms for the company's industry.

Results are cached for an hour at Vercel's edge, so repeat visits are instant and the SEC sees few requests.

The page shows the score as a ring with a letter grade and a one-line summary, pillar bars with their weights and a
radar, a 0–10 bar on every measure (hover or tap for its 0- and 10-point values), a 1Y / 6M / 3M price chart,
quarterly results with year-on-year arrows and sparklines, and the method with every threshold by industry.

| Address | What it does |
|---|---|
| `/AAPL` | the scorecard for one company (`api/page.py` adds the link-preview tags, then the page loads the score) |
| `/AAPL/vs/MSFT` | two companies side by side; typing `AAPL vs MSFT` in the search box goes there too |
| `/api/score?t=AAPL` | the score as JSON |
| `/api/og?t=AAPL` (`&vs=MSFT`) | the 1200×630 share image for chat apps and social sites (`api/og.js`, Vercel's free Edge runtime) |
| `/api/rules` | pillars, weights, grades and every threshold by industry, for the method block |
| `/api/tickers` | the list behind the search suggestions |

"Your recent scores" on the home page is kept in the visitor's own browser; nothing is stored on the server.

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
python3 tests/test_page.py             # link-preview tags and the method data
python3 tools/fixtures.py              # made-up companies in out/ (DEMO, SHOP2, BANKX) to preview offline
python3 tools/dev_server.py            # preview at http://127.0.0.1:8765/DEMO or /DEMO/vs/SHOP2
npm install && node tools/og_preview.mjs DEMO SHOP2   # render a share image to out/
```

The Python is standard library only. The share image needs one npm package (`@vercel/og`), which Vercel installs
on its own; the Inter font files it uses are in `api/_og/` (SIL Open Font License).
