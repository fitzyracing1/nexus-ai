const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const tokenInput = document.getElementById("tokenInput");
const licenseNote = document.getElementById("licenseNote");
const subscribeBtn = document.getElementById("subscribeBtn");
const portalBtn = document.getElementById("portalBtn");
const activateBtn = document.getElementById("activateBtn");
const licenseInput = document.getElementById("licenseInput");

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = "status " + (cls || "mid");
}

async function refreshLicense() {
  if (!licenseNote) return;
  try {
    const info = await chrome.runtime.sendMessage({ type: "get_license" });
    if (!info) return;
    if (info.allowed && info.source === "local_trial") {
      licenseNote.className = "note";
      licenseNote.textContent = `Free trial — ${info.trialRemainingLabel}. Then $5/month.`;
    } else if (info.allowed && (info.source === "active" || info.source === "trialing")) {
      licenseNote.className = "note";
      const label = info.source === "trialing" ? "Stripe trial active" : "Subscription active";
      licenseNote.textContent = info.licenseKeyMasked ? `${label} (${info.licenseKeyMasked})` : label;
    } else if (info.allowed && info.source === "past_due_grace") {
      licenseNote.className = "note bad";
      licenseNote.textContent = info.message;
    } else {
      licenseNote.className = "note bad";
      licenseNote.textContent = info.message || "Trial ended. Subscribe to keep using Nexus AI.";
    }
  } catch (e) {
    licenseNote.className = "note";
    licenseNote.textContent = "Could not read license status.";
  }
}

async function refresh() {
  const { connected, statusReason, auth_token = "" } =
    await chrome.storage.local.get(["connected", "statusReason", "auth_token"]);

  if (document.activeElement !== tokenInput) {
    tokenInput.value = auth_token;
  }

  if (connected) setStatus("Connected to bridge", "ok");
  else setStatus(statusReason ? `Disconnected — ${statusReason}` : "Disconnected", "bad");

  const { log = [] } = await chrome.storage.local.get("log");
  logEl.innerHTML = "";
  for (const entry of log.slice(-30).reverse()) {
    const div = document.createElement("div");
    const isErr = (entry.kind && entry.kind.includes("error")) || entry.kind === "poll_fail" || (entry.kind === "result" && !entry.ok);
    const isOk = entry.kind === "result" && entry.ok;
    div.className = "entry " + (isErr ? "err" : isOk ? "ok" : "");
    const t = new Date(entry.ts).toLocaleTimeString();
    let detail = "";
    if (entry.kind === "command") detail = `${entry.action} ${JSON.stringify(entry.args || {}).slice(0,80)}`;
    else if (entry.kind === "result") detail = `result ${entry.request_id?.slice(0,8)} ${entry.ok ? "ok" : "ERR: " + (entry.error || "")}`;
    else detail = `${entry.kind} ${entry.error || ""}`;
    div.textContent = `[${t}] ${detail}`;
    logEl.appendChild(div);
  }
  await refreshLicense();
}

document.getElementById("saveTokenBtn").addEventListener("click", async () => {
  const tok = tokenInput.value.trim();
  await chrome.runtime.sendMessage({ type: "set_token", token: tok });
  setStatus("token saved — reconnecting…", "mid");
  setTimeout(refresh, 800);
});

document.getElementById("reconnectBtn").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "stop" });
  await chrome.runtime.sendMessage({ type: "start" });
  setTimeout(refresh, 500);
});

if (subscribeBtn) {
  subscribeBtn.addEventListener("click", async () => {
    licenseNote.className = "note";
    licenseNote.textContent = "Opening Stripe Checkout…";
    const resp = await chrome.runtime.sendMessage({ type: "start_checkout" });
    if (!resp || !resp.ok) {
      licenseNote.className = "note bad";
      licenseNote.textContent = (resp && resp.error)
        ? `Checkout failed: ${resp.error}. Is the license server running at 127.0.0.1:8787?`
        : "Checkout failed.";
    }
  });
}

if (portalBtn) {
  portalBtn.addEventListener("click", async () => {
    const resp = await chrome.runtime.sendMessage({ type: "open_portal" });
    if (!resp || !resp.ok) {
      licenseNote.className = "note bad";
      licenseNote.textContent = (resp && resp.error) || "No billing portal available yet.";
    }
  });
}

if (activateBtn) {
  activateBtn.addEventListener("click", async () => {
    const key = licenseInput.value.trim();
    const resp = await chrome.runtime.sendMessage({ type: "activate_license", key });
    if (resp && resp.ok) {
      licenseNote.className = "note";
      licenseNote.textContent = `License activated (${resp.status}).`;
      licenseInput.value = "";
      refreshLicense();
    } else {
      licenseNote.className = "note bad";
      licenseNote.textContent = (resp && resp.error) || "Activation failed.";
    }
  });
}

refresh();
setInterval(refresh, 1500);
