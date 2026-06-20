"""
License gating + auth token + free-tier rate limit for the ai-browser bridge.

Storage: %LOCALAPPDATA%\\ai-browser\\ on Windows, ~/.config/ai-browser/ elsewhere.
Files:
  config.json   {auth_token, license_key, license_status, license_checked_at,
                 license_instance_id, license_meta}
  state.json    {date: "YYYY-MM-DD", count: int}

LemonSqueezy is contacted only when LS_STORE_ID env var is set (production).
Without it, license activation is a no-op and the free-tier rate limit applies.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CONFIG_VERSION = 1
FREE_TIER_DAILY_LIMIT = int(os.environ.get("AI_BROWSER_FREE_LIMIT", "25"))

LS_API = "https://api.lemonsqueezy.com/v1/licenses"
LS_STORE_ID = os.environ.get("AI_BROWSER_LS_STORE_ID", "").strip()
# If set, license activate/validate calls go to LemonSqueezy. Otherwise no-op.

REVALIDATE_AFTER_S = 7 * 24 * 3600  # re-check license cache once per week

_state_lock = threading.Lock()


# --------------------- paths ---------------------
def config_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        p = Path(base) / "ai-browser"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        p = Path(base) / "ai-browser"
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_path() -> Path:
    return config_dir() / "config.json"


def state_path() -> Path:
    return config_dir() / "state.json"


# --------------------- config ---------------------
def _default_config() -> dict:
    return {
        "version": CONFIG_VERSION,
        "auth_token": secrets.token_hex(16),
        "license_key": None,
        "license_status": "free",         # free | active | inactive | expired | error
        "license_checked_at": None,
        "license_instance_id": None,
        "license_meta": None,
    }


def load_config() -> dict:
    p = config_path()
    if not p.exists():
        cfg = _default_config()
        _atomic_write(p, json.dumps(cfg, indent=2))
        return cfg
    try:
        cfg = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        cfg = _default_config()
        _atomic_write(p, json.dumps(cfg, indent=2))
        return cfg
    # repair missing keys
    base = _default_config()
    for k, v in base.items():
        cfg.setdefault(k, v)
    if not cfg.get("auth_token"):
        cfg["auth_token"] = secrets.token_hex(16)
        _atomic_write(p, json.dumps(cfg, indent=2))
    return cfg


def save_config(cfg: dict) -> None:
    _atomic_write(config_path(), json.dumps(cfg, indent=2))


def _atomic_write(path: Path, data: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


# --------------------- state (daily counter) ---------------------
def load_state() -> dict:
    p = state_path()
    if not p.exists():
        return {"date": _today(), "count": 0}
    try:
        s = json.loads(p.read_text(encoding="utf-8-sig"))
        return {"date": s.get("date", _today()), "count": int(s.get("count", 0))}
    except Exception:
        return {"date": _today(), "count": 0}


def save_state(state: dict) -> None:
    _atomic_write(state_path(), json.dumps(state))


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def check_and_increment(cfg: dict) -> tuple[bool, dict]:
    """Returns (allowed, info). info has: licensed, limit, used_today, remaining."""
    with _state_lock:
        licensed = is_licensed(cfg)
        if licensed:
            return True, {
                "licensed": True,
                "limit": None,
                "used_today": None,
                "remaining": None,
            }
        st = load_state()
        if st["date"] != _today():
            st = {"date": _today(), "count": 0}
        if st["count"] >= FREE_TIER_DAILY_LIMIT:
            return False, {
                "licensed": False,
                "limit": FREE_TIER_DAILY_LIMIT,
                "used_today": st["count"],
                "remaining": 0,
            }
        st["count"] += 1
        save_state(st)
        return True, {
            "licensed": False,
            "limit": FREE_TIER_DAILY_LIMIT,
            "used_today": st["count"],
            "remaining": FREE_TIER_DAILY_LIMIT - st["count"],
        }


# --------------------- license logic ---------------------
def is_licensed(cfg: dict) -> bool:
    return cfg.get("license_status") == "active" and cfg.get("license_key")


def license_status(cfg: dict) -> dict:
    info = check_and_peek(cfg)
    return {
        "status": cfg.get("license_status", "free"),
        "licensed": info["licensed"],
        "license_key_masked": _mask(cfg.get("license_key")),
        "checked_at": cfg.get("license_checked_at"),
        "instance_id": cfg.get("license_instance_id"),
        "free_tier_limit": FREE_TIER_DAILY_LIMIT,
        "used_today": info["used_today"],
        "remaining_today": info["remaining"],
        "ls_configured": bool(LS_STORE_ID),
    }


def check_and_peek(cfg: dict) -> dict:
    """Read-only version: doesn't increment the counter."""
    if is_licensed(cfg):
        return {"licensed": True, "limit": None, "used_today": None, "remaining": None}
    st = load_state()
    if st["date"] != _today():
        st = {"date": _today(), "count": 0}
    return {
        "licensed": False,
        "limit": FREE_TIER_DAILY_LIMIT,
        "used_today": st["count"],
        "remaining": max(0, FREE_TIER_DAILY_LIMIT - st["count"]),
    }


