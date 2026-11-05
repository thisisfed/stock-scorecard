// Render share images offline from out/*.json: node tools/og_preview.mjs DEMO [VS]  -> out/og-DEMO[-VS].png
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const real = globalThis.fetch;
globalThis.fetch = async (u, o) => {
  const s = String(u);
  if (s.startsWith("file:")) return new Response(await readFile(fileURLToPath(s)));
  const m = s.match(/\/api\/score\?t=([A-Z0-9-]+)/);
  if (m) {
    try { return new Response(await readFile(`out/${m[1]}.json`), { headers: { "content-type": "application/json" } }); }
    catch { return new Response(JSON.stringify({ error: "not found" }), { status: 404 }); }
  }
  return real(u, o);
};
const { default: handler } = await import("../api/og.js");
const [t, vs] = process.argv.slice(2);
const res = await handler(new Request(`http://localhost/api/og?t=${t || ""}${vs ? "&vs=" + vs : ""}`));
const name = `out/og-${t || "home"}${vs ? "-" + vs : ""}.png`;
await writeFile(name, Buffer.from(await res.arrayBuffer()));
console.log(res.status, res.headers.get("content-type"), name);
