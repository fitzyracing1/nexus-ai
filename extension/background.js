importScripts("license.js", "license-config.js");

// Service worker: polls the local bridge for commands, dispatches to the
// active tab's content script, returns results.
//
// All bridge calls send the auth token in the X-Auth-Token header.
// The token is stored in chrome.storage.local under 'auth_token'.
// User pastes it into the popup once; bridge prints it on startup.

const BRIDGE = "http://127.0.0.1:17777";
const POLL_TIMEOUT_MS = 7000;  // matches bridge's 5s long-poll + 2s headroom


// ----- paid license / 7-day trial -----
const LICENSE_REFRESH_MINUTES = 360; // 6 hours
const LICENSE_STORAGE_KEYS = [
  "trialStartedAt",
  "installId",
  "license_key",
  "license_status",
  "license_period_end",
  "license_trial_end",
  "license_verified_at",
  "license_server_url",
];

function licenseServerUrlFrom(stored) {
  const override = (stored || "").trim();
  if (override) return override.replace(/\/$/, "");
  const fallback = (typeof NEXUS_LICENSE_SERVER_URL === "string" && NEXUS_LICENSE_SERVER_URL) ||
    NexusLicense.DEFAULT_SERVER_URL;
  return String(fallback).replace(/\/$/, "");
}

async function loadLicenseState() {
  const stored = await chrome.storage.local.get(LICENSE_STORAGE_KEYS);
  let trialStartedAt = stored.trialStartedAt;
  let installId = stored.installId;
  if (!trialStartedAt || !installId) {
    try {
      const synced = await chrome.storage.sync.get(["trialStartedAt", "installId"]);
      if (!trialStartedAt && synced.trialStartedAt) trialStartedAt = synced.trialStartedAt;
      if (!installId && synced.installId) installId = synced.installId;
    } catch (_) {}
  }
  const ensured = NexusLicense.ensureTrial(
    { trialStartedAt, installId },
    Date.now()
  );
  if (ensured.trialStartedAt !== stored.trialStartedAt || ensured.installId !== stored.installId) {
    await chrome.storage.local.set({
      trialStartedAt: ensured.trialStartedAt,
      installId: ensured.installId,
    });
    try {
      await chrome.storage.sync.set({
        trialStartedAt: ensured.trialStartedAt,
        installId: ensured.installId,
      });
    } catch (_) {}
  }
  return {
    trialStartedAt: ensured.trialStartedAt,
    installId: ensured.installId,
    serverUrl: licenseServerUrlFrom(stored.license_server_url),
    license: {
      key: stored.license_key || "",
      status: stored.license_status || "none",
      currentPeriodEnd: stored.license_period_end ? Number(stored.license_period_end) * 1000 : 0,
      trialEnd: stored.license_trial_end ? Number(stored.license_trial_end) * 1000 : 0,
      lastVerifiedAt: stored.license_verified_at || 0,
    },
  };
}

function currentEntitlement(state) {
  return NexusLicense.evaluateEntitlement(state, Date.now());
}

async function requireEntitlement() {
  const state = await loadLicenseState();
  const ent = currentEntitlement(state);
  if (ent.allowed) return { ok: true, ent, state };
  return { ok: false, ent, state };
}

async function persistVerifiedLicense(key, payload) {
  const status = payload.status || "none";
  await chrome.storage.local.set({
    license_key: key,
    license_status: status,
    license_period_end: payload.current_period_end || 0,
    license_trial_end: payload.trial_end || 0,
    license_verified_at: Date.now(),
  });
}

function isTrustedLicenseSender(sender) {
  const url = (sender && (sender.url || (sender.tab && sender.tab.url))) || "";
  const extOrigin = `chrome-extension://${chrome.runtime.id}/`;
  if (url.startsWith(extOrigin)) return true;
  if (url.startsWith("https://fitzyracing1.github.io/nexus-ai")) return true;
  if (url.startsWith("http://127.0.0.1:8787/")) return true;
  return false;
}

