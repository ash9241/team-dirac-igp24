import assert from "node:assert/strict";
import { cp, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

// Render the approved article once; its only client interaction is the score calculator.
const root = resolve(import.meta.dirname, "..");
const output = join(root, "out/vercel");
const origin = new URL(process.env.SITE_ORIGIN || "https://team-dirac-igp24.vercel.app").origin;
const sharePath = "/working-with-ai-on-the-inverse-galois-problem";
const { default: worker } = await import(pathToFileURL(join(root, "dist/server/index.js")));
const response = await worker.fetch(new Request(origin, { headers: { accept: "text/html", host: new URL(origin).host } }),
  { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
  { waitUntil() {}, passThroughOnException() {} });
assert.equal(response.status, 200);
let html = await response.text();
html = html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "");
html = html.replace(/<link\b[^>]*>/gi, tag => /rel="stylesheet"/.test(tag) ? tag : "");
html = html.replace("</head>", `<link rel="canonical" href="${origin}/"/><meta property="og:url" content="${origin}/"/></head>`);
html = html.replace("</body>", '<script type="module" src="/scripts/calculator.mjs"></script></body>');

await mkdir(output, { recursive: true });
const copied = new Set();
async function copyAsset(url) {
  if (copied.has(url)) return;
  assert.match(url, /^\/(?:assets|images|figures|downloads)\/[\w./-]+$/);
  assert.ok(!url.includes(".."));
  const input = join(root, url.startsWith("/assets/") ? "dist/client" : "public", url);
  const destination = join(output, url);
  await mkdir(dirname(destination), { recursive: true });
  await cp(input, destination);
  copied.add(url);
  if (url.endsWith(".css")) {
    const css = await readFile(input, "utf8");
    for (const match of css.matchAll(/url\(["']?(\/assets\/[^)"'\s]+)["']?\)/g)) await copyAsset(match[1]);
  }
}
for (const match of html.matchAll(/(?:src|srcSet|href)="(\/(?:assets|images|figures|downloads)\/[^"?]+)"/g)) await copyAsset(match[1]);
await copyAsset("/images/hero-wire-knot.png");
await mkdir(join(output, "scripts"), { recursive: true });
await cp(join(root, "scripts/static-scoring.mjs"), join(output, "scripts/calculator.mjs"));
await cp(join(root, "app/scoring.mjs"), join(output, "scripts/scoring.mjs"));
await writeFile(join(output, "index.html"), html);
// Give the retitled article a distinct URL for social crawlers that cached the old root page.
// Serve the complete article here, without redirecting crawlers back to that cached URL.
const shareHtml = html
  .replace(`rel="canonical" href="${origin}/"`, `rel="canonical" href="${origin}${sharePath}"`)
  .replace(`property="og:url" content="${origin}/"`, `property="og:url" content="${origin}${sharePath}"`);
await writeFile(join(output, sharePath.slice(1) + ".html"), shareHtml);
await writeFile(join(output, "robots.txt"), `User-agent: *\nAllow: /\nSitemap: ${origin}/sitemap.xml\n`);
await writeFile(join(output, "sitemap.xml"), `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>${origin}/</loc></url></urlset>\n`);
await writeFile(join(output, "vercel.json"), JSON.stringify({
  "$schema": "https://openapi.vercel.sh/vercel.json",
  framework: null,
  buildCommand: null,
  installCommand: null,
  outputDirectory: ".",
  rewrites: [{ source: sharePath, destination: sharePath + ".html" }],
  headers: [
    { source: "/assets/(.*)", headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }] },
    { source: "/(images|figures)/(.*)", headers: [{ key: "Cache-Control", value: "public, max-age=3600, stale-while-revalidate=86400" }] }
  ]
}, null, 2) + "\n");
assert.ok(html.includes('href="https://github.com/ash9241/team-dirac-igp24"'));
assert.ok(html.includes(origin + "/images/hero-wire-knot.png"));
assert.ok(!html.includes("chatgpt.site"), "A private Sites link remains in the public export.");
assert.ok(!html.includes("localhost"), "Local metadata remains in the public export.");
console.log(JSON.stringify({ output, origin, htmlBytes: Buffer.byteLength(html), assets: copied.size }));
