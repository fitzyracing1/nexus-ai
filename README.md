# AI Browser Bridge

Lets any local AI (Claude Code, curl, scripts) drive your real Chrome tabs —
click, type, fill forms, read page content, screenshot, navigate.

## Pieces

```
bridge.py              tiny stdlib HTTP server on 127.0.0.1:17777
extension/             Chrome MV3 extension (background + content script)
```

## One-time setup

1. **Start the bridge** (leave running):
   ```
   python C:\Users\EC\ai-browser\bridge.py
   ```
2. **Install the extension**:
   - Open `chrome://extensions`
   - Toggle **Developer mode** on (top-right)
   - Click **Load unpacked**
   - Pick `C:\Users\EC\ai-browser\extension`
3. **Connect**: click the extension's puzzle-piece icon → pin "AI Browser Bridge"
   → click its icon → press **Connect**. Status should turn green ("Connected to bridge").

## Calling it from anywhere

Anything that can POST JSON to `http://127.0.0.1:17777/command` can drive it.

```bash
# Click an element by CSS selector
curl -X POST http://127.0.0.1:17777/command \
  -H "Content-Type: application/json" \
  -d '{"action":"click","args":{"selector":"#submit-btn"}}'

# Click by visible text
curl -X POST http://127.0.0.1:17777/command \
  -H "Content-Type: application/json" \
  -d '{"action":"click","args":{"selector":"text=Sign In"}}'

# Type into an input
curl -X POST http://127.0.0.1:17777/command \
  -H "Content-Type: application/json" \
  -d '{"action":"type","args":{"selector":"input[name=q]","text":"hello","submit":true}}'

# Get current page URL/title
curl http://127.0.0.1:17777/command -X POST \
  -H "Content-Type: application/json" \
  -d '{"action":"get_url"}'
```

## Actions

| action       | args                                                | returns                            |
|--------------|-----------------------------------------------------|------------------------------------|
| `click`      | `{selector}` or `{x,y}`                             | element descriptor                 |
| `type`/`fill`| `{selector?, text, append?, submit?}`               | typed-into element + final value   |
| `get_url`    | —                                                   | `{url, title}`                     |
| `get_text`   | `{selector?, max_chars?}`                           | `{text, length, truncated}`        |
| `get_html`   | `{selector?, max_chars?}`                           | `{html, length, truncated}`        |
| `query`      | `{selector, limit?}`                                | matching elements descriptors      |
| `scroll_to`  | `{selector}` or `{x,y}`                             | `{scrolled_to}`                    |
| `wait_for`   | `{selector, timeout_ms?, state?}`                   | `{found}`                          |
| `press`      | `{key, selector?}`                                  | `{pressed}`                        |
| `screenshot` | `{format?, quality?}`                               | `{data_url}` (base64 PNG/JPEG)     |
| `navigate`   | `{url}`                                             | `{url}`                            |
| `list_tabs`  | —                                                   | `[{id,title,url,active,windowId}]` |
| `switch_tab` | `{tab_id}`                                          | `{id, url}`                        |

## Selector syntax

- CSS selectors: `#id`, `.class`, `button[type=submit]`
- Text match: `text=Sign In` (exact or substring, deepest match wins)
- Fuzzy text: `text~=sign` (substring, case-insensitive)

## Health / debug

```
GET  http://127.0.0.1:17777/health
GET  http://127.0.0.1:17777/status
```

## Notes

- The bridge binds to **127.0.0.1 only** — not reachable from other machines.
- The extension cannot inject into `chrome://`, the Chrome Web Store, or other
  internal pages. Open a normal http(s) tab first.
- Service worker keepalive uses `chrome.alarms`. The long-poll itself also
  counts as active work, so the SW stays alive while a poll is in flight.
- Commands without an active tab error out fast — you'll see "no active tab".