async function verifyLicenseWithServer(state) {
  const key = state.license && state.license.key;
  if (!key || !NexusLicense.isValidLicenseKey(key)) return state;
  try {
    const resp = await fetch(`${state.serverUrl}/v1/license/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ license_key: NexusLicense.normalizeKey(key), install_id: state.installId }),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok && data && data.ok) {
      await persistVerifiedLicense(NexusLicense.normalizeKey(key), data);
    }
  } catch (_) {
    // Offline: entitlement may still use the last verified cache + grace window.
  }
  return loadLicenseState();
}

async function startCheckout() {
  const state = await loadLicenseState();
  const resp = await fetch(`${state.serverUrl}/v1/checkout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      install_id: state.installId,
      trial_started_at: state.trialStartedAt,
    }),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok || !data.url) {
    throw new Error(data.error || `checkout failed (${resp.status})`);
  }
  await chrome.storage.local.set({ checkout_session_id: data.session_id || "" });
  await chrome.tabs.create({ url: data.url });
  if (data.session_id) pollCheckoutSession(state.serverUrl, data.session_id);
  return { ok: true, session_id: data.session_id };
}

function pollCheckoutSession(serverUrl, sessionId) {
  let attempts = 0;
  const tick = async () => {
    attempts += 1;
    try {
      const resp = await fetch(`${serverUrl}/v1/checkout/${encodeURIComponent(sessionId)}`);
      const data = await resp.json().catch(() => ({}));
      if (data && data.license_key) {
        await persistVerifiedLicense(data.license_key, data);
        await appendLog({ kind: "license", error: "license activated" });
        return;
      }
    } catch (_) {}
    if (attempts < 30) setTimeout(tick, 2000);
  };
  setTimeout(tick, 2000);
}

async function activateLicenseKey(rawKey) {
  const key = NexusLicense.normalizeKey(rawKey);
  if (!NexusLicense.isValidLicenseKey(key)) {
    return { ok: false, error: "That does not look like a Nexus AI license key." };
  }
  const state = await loadLicenseState();
  try {
    const resp = await fetch(`${state.serverUrl}/v1/license/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ license_key: key, install_id: state.installId }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.ok) {
      return { ok: false, error: data.error || "License could not be verified." };
    }
    await persistVerifiedLicense(key, data);
    return { ok: true, status: data.status };
  } catch (e) {
    return { ok: false, error: "Could not reach the license server. Is it running?" };
  }
}

async function openBillingPortal() {
  const state = await loadLicenseState();
  const key = state.license && state.license.key;
  if (!key) throw new Error("No license key saved yet.");
  const resp = await fetch(`${state.serverUrl}/v1/portal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ license_key: NexusLicense.normalizeKey(key) }),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok || !data.url) throw new Error(data.error || "Could not open billing portal");
  await chrome.tabs.create({ url: data.url });
  return { ok: true };
}

async function licenseSnapshot() {
  let state = await loadLicenseState();
  if (state.license && state.license.key) {
    state = await verifyLicenseWithServer(state);
  }
  const ent = currentEntitlement(state);
  return {
    ok: true,
    allowed: ent.allowed,
    source: ent.source,
    status: ent.status,
    message: ent.message,
    trialRemainingMs: ent.trialRemainingMs,
    trialRemainingLabel: NexusLicense.formatRemaining(ent.trialRemainingMs),
    licenseKeyMasked: state.license && state.license.key
      ? `${state.license.key.slice(0, 10)}…${state.license.key.slice(-4)}`
      : "",
    hasLicense: !!(state.license && state.license.key),
    serverUrl: state.serverUrl,
  };
}


let connected = false;
let stopRequested = false;
let activePoll = null;
let loopRunning = false;  // guard against concurrent pollLoop invocations

async function getToken() {
  const { auth_token = "" } = await chrome.storage.local.get("auth_token");
  return auth_token;
}

function authHeaders(token) {
  const h = { "Content-Type": "application/json" };
  if (token) h["X-Auth-Token"] = token;
  return h;
}

async function setConnected(v, reason) {
  connected = v;
  await chrome.storage.local.set({
    connected: v,
    statusReason: reason || "",
    statusAt: Date.now(),
  });
  try {
    chrome.action.setBadgeText({ text: v ? "ON" : "" });
    chrome.action.setBadgeBackgroundColor({ color: v ? "#22c55e" : "#ef4444" });
  } catch (_) {}
}

