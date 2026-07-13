/*
 * Nexus Cloud Browser
 * -------------------
 * A real Chromium runs here on the server; a phone-friendly web panel drives it.
 * You open the panel from any device, tap the live screenshot to click, type to
 * fill fields, and navigate — the browser lives in the cloud, not on your phone.
 *
 * This mirrors the Nexus bridge's action vocabulary (navigate/click/type/…),
 * but instead of reaching a real Chrome tab over localhost, the browser is right
 * here and we stream it back as screenshots.
 *
 * Auth: every /api/* call requires the token (env CB_TOKEN, or one is generated
 * and printed at startup). Pass it as ?token= or the X-Token header. The browser
 * is on the public internet once deployed, so the token is the only thing between
 * a stranger and your session — keep it secret, rotate by restarting.
 */
"use strict";

const http = require("http");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const PORT = parseInt(process.env.PORT || "8080", 10);
const TOKEN = process.env.CB_TOKEN || crypto.randomBytes(12).toString("hex");
const VIEWPORT = { width: 390, height: 844 };
const MOBILE_UA =
  "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36";
const START_URL = `http://127.0.0.1:${PORT}/start`;

let browser = null;
let context = null;
let page = null;

// ---- serialize page actions so concurrent taps don't race ----
let lock = Promise.resolve();
function withLock(fn) {
  const result = lock.then(fn);
  lock = result.then(() => {}, () => {});
  return result;
}

async function ensureBrowser() {
  if (page && !page.isClosed()) return;
  if (!browser) {
    browser = await chromium.launch({ headless: true, args: ["--no-sandbox", "--disable-quic"] });
  }
  if (!context) {
    context = await browser.newContext({
      viewport: VIEWPORT,
      deviceScaleFactor: 2,
      isMobile: true,
      hasTouch: true,
      userAgent: MOBILE_UA,
    });
    // follow popups / target=_blank into the same panel
    context.on("page", (p) => { page = p; });
  }
  page = await context.newPage();
  try { await page.goto(START_URL, { waitUntil: "domcontentloaded", timeout: 15000 }); } catch (_) {}
}

function normalizeUrl(u) {
  u = (u || "").trim();
  if (!u) return START_URL;
  if (/^https?:\/\//i.test(u) || /^about:/.test(u)) return u;
  // looks like a bare domain/path -> https; otherwise treat as a search
  if (/^[\w-]+(\.[\w-]+)+([/:?#].*)?$/.test(u)) return "https://" + u;
  return "https://www.google.com/search?q=" + encodeURIComponent(u);
}

// ---------------- actions ----------------
const actions = {
  async navigate(body) {
    const url = normalizeUrl(body.url);
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
    return { url: page.url() };
  },
  async click(body) {
    if (typeof body.x === "number" && typeof body.y === "number") {
      await page.mouse.click(body.x, body.y);
    } else if (body.selector) {
      await page.click(body.selector, { timeout: 8000 });
    } else {
      throw new Error("click needs {x,y} or {selector}");
    }
    return { ok: true };
  },
  async type(body) {
    if (typeof body.text !== "string") throw new Error("type needs {text}");
    if (body.selector) await page.fill(body.selector, body.text);
    else await page.keyboard.type(body.text, { delay: 10 });
    if (body.submit) await page.keyboard.press("Enter");
    return { ok: true };
  },
  async key(body) {
    await page.keyboard.press(body.key || "Enter");
    return { ok: true };
  },
  async scroll(body) {
    const dy = typeof body.dy === "number" ? body.dy : VIEWPORT.height * 0.7;
    await page.mouse.wheel(0, dy);
    return { ok: true };
  },
  async back() { await page.goBack({ timeout: 20000 }).catch(() => {}); return { url: page.url() }; },
  async forward() { await page.goForward({ timeout: 20000 }).catch(() => {}); return { url: page.url() }; },
  async reload() { await page.reload({ timeout: 30000 }).catch(() => {}); return { url: page.url() }; },
};

// ---------------- http plumbing ----------------
function send(res, status, obj, headers = {}) {
  const body = Buffer.isBuffer(obj) ? obj : Buffer.from(JSON.stringify(obj));
  res.writeHead(status, {
    "Content-Type": Buffer.isBuffer(obj) ? headers["Content-Type"] : "application/json",
    "Cache-Control": "no-store",
    ...headers,
  });
  res.end(body);
}
function readBody(req) {
  return new Promise((resolve) => {
    let d = "";
    req.on("data", (c) => (d += c));
    req.on("end", () => { try { resolve(d ? JSON.parse(d) : {}); } catch (_) { resolve({}); } });
  });
}
function tokenOf(req, url) {
  return req.headers["x-token"] || url.searchParams.get("token") || "";
}

const START_HTML = `<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0;height:100vh;display:flex;flex-direction:column;align-items:center;
justify-content:center;background:#0b0f19;color:#e4e4e7;font-family:system-ui,sans-serif;text-align:center;padding:24px}
h1{font-size:22px;margin:0 0 8px}p{color:#8b8b95;margin:0;font-size:14px;max-width:300px}
.dot{font-size:44px;margin-bottom:12px}</style></head>
<body><div class="dot">🌐</div><h1>Nexus Cloud Browser</h1>
<p>Ready. Type a URL above (try <b>youtube.com</b>) and this browser will go there.</p></body></html>`;

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const p = url.pathname;

  // public routes
  if (p === "/health") return send(res, 200, { ok: true });
  if (p === "/start") return send(res, 200, Buffer.from(START_HTML), { "Content-Type": "text/html; charset=utf-8" });
  if (p === "/" || p === "/index.html") {
    const html = fs.readFileSync(path.join(__dirname, "public", "index.html"));
    return send(res, 200, html, { "Content-Type": "text/html; charset=utf-8" });
  }

  // everything under /api requires the token
  if (p.startsWith("/api/")) {
    if (tokenOf(req, url) !== TOKEN) return send(res, 401, { ok: false, error: "bad or missing token" });
    try {
      await ensureBrowser();

      if (p === "/api/screenshot") {
        const buf = await withLock(() => page.screenshot({ type: "png" }));
        return send(res, 200, buf, { "Content-Type": "image/png" });
      }
      if (p === "/api/state") {
        let title = "";
        try { title = await page.title(); } catch (_) {}
        return send(res, 200, { ok: true, url: page.url(), title, viewport: VIEWPORT });
      }
      const name = p.slice("/api/".length);
      if (actions[name]) {
        const body = req.method === "POST" ? await readBody(req) : {};
        const result = await withLock(() => actions[name](body));
        return send(res, 200, { ok: true, ...result });
      }
      return send(res, 404, { ok: false, error: "unknown action: " + name });
    } catch (e) {
      return send(res, 500, { ok: false, error: String((e && e.message) || e) });
    }
  }

  return send(res, 404, { ok: false, error: "not found" });
});

server.listen(PORT, "0.0.0.0", () => {
  console.log("=".repeat(60));
  console.log("  Nexus Cloud Browser listening on 0.0.0.0:" + PORT);
  console.log("  Open the control panel:  http://<this-host>:" + PORT + "/");
  console.log("  Access token:  " + TOKEN);
  console.log("  (set CB_TOKEN to pin it; keep it secret — it guards your browser)");
  console.log("=".repeat(60));
});
