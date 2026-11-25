"""
Copyright (c) 2026 Joshua Almeida

SQLite storage for licenses and processed webhook event ids.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS licenses (
            id INTEGER PRIMARY KEY,
            license_key TEXT NOT NULL UNIQUE,
            install_id TEXT,
            email TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            stripe_checkout_session_id TEXT,
            trial_end INTEGER,
            current_period_end INTEGER,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_licenses_session
            ON licenses(stripe_checkout_session_id);
        CREATE INDEX IF NOT EXISTS idx_licenses_subscription
            ON licenses(stripe_subscription_id);
        CREATE INDEX IF NOT EXISTS idx_licenses_customer
            ON licenses(stripe_customer_id);

        CREATE TABLE IF NOT EXISTS webhook_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            processed_at INTEGER NOT NULL
        );
        """
    )
    conn.commit()


def now_s() -> int:
    return int(time.time())


def insert_pending(
    conn: sqlite3.Connection,
    *,
    license_key: str,
    install_id: str | None,
    checkout_session_id: str | None,
) -> dict[str, Any]:
    ts = now_s()
    with _lock:
        conn.execute(
            """
            INSERT INTO licenses (
                license_key, install_id, status, stripe_checkout_session_id,
                created_at, updated_at
            ) VALUES (?, ?, 'pending', ?, ?, ?)
            """,
            (license_key, install_id, checkout_session_id, ts, ts),
        )
        conn.commit()
    return get_by_key(conn, license_key)


def get_by_key(conn: sqlite3.Connection, license_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM licenses WHERE license_key = ?", (license_key,)
    ).fetchone()
    return dict(row) if row else None


def get_by_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM licenses WHERE stripe_checkout_session_id = ?",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def get_by_subscription(conn: sqlite3.Connection, sub_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM licenses WHERE stripe_subscription_id = ?",
        (sub_id,),
    ).fetchone()
    return dict(row) if row else None


def get_by_customer(conn: sqlite3.Connection, customer_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM licenses
        WHERE stripe_customer_id = ?
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (customer_id,),
    ).fetchone()
    return dict(row) if row else None


def update_license(conn: sqlite3.Connection, license_key: str, **fields: Any) -> dict[str, Any] | None:
    if not fields:
        return get_by_key(conn, license_key)
    fields = {k: v for k, v in fields.items() if k != "license_key"}
    fields["updated_at"] = now_s()
    assignments = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [license_key]
    with _lock:
        conn.execute(f"UPDATE licenses SET {assignments} WHERE license_key = ?", values)
        conn.commit()
    return get_by_key(conn, license_key)


def already_processed(conn: sqlite3.Connection, event_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM webhook_events WHERE event_id = ?", (event_id,)
    ).fetchone()
    return row is not None


def mark_processed(conn: sqlite3.Connection, event_id: str, event_type: str) -> bool:
    """Return True if this is the first time the event was recorded."""
    with _lock:
        cur = conn.execute(
            "INSERT OR IGNORE INTO webhook_events (event_id, event_type, processed_at) VALUES (?, ?, ?)",
            (event_id, event_type, now_s()),
        )
        conn.commit()
        return cur.rowcount == 1
