const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const tokenInput = document.getElementById("tokenInput");

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = "status " + (cls || "mid");
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

refresh();
setInterval(refresh, 1500);