async function appendLog(entry) {
  const { log = [] } = await chrome.storage.local.get("log");
  log.push({ ts: Date.now(), ...entry });
  while (log.length > 50) log.shift();
  await chrome.storage.local.set({ log });
}

async function pollLoop() {
  if (loopRunning) return;  // only one loop at a time
  loopRunning = true;
  try {
    await _pollLoopInner();
  } finally {
    loopRunning = false;
  }
}

async function _pollLoopInner() {
  await setConnected(false, "connecting");
  while (!stopRequested) {
    const token = await getToken();
    if (!token) {
      await setConnected(false, "no auth token — paste it in the extension popup");
      await sleep(2500);
      continue;
    }
    try {
      const controller = new AbortController();
      activePoll = controller;
      const timer = setTimeout(() => controller.abort(), POLL_TIMEOUT_MS);
      const resp = await fetch(`${BRIDGE}/next-command`, {
        method: "GET",
        headers: authHeaders(token),
        signal: controller.signal,
        cache: "no-store",
      });
      clearTimeout(timer);
      activePoll = null;

      if (resp.status === 401) {
        await setConnected(false, "auth token rejected (check the popup)");
        await sleep(3000);
        continue;
      }
      if (!connected) await setConnected(true, "");

      if (resp.status === 204) continue;
      if (!resp.ok) {
        await appendLog({ kind: "poll_error", status: resp.status });
        await sleep(1000);
        continue;
      }
      const cmd = await resp.json();
      handleCommand(cmd, token).catch(async (e) => {
        await appendLog({ kind: "dispatch_error", error: String(e), cmd });
        await postResult(cmd.request_id, false, null, String(e), token);
      });
    } catch (e) {
      activePoll = null;
      if (stopRequested) break;
      const wasConnected = connected;
      if (connected) await setConnected(false, "bridge unreachable");
      // AbortError on a not-yet-connected loop is expected MV3 SW lifecycle noise.
      // Only log it if we had been connected (i.e. a real disconnect).
      const isAbort = e && (e.name === "AbortError" || String(e).includes("aborted"));
      if (!isAbort || wasConnected) {
        await appendLog({ kind: "poll_fail", error: String(e) });
      }
      await sleep(isAbort ? 200 : 1500);
    }
  }
  await setConnected(false, "stopped");
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab) {
    const tabs = await chrome.tabs.query({ active: true });
    return tabs[0] || null;
  }
  return tab;
}

async function ensureContentScript(tabId) {
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
  } catch (_) {}
}

async function sendToContent(tabId, message, timeoutMs = 25000) {
  return await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("content script timeout")), timeoutMs);
    try {
      chrome.tabs.sendMessage(tabId, message, (resp) => {
        clearTimeout(timer);
        if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
        else resolve(resp);
      });
    } catch (e) { clearTimeout(timer); reject(e); }
  });
}

async function handleCommand(cmd, token) {
  const { request_id, action, args = {} } = cmd;
  await appendLog({ kind: "command", action, args });
  try {
    const gate = await requireEntitlement();
    if (!gate.ok) {
      await postResult(request_id, false, null, gate.ent.message, token);
      return;
    }
    if (action === "navigate") {
      const tab = await getActiveTab();
      if (!tab) throw new Error("no active tab");
      await chrome.tabs.update(tab.id, { url: args.url });
      await postResult(request_id, true, { url: args.url }, null, token);
      return;
    }
    if (action === "screenshot") {
      const tab = await getActiveTab();
      if (!tab) throw new Error("no active tab");
      const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {
        format: args.format || "png",
        quality: args.quality || 90,
      });
      await postResult(request_id, true, { data_url: dataUrl }, null, token);
      return;
    }
    if (action === "list_tabs") {
      const tabs = await chrome.tabs.query({});
      await postResult(request_id, true, {
        tabs: tabs.map((t) => ({ id: t.id, title: t.title, url: t.url, active: t.active, windowId: t.windowId })),
      }, null, token);
      return;
    }
    if (action === "switch_tab") {
      const tab = await chrome.tabs.update(args.tab_id, { active: true });
      await chrome.windows.update(tab.windowId, { focused: true });
      await postResult(request_id, true, { id: tab.id, url: tab.url }, null, token);
      return;
    }

    const tab = await getActiveTab();
    if (!tab) throw new Error("no active tab");
    if (!/^https?:|^file:/.test(tab.url || "")) {
      throw new Error(`active tab not scriptable: ${tab.url}`);
    }
    await ensureContentScript(tab.id);
    const resp = await sendToContent(tab.id, { action, args });
    if (!resp) { await postResult(request_id, false, null, "no response from content script", token); return; }
    if (resp.ok) await postResult(request_id, true, resp.result || {}, null, token);
    else await postResult(request_id, false, null, resp.error || "content script error", token);
  } catch (e) {
    await postResult(request_id, false, null, String(e && e.message || e), token);
  }
}