def _mask(key):
    if not key:
        return None
    if len(key) < 8:
        return "***"
    return f"{key[:4]}…{key[-4:]}"


# --------------------- LemonSqueezy API ---------------------
def activate_license(cfg: dict, license_key: str, instance_name: str = None) -> dict:
    """Activate a license against LemonSqueezy. Returns (ok, message, raw)."""
    if not LS_STORE_ID:
        # Demo / dev mode: any non-empty key activates locally without phoning home.
        # In production builds, bake LS_STORE_ID into the .exe so this path is never hit.
        cfg["license_key"] = license_key
        cfg["license_status"] = "active"
        cfg["license_checked_at"] = int(time.time())
        cfg["license_instance_id"] = "dev-local-" + secrets.token_hex(4)
        cfg["license_meta"] = {"mode": "dev-no-ls", "note": "LS_STORE_ID not configured"}
        save_config(cfg)
        return {"ok": True, "message": "activated (dev mode, no LemonSqueezy)", "raw": None}

    if not license_key:
        return {"ok": False, "message": "license_key required", "raw": None}

    body = {
        "license_key": license_key,
        "instance_name": instance_name or f"ai-browser-{secrets.token_hex(4)}",
    }
    raw, err = _ls_post("/activate", body)
    if err:
        return {"ok": False, "message": err, "raw": raw}
    if not raw.get("activated"):
        return {"ok": False, "message": raw.get("error") or "activation refused", "raw": raw}
    cfg["license_key"] = license_key
    cfg["license_status"] = "active"
    cfg["license_checked_at"] = int(time.time())
    cfg["license_instance_id"] = (raw.get("instance") or {}).get("id")
    cfg["license_meta"] = raw.get("meta")
    save_config(cfg)
    return {"ok": True, "message": "activated", "raw": raw}


def revalidate_if_stale(cfg: dict) -> dict:
    """Re-validate cached license with LemonSqueezy if cache is older than a week.
    Updates cfg in place. Returns the new license_status."""
    if not is_licensed(cfg):
        return cfg.get("license_status", "free")
    if not LS_STORE_ID:
        return cfg.get("license_status", "free")
    last = cfg.get("license_checked_at") or 0
    if time.time() - last < REVALIDATE_AFTER_S:
        return cfg["license_status"]

    body = {
        "license_key": cfg["license_key"],
        "instance_id": cfg.get("license_instance_id"),
    }
    raw, err = _ls_post("/validate", body)
    if err:
        # network glitch; trust the cache for now
        return cfg["license_status"]
    if raw.get("valid"):
        cfg["license_status"] = "active"
        cfg["license_checked_at"] = int(time.time())
    else:
        cfg["license_status"] = "inactive"
        cfg["license_meta"] = raw
    save_config(cfg)
    return cfg["license_status"]


def _ls_post(endpoint: str, body: dict) -> tuple[dict, str | None]:
    url = LS_API + endpoint
    data = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in body.items() if v is not None).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "ai-browser-bridge/0.2",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {"http_status": e.code}
        return body, f"HTTP {e.code}"
    except Exception as e:
        return {}, str(e)
