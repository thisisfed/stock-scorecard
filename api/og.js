// GET /api/og?t=AAPL (or ?t=AAPL&vs=MSFT) -> a 1200x630 PNG for link previews: ticker, score, grade, top pillars.
// Runs on Vercel's free Edge runtime. It reads the score from this site's own /api/score, so the two always agree.
import { ImageResponse } from "@vercel/og";

export const config = { runtime: "edge" };

const W = 1200, H = 630;
const INK = "#23201d", MUTED = "#786e64", LINE = "#ebe7e2";
const TONE = { green: "#10b981", amber: "#d48a12", red: "#e5484d", grey: "#a69d93" };
const FILL = { green: "#34d399", amber: "#f5b53f", red: "#f07470", grey: "#d6d1cb" };
const tone = (s) => (s == null ? "grey" : s >= 6.5 ? "green" : s >= 4 ? "amber" : "red");
const HUES = [[0, 0], [3.5, 12], [5, 34], [6.5, 72], [8, 128], [10, 148]];
function hue(score) {
  const v = Math.max(0, Math.min(10, score || 0));
  for (let i = 1; i < HUES.length; i++) {
    if (v <= HUES[i][0]) { const a = HUES[i - 1], b = HUES[i]; return a[1] + (b[1] - a[1]) * (v - a[0]) / (b[0] - a[0]); }
  }
  return 148;
}
function rgba(hh, sat, light, a) {  // hsl to rgba: satori blends hsla gradients through black
  const s = sat / 100, l = light / 100, k = (n) => (n + hh / 30) % 12, m = s * Math.min(l, 1 - l);
  const f = (n) => Math.round(255 * (l - m * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1))));
  return `rgba(${f(0)},${f(8)},${f(4)},${a})`;
}
const norm = (t) => String(t || "").trim().toUpperCase().replace(/[./]/g, "-").replace(/[^A-Z0-9-]/g, "").slice(0, 12);

// a tiny element builder: satori takes React-like objects, so no JSX or build step is needed
function h(type, style, ...children) {
  const kids = children.flat().filter((c) => c != null && c !== false);
  return { type, props: { style, children: kids.length === 0 ? undefined : kids.length === 1 ? kids[0] : kids } };
}
const row = (style, ...c) => h("div", { display: "flex", ...style }, ...c);
const col = (style, ...c) => h("div", { display: "flex", flexDirection: "column", ...style }, ...c);
const text = (style, s) => h("div", { display: "flex", ...style }, String(s));

function ring(score, size, stroke) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r, f = Math.max(0, Math.min(1, (score || 0) / 10));
  const color = TONE[tone(score)];
  return {
    type: "svg",
    props: {
      width: size, height: size, viewBox: `0 0 ${size} ${size}`, style: { position: "absolute", top: 0, left: 0 },
      children: [
        { type: "circle", props: { cx: size / 2, cy: size / 2, r, fill: "none", stroke: "#efebe6", "stroke-width": stroke } },
        { type: "circle", props: { cx: size / 2, cy: size / 2, r, fill: "none", stroke: color, "stroke-width": stroke, "stroke-linecap": "round",
          "stroke-dasharray": `${(c * f).toFixed(1)} ${c.toFixed(1)}`, transform: `rotate(-90 ${size / 2} ${size / 2})` } },
      ],
    },
  };
}

function scoreBlock(s, size) {
  return h("div", { display: "flex", position: "relative", width: size, height: size, alignItems: "center", justifyContent: "center" },
    ring(s.overall, size, Math.round(size * 0.075)),
    col({ alignItems: "center" },
      text({ fontSize: size * 0.3, fontWeight: 600, letterSpacing: "-0.04em", lineHeight: 1, color: INK }, s.overall.toFixed(1)),
      text({ fontSize: size * 0.085, color: MUTED, marginTop: 6 }, "out of 10")));
}

function grade(s, size) {
  const t = tone(s.overall);
  return text({ width: size, height: size, borderRadius: size * 0.22, alignItems: "center", justifyContent: "center",
    fontSize: size * 0.62, fontWeight: 600, color: TONE[t], backgroundColor: t === "green" ? "#e6f4ea" : t === "amber" ? "#fdf3e1" : t === "red" ? "#fdeceb" : "#f1efec" }, s.grade);
}

function bar(p, width) {
  const t = tone(p.score);
  return col({ width, marginBottom: 18 },
    row({ justifyContent: "space-between", fontSize: 24, color: INK, marginBottom: 8 },
      text({}, p.name), text({ fontWeight: 600 }, p.score == null ? "n/a" : p.score.toFixed(1))),
    row({ width, height: 12, borderRadius: 6, backgroundColor: "#efebe6" },
      h("div", { width: Math.max(8, width * (p.score || 0) / 10), height: 12, borderRadius: 6, backgroundColor: FILL[t] })));
}

