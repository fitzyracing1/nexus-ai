"""
grok_client.py — drive the ai-browser bridge with xAI's Grok via tool-calling.

Usage:
    $env:XAI_API_KEY = "xai-..."
    python grok_client.py "open hacker news and tell me the top story"
    python grok_client.py            # interactive REPL

Env:
    XAI_API_KEY   required
    XAI_MODEL     optional, defaults to "grok-4-latest"
    XAI_BASE_URL  optional, defaults to "https://api.x.ai/v1"
    AI_BROWSER_PORT  optional, matches bridge (default 17777)
"""
import json
import os
import sys
import time
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

import license as licmod


BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = int(os.environ.get("AI_BROWSER_PORT", "17777"))
BRIDGE_URL = f"http://{BRIDGE_HOST}:{BRIDGE_PORT}"

XAI_BASE = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/")
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4-latest")

MAX_TOOL_ROUNDS = 20
CMD_TIMEOUT_MS = 30000
SCREENSHOT_PREVIEW_CHARS = 200


def _http_json(method: str, url: str, headers: dict, body: dict | None, timeout: float = 60.0) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urlrequest.Request(url, data=data, method=method, headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"error": raw}


def bridge_call(action: str, args: dict, auth_token: str) -> dict:
    headers = {"Content-Type": "application/json", "X-Auth-Token": auth_token}
    body = {"action": action, "args": args, "timeout_ms": CMD_TIMEOUT_MS}
    try:
        status, payload = _http_json("POST", f"{BRIDGE_URL}/command", headers, body,
                                     timeout=(CMD_TIMEOUT_MS / 1000.0) + 5)
    except URLError as e:
        return {"ok": False, "error": f"bridge unreachable at {BRIDGE_URL}: {e}"}
    if status == 401:
        return {"ok": False, "error": "bridge rejected auth token (regenerate config.json?)"}
    if status == 402:
        return {"ok": False, "error": payload.get("error", "free-tier limit reached")}
    return payload


def _shorten_for_model(action: str, result: dict) -> dict:
    """Screenshots return a huge data_url. Strip it before sending back to Grok
    and just report that an image was captured."""
    if not isinstance(result, dict):
        return result
    if action == "screenshot" and "data_url" in result:
        du = result["data_url"]
        return {
            "ok": True,
            "note": "screenshot captured (data_url omitted from context)",
            "data_url_prefix": (du[:SCREENSHOT_PREVIEW_CHARS] + "...") if isinstance(du, str) else None,
            "data_url_length": len(du) if isinstance(du, str) else None,
        }
    return result


# ---------------- tool schema (xAI is OpenAI-compatible) ----------------
def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOLS = [
    _tool("navigate", "Navigate the active browser tab to a URL.",
          {"url": {"type": "string", "description": "Absolute http(s) URL"}}, ["url"]),
    _tool("get_url", "Get the URL and title of the active tab.", {}, []),
    _tool("get_text", "Get visible text content of the page or a specific selector.",
          {"selector": {"type": "string", "description": "CSS selector, optional (defaults to <body>)"}}, []),
    _tool("get_html", "Get outerHTML of the page or a specific selector.",
          {"selector": {"type": "string", "description": "CSS selector, optional"}}, []),
    _tool("click", "Click an element matching a CSS selector.",
          {"selector": {"type": "string"}}, ["selector"]),
    _tool("type", "Type text into an input/textarea matching a CSS selector.",
          {"selector": {"type": "string"},
           "text": {"type": "string"},
           "submit": {"type": "boolean", "description": "Press Enter after typing"}},
          ["selector", "text"]),
    _tool("press", "Press a keyboard key (e.g. 'Enter', 'Escape', 'Tab').",
          {"key": {"type": "string"}}, ["key"]),
    _tool("scroll_to", "Scroll an element into view.",
          {"selector": {"type": "string"}}, ["selector"]),
    _tool("wait_for", "Wait for a selector to appear (up to a few seconds).",
          {"selector": {"type": "string"},
           "timeout_ms": {"type": "integer", "description": "Max wait, default 10000"}},
          ["selector"]),
    _tool("query", "Run document.querySelectorAll and return matches' tag/text/attrs.",
          {"selector": {"type": "string"},
           "limit": {"type": "integer"}}, ["selector"]),
    _tool("screenshot", "Capture a screenshot of the visible tab. Returns a note; the image is not sent back into context.",
          {}, []),
    _tool("list_tabs", "List all open browser tabs.", {}, []),
    _tool("switch_tab", "Activate a tab by id (from list_tabs).",
          {"tab_id": {"type": "integer"}}, ["tab_id"]),
]

