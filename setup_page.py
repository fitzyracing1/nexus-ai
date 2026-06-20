"""HTML page served at GET /setup — onboarding UI for new users.

Self-contained: inline CSS/JS, no external assets. Token is embedded server-side
so the page can make authenticated /activate calls without the user typing it.
"""

SETUP_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>AI Browser Bridge — Setup</title>
  <style>
    :root {
      --bg:#0b1220; --panel:#0f172a; --panel2:#111827; --border:#1f2937;
      --text:#e5e7eb; --muted:#94a3b8;
      --accent:#3b82f6; --accent2:#1d4ed8;
      --ok:#10b981; --okBg:#064e3b; --warn:#f59e0b; --warnBg:#713f12;
      --bad:#ef4444; --badBg:#7f1d1d;
    }
    * { box-sizing: border-box; }
    body { margin:0; font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
           background: var(--bg); color: var(--text); min-height:100vh; }
    .container { max-width: 720px; margin: 0 auto; padding: 32px 24px 64px; }
    h1 { margin: 0 0 6px; font-size: 24px; font-weight: 700; }
    .sub { color: var(--muted); margin: 0 0 28px; font-size: 14px; }
    .card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
            padding: 18px 20px; margin: 0 0 16px; }
    .step-h { display:flex; align-items:center; gap:10px; margin: 0 0 10px;
              font-size: 13px; text-transform: uppercase; letter-spacing: 1.5px; color: var(--muted); font-weight: 600; }
    .step-num { background: var(--accent); color: white; width: 22px; height: 22px;
                border-radius: 999px; display: inline-flex; align-items:center; justify-content:center;
                font-size: 12px; font-weight: 700; letter-spacing: 0; }
    h2 { margin: 0 0 12px; font-size: 18px; font-weight: 600; }
    p { line-height: 1.55; margin: 0 0 12px; }
    .row { display: flex; gap: 8px; align-items: center; }
    input { flex: 1; padding: 10px 12px; border-radius: 8px; border: 1px solid var(--border);
            background: #06080d; color: var(--text); font-family: ui-monospace, Consolas, monospace;
            font-size: 13px; }
    input:focus { outline: 2px solid var(--accent); }
    button { padding: 10px 14px; border: 0; border-radius: 8px; background: var(--accent);
             color: white; cursor: pointer; font-size: 14px; font-weight: 600; }
    button:hover { background: var(--accent2); }
    button.ghost { background: transparent; border: 1px solid var(--border); color: var(--text); }
    button.ghost:hover { background: var(--panel2); }
    code { background: var(--panel2); padding: 2px 6px; border-radius: 4px; font-size: 12px; }
    a { color: #60a5fa; }
    .badge { display: inline-flex; align-items: center; gap: 6px;
             padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600;
             border: 1px solid transparent; }
    .badge.ok   { background: var(--okBg);   color: #6ee7b7; border-color: #064e3b; }
    .badge.warn { background: var(--warnBg); color: #fcd34d; border-color: #713f12; }
    .badge.bad  { background: var(--badBg);  color: #fecaca; border-color: #7f1d1d; }
    .dot { width: 8px; height: 8px; border-radius: 999px; background: currentColor; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .stat { background: var(--panel2); border-radius: 8px; padding: 10px 12px; }
    .stat .lbl { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; }
    .stat .val { font-size: 16px; font-weight: 600; margin-top: 2px; }
    .msg { padding: 10px 12px; border-radius: 8px; font-size: 13px; margin-top: 10px; }
    .msg.ok { background: var(--okBg); color: #d1fae5; }
    .msg.bad { background: var(--badBg); color: #fee2e2; }
    .small { font-size: 12px; color: var(--muted); }
    .token { font-family: ui-monospace, Consolas, monospace; font-size: 13px; word-break: break-all; }
    .pulse { animation: pulse 1.6s ease-in-out infinite; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }
  </style>
</head>
<body>
  <div class="container">
    <h1>AI Browser Bridge</h1>
    <p class="sub">Get the bridge wired up to your Chrome and your AI client in three steps.</p>

    <!-- STATUS -->
    <div class="card">
      <div class="grid">
        <div class="stat">
          <div class="lbl">Bridge</div>
          <div class="val"><span id="bridgeBadge" class="badge ok"><span class="dot"></span>online</span></div>
        </div>
        <div class="stat">
          <div class="lbl">Extension</div>
          <div class="val"><span id="extBadge" class="badge warn pulse"><span class="dot"></span>waiting…</span></div>
        </div>
        <div class="stat">
          <div class="lbl">License</div>
          <div class="val"><span id="licBadge" class="badge warn"><span class="dot"></span>free tier</span></div>
        </div>
        <div class="stat">
          <div class="lbl">Today's usage</div>
          <div class="val" id="usageVal">—</div>
        </div>
      </div>
    </div>

    <!-- STEP 1: extension -->
    <div class="card">
      <div class="step-h"><span class="step-num">1</span> Install the Chrome extension</div>
      <p>If you haven't already, install <strong>AI Browser Bridge</strong> from the Chrome Web Store, or load this folder as unpacked at <code>chrome://extensions</code> (developer mode on).</p>
      <p class="small">The extension needs permission to read and change pages — that's what lets the AI click and type. Permissions only kick in once you connect.</p>
    </div>

    <!-- STEP 2: auth token -->
    <div class="card">
      <div class="step-h"><span class="step-num">2</span> Connect the extension</div>
      <p>Click the extension's icon in Chrome's toolbar, then paste this auth token into the popup and click <em>Save token</em>:</p>
      <div class="row">
        <input id="tokenField" readonly value="__TOKEN__" />
        <button id="copyTokenBtn">Copy</button>
      </div>
      <p class="small" style="margin-top:10px;">This token is unique to this install and never leaves your machine. It prevents websites you visit from quietly hitting the bridge on localhost.</p>
    </div>

    <!-- STEP 3: license -->
    <div class="card">
      <div class="step-h"><span class="step-num">3</span> Activate your license <span class="small" style="text-transform:none;letter-spacing:0;margin-left:8px;">(optional — free tier works without one)</span></div>
      <p>Paste the license key from your purchase email. Without a key, the bridge runs in <em>free tier</em> (25 commands per day).</p>
      <div class="row">
        <input id="licenseField" placeholder="XXXX-XXXX-XXXX-XXXX" autocomplete="off" spellcheck="false" />
        <button id="activateBtn">Activate</button>
      </div>
      <div id="licMsg"></div>
      <p class="small" style="margin-top:12px;">Don't have a key? <a id="buyLink" href="https://example.com/buy" target="_blank" rel="noopener">Buy a license</a>. One-time payment, no subscription.</p>
    </div>

    <div class="card">
      <div class="step-h">Test it</div>
      <p>Once steps 1 + 2 are done (Extension badge above turns green), try this from a terminal:</p>
      <pre style="background:var(--panel2);padding:12px;border-radius:8px;overflow:auto;font-size:12px;"><code>curl -X POST http://127.0.0.1:17777/command \
  -H "Content-Type: application/json" \
  -H "X-Auth-Token: __TOKEN__" \
  -d '{"action":"get_url"}'</code></pre>
      <p class="small">It should return the URL of whatever tab you currently have active.</p>
    </div>
  </div>

<script>
const TOKEN = "__TOKEN__";

const $ = (id) => document.getElementById(id);

async function refresh() {
  try {
    const s = await (await fetch("/status")).json();
    const last = s.extension_last_poll_seconds_ago;
    const extOk = last !== null && last < 30;
    setBadge("extBadge", extOk ? "ok" : "warn", extOk ? "connected" : "waiting…", extOk);
    if (!extOk) $("extBadge").classList.add("pulse"); else $("extBadge").classList.remove("pulse");

    const l = await (await fetch("/license")).json();
    if (l.licensed) {
      setBadge("licBadge", "ok", "licensed", true);
      $("usageVal").textContent = "unlimited";
      $("licenseField").value = l.license_key_masked || "";
    } else {
      setBadge("licBadge", "warn", "free tier", true);
      const used = l.used_today ?? 0;
      const lim = l.free_tier_limit;
      $("usageVal").textContent = `${used} / ${lim}`;
    }
  } catch (e) {
    setBadge("bridgeBadge", "bad", "offline", true);
  }
}

function setBadge(id, cls, text) {
  const el = $(id);
  el.classList.remove("ok","warn","bad");
  el.classList.add(cls);
  el.innerHTML = '<span class="dot"></span>' + text;
}

$("copyTokenBtn").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(TOKEN);
    const b = $("copyTokenBtn");
    const t = b.textContent;
    b.textContent = "Copied";
    setTimeout(() => b.textContent = t, 1200);
  } catch (e) { alert("Copy failed — select and copy manually."); }
});

$("activateBtn").addEventListener("click", async () => {
  const key = $("licenseField").value.trim();
  if (!key) { showMsg("Enter your license key first.", "bad"); return; }
  showMsg("Activating…", "ok");
  try {
    const r = await fetch("/activate", {
      method: "POST",
      headers: {"Content-Type":"application/json", "X-Auth-Token": TOKEN},
      body: JSON.stringify({license_key: key}),
    });
    const data = await r.json();
    if (data.ok) {
      showMsg("Activated. Free-tier limit removed.", "ok");
      refresh();
    } else {
      showMsg(data.message || data.error || "Activation failed.", "bad");
    }
  } catch (e) {
    showMsg("Network error: " + String(e), "bad");
  }
});

function showMsg(text, cls) {
  const m = $("licMsg");
  m.className = "msg " + cls;
  m.textContent = text;
}

refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""
