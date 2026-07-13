# Nexus Cloud Browser

A real Chromium runs on a server; you drive it from a phone-friendly web panel.
The browser lives in the cloud, so there's nothing to install on your phone and
no browser-extension support required — you just open a web page, tap the live
screenshot to click, type to fill fields, and navigate.

This is the mobile-friendly counterpart to the local bridge + extension: same
idea (an AI/you driving a real browser), but the browser is hosted instead of
being your own local Chrome.

## Run locally

```bash
cd cloud-browser
npm install
npx playwright install chromium   # first time only
CB_TOKEN=pick-a-long-secret npm start
```

Open `http://localhost:8080/`, paste the token, and drive.

## Deploy (Docker — works on Render, Railway, Fly.io, a VPS…)

```bash
docker build -t nexus-cloud-browser .
docker run -p 8080:8080 -e CB_TOKEN=pick-a-long-secret nexus-cloud-browser
```

The `mcr.microsoft.com/playwright` base image ships Chromium and all its OS
dependencies, so it runs anywhere Docker does. Set `CB_TOKEN` in the platform's
environment. If you don't, the server generates one and prints it in the logs on
boot.

Then open `https://<your-host>/` on your phone.

## Security

The browser is on the public internet once deployed, so **the token is the only
thing between a stranger and your session**:

- Always set a long, random `CB_TOKEN`.
- Rotate it by restarting with a new value.
- Prefer a host that terminates HTTPS (Render/Railway/Fly give you TLS for free).

## API

Every `/api/*` call needs the token as `?token=` or the `X-Token` header.

| method | path             | body                          | does                        |
|--------|------------------|-------------------------------|-----------------------------|
| GET    | `/api/screenshot`| —                             | PNG of the current viewport |
| GET    | `/api/state`     | —                             | `{url, title, viewport}`    |
| POST   | `/api/navigate`  | `{url}`                       | go to a URL (or search)     |
| POST   | `/api/click`     | `{x,y}` or `{selector}`       | click                       |
| POST   | `/api/type`      | `{text, selector?, submit?}`  | type / fill                 |
| POST   | `/api/key`       | `{key}`                       | press a key                 |
| POST   | `/api/scroll`    | `{dy}`                        | scroll                      |
| POST   | `/api/back` `/api/forward` `/api/reload` | —         | history / reload            |

The action vocabulary mirrors the Nexus bridge, so anything that can POST JSON
(a script, an AI agent) can drive it too — not just the web panel.