async function postResult(request_id, ok, result, error, token) {
  const body = { request_id, ok };
  if (result !== undefined && result !== null) body.result = result;
  if (error) body.error = error;
  try {
    await fetch(`${BRIDGE}/result`, {
      method: "POST",
      headers: authHeaders(token),
      body: JSON.stringify(body),
    });
  } catch (e) {
    await appendLog({ kind: "post_result_fail", error: String(e), request_id });
  }
  await appendLog({ kind: "result", request_id, ok, error });
}

// Keep the service worker alive: alarm fires every ~30s, wakes the SW,
// and (if no loop is running because SW was just restarted) re-arms the loop.
chrome.alarms.create("keepalive", { periodInMinutes: 0.5 });
chrome.alarms.create("license-refresh", { periodInMinutes: LICENSE_REFRESH_MINUTES });
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "keepalive" && !loopRunning && !stopRequested) {
    pollLoop();
  }
  if (a.name === "license-refresh") {
    loadLicenseState().then(verifyLicenseWithServer).catch(() => {});
  }
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    if (msg.type === "ping") {
      sendResponse({ connected, stopped: stopRequested });
    } else if (msg.type === "start") {
      stopRequested = false;
      pollLoop();
      sendResponse({ ok: true });
    } else if (msg.type === "stop") {
      stopRequested = true;
      if (activePoll) activePoll.abort();
      sendResponse({ ok: true });
    } else if (msg.type === "set_token") {
      await chrome.storage.local.set({ auth_token: msg.token || "" });
      // Force the loop to re-check
      if (activePoll) activePoll.abort();
      sendResponse({ ok: true });
    } else if (msg.type === "get_log") {
      const { log = [] } = await chrome.storage.local.get("log");
      sendResponse({ log });
    } else if (msg.type === "get_license") {
      sendResponse(await licenseSnapshot());
    } else if (msg.type === "start_checkout") {
      try {
        sendResponse(await startCheckout());
      } catch (e) {
        sendResponse({ ok: false, error: String(e && e.message || e) });
      }
    } else if (msg.type === "activate_license") {
      if (!isTrustedLicenseSender(_sender)) {
        sendResponse({ ok: false, error: "untrusted sender" });
        return;
      }
      sendResponse(await activateLicenseKey(msg.key || ""));
    } else if (msg.type === "open_portal") {
      try {
        sendResponse(await openBillingPortal());
      } catch (e) {
        sendResponse({ ok: false, error: String(e && e.message || e) });
      }
    } else if (msg.type === "set_license_server_url") {
      const url = String(msg.url || "").trim();
      await chrome.storage.local.set({ license_server_url: url });
      sendResponse({ ok: true });
    } else {
      sendResponse({ ok: false, error: "unknown message" });
    }
  })();
  return true;
});

chrome.runtime.onInstalled.addListener(() => { stopRequested = false; loadLicenseState().catch(() => {}); pollLoop(); });
chrome.runtime.onStartup.addListener(() => { stopRequested = false; pollLoop(); });
stopRequested = false;
loadLicenseState().catch(() => {});
pollLoop();