TOOL_NAMES = {t["function"]["name"] for t in TOOLS}


# ---------------- xAI chat ----------------
def chat_once(api_key: str, messages: list[dict]) -> dict:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": XAI_MODEL,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
    }
    status, payload = _http_json("POST", f"{XAI_BASE}/chat/completions", headers, body, timeout=120)
    if status != 200:
        raise RuntimeError(f"xAI API error {status}: {json.dumps(payload)[:500]}")
    return payload


def run_turn(api_key: str, auth_token: str, user_prompt: str, history: list[dict]) -> list[dict]:
    """Run one user turn through Grok, executing any tool calls against the
    bridge, and appending all messages (including assistant + tool results) to
    history. Returns the updated history."""
    history.append({"role": "user", "content": user_prompt})

    for round_i in range(MAX_TOOL_ROUNDS):
        resp = chat_once(api_key, history)
        choice = resp["choices"][0]
        msg = choice["message"]
        finish = choice.get("finish_reason")

        # Persist the assistant message verbatim (tool_calls included).
        assistant_entry = {"role": "assistant", "content": msg.get("content") or ""}
        if msg.get("tool_calls"):
            assistant_entry["tool_calls"] = msg["tool_calls"]
        history.append(assistant_entry)

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            text = msg.get("content") or ""
            print(f"\n[grok] {text}\n")
            return history

        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if name not in TOOL_NAMES:
                tool_result = {"ok": False, "error": f"unknown tool: {name}"}
            else:
                print(f"  -> {name}({json.dumps(args)[:120]})")
                bridge_resp = bridge_call(name, args, auth_token)
                if bridge_resp.get("ok"):
                    raw_result = bridge_resp.get("result", {})
                    tool_result = {"ok": True, "result": _shorten_for_model(name, raw_result)}
                else:
                    tool_result = {"ok": False, "error": bridge_resp.get("error", "unknown error")}
                print(f"     ok={tool_result['ok']} {('err=' + str(tool_result.get('error'))) if not tool_result['ok'] else ''}")

            history.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(tool_result)[:8000],
            })

        if finish == "stop":
            # Some models emit stop alongside tool_calls; loop again so Grok can summarize.
            continue

    print("\n[grok_client] hit MAX_TOOL_ROUNDS without a final answer.\n")
    return history


SYSTEM_PROMPT = (
    "You are Grok driving the user's real Chrome browser through a local bridge. "
    "Use the provided tools to navigate, read, and interact with web pages. "
    "Prefer get_text over get_html. After actions that change the page (click, navigate, type+submit), "
    "call wait_for or get_url before reading content. Be concise in your final answer."
)


def main():
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        print("error: XAI_API_KEY env var not set. Get a key at https://console.x.ai", file=sys.stderr)
        sys.exit(2)

    cfg = licmod.load_config()
    auth_token = cfg["auth_token"]

    # Sanity check: bridge reachable?
    try:
        status, _ = _http_json("GET", f"{BRIDGE_URL}/health", {}, None, timeout=2)
        if status != 200:
            print(f"warn: bridge /health returned {status}", file=sys.stderr)
    except URLError:
        print(f"error: bridge not running at {BRIDGE_URL}. Start ai-browser-bridge first.", file=sys.stderr)
        sys.exit(3)

    history = [{"role": "system", "content": SYSTEM_PROMPT}]

    args = sys.argv[1:]
    if args:
        prompt = " ".join(args)
        run_turn(api_key, auth_token, prompt, history)
        return

    print(f"grok_client — model={XAI_MODEL}  bridge={BRIDGE_URL}")
    print("type your request, blank line to quit.\n")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            return
        try:
            run_turn(api_key, auth_token, line, history)
        except Exception as e:
            print(f"[error] {e}\n")


if __name__ == "__main__":
    main()