function frame(score, ...children) {
  const hh = score == null ? 230 : hue(score), c1 = (hh + 352) % 360, c2 = (hh + 10) % 360;
  return col({ width: W, height: H, padding: "56px 64px", backgroundColor: "#f6f5f3", fontFamily: "Inter", color: INK,
    backgroundImage: `radial-gradient(circle 720px at 8% 0%, ${rgba(c1, 95, 79, 0.6)}, ${rgba(c1, 95, 79, 0)}), ` +
      `radial-gradient(circle 640px at 92% 0%, ${rgba(c2, 90, 80, 0.55)}, ${rgba(c2, 90, 80, 0)})` },
    ...children,
    row({ marginTop: "auto", justifyContent: "space-between", fontSize: 22, color: MUTED, borderTop: `1px solid ${LINE}`, paddingTop: 20 },
      text({ fontWeight: 600, color: INK }, "Stock Scorecard"), text({}, "Built from SEC filings · not investment advice")));
}

function single(r) {
  const s = r.score;
  const top = s.pillars.filter((p) => p.score != null).sort((a, b) => b.score * b.weight - a.score * a.weight).slice(0, 3);
  return frame(s.overall,
    row({ flex: 1, alignItems: "center", gap: 56 },
      col({ flex: 1 },
        text({ fontSize: 30, color: MUTED, fontWeight: 600, letterSpacing: "0.02em" }, r.ticker),
        text({ fontSize: r.name.length > 26 ? 50 : 62, fontWeight: 600, letterSpacing: "-0.03em", lineHeight: 1.05, marginTop: 6 }, r.name),
        text({ fontSize: 28, color: "#5d554d", marginTop: 18, lineHeight: 1.3 }, s.summary || s.verdict),
        col({ marginTop: 34 }, top.map((p) => bar(p, 560)))),
      col({ alignItems: "center", gap: 24 }, scoreBlock(s, 300), grade(s, 96))));
}

function compare(a, b) {
  const side = (r) => col({ flex: 1, alignItems: "center" },
    text({ fontSize: 44, fontWeight: 600, letterSpacing: "-0.02em" }, r.ticker),
    text({ fontSize: 22, color: MUTED, marginTop: 4, marginBottom: 24, maxWidth: 440, textAlign: "center" }, r.name.length > 34 ? r.name.slice(0, 32) + "…" : r.name),
    row({ alignItems: "center", gap: 28 }, scoreBlock(r.score, 240), grade(r.score, 84)));
  const best = a.score.overall >= b.score.overall ? a : b;
  return frame((a.score.overall + b.score.overall) / 2,
    row({ flex: 1, alignItems: "center" }, side(a), text({ fontSize: 30, color: MUTED, padding: "0 16px" }, "vs"), side(b)),
    text({ fontSize: 26, color: "#5d554d", justifyContent: "center", marginBottom: 22 },
      a.score.overall === b.score.overall ? "Level on the overall score" : `${best.ticker} scores higher overall`));
}

function message(t, msg) {
  return frame(null, col({ flex: 1, justifyContent: "center" },
    text({ fontSize: 72, fontWeight: 600, letterSpacing: "-0.03em" }, t || "Any stock, scored."),
    text({ fontSize: 32, color: "#5d554d", marginTop: 16 }, msg)));
}

async function getScore(origin, t) {
  const r = await fetch(`${origin}/api/score?t=${encodeURIComponent(t)}`, { headers: { accept: "application/json" } });
  const j = await r.json();
  if (!r.ok || j.error || !j.score) throw new Error(j.error || "no score");
  return j;
}

let FONTS = null;
async function fonts() {
  if (!FONTS) {
    const load = (f) => fetch(new URL(`./_og/${f}`, import.meta.url)).then((r) => r.arrayBuffer());
    const [regular, semibold] = await Promise.all([load("inter-latin-400-normal.woff"), load("inter-latin-600-normal.woff")]);
    FONTS = [{ name: "Inter", data: regular, weight: 400, style: "normal" }, { name: "Inter", data: semibold, weight: 600, style: "normal" }];
  }
  return FONTS;
}

export default async function handler(req) {
  const url = new URL(req.url), t = norm(url.searchParams.get("t")), vs = norm(url.searchParams.get("vs"));
  let el, ok = true;
  try {
    if (!t) el = message("", "Type a ticker, get an independent 0–10 score.");
    else if (vs && vs !== t) { const [a, b] = await Promise.all([getScore(url.origin, t), getScore(url.origin, vs)]); el = compare(a, b); }
    else el = single(await getScore(url.origin, t));
  } catch (e) {
    ok = false;
    el = message(t, "Open the link to see its score.");
  }
  return new ImageResponse(el, {
    width: W, height: H, fonts: await fonts(),
    headers: { "Cache-Control": ok ? "public, max-age=3600, s-maxage=3600, stale-while-revalidate=86400" : "public, max-age=60, s-maxage=300" },
  });
}
