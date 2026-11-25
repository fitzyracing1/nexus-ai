"""
Copyright (c) 2026 Joshua Almeida

Stripe Checkout, Customer Portal, and webhook event application.
Uses StripeClient (not the deprecated global api_key). Test mode only.
"""
from __future__ import annotations

import time
from typing import Any

from keys import generate_license_key, stripe_trial_days
import db as store

# Paid statuses that should unlock the extension (server-side).
UNLOCK_STATUSES = {"active", "trialing"}


def map_subscription_status(stripe_status: str | None, *, extra: str | None = None) -> str:
    if extra:
        return extra
    mapping = {
        "trialing": "trialing",
        "active": "active",
        "past_due": "past_due",
        "unpaid": "canceled",
        "canceled": "canceled",
        "incomplete": "pending",
        "incomplete_expired": "canceled",
        "paused": "canceled",
    }
    return mapping.get(stripe_status or "", "canceled")


def period_end_from_subscription(sub: dict[str, Any] | None) -> int | None:
    if not sub:
        return None
    # API 2025+ may use items.data[0].current_period_end
    value = sub.get("current_period_end") or sub.get("trial_end")
    if value:
        return int(value)
    items = ((sub.get("items") or {}).get("data")) or []
    if items:
        value = items[0].get("current_period_end")
        if value:
            return int(value)
    return None


def trial_end_from_subscription(sub: dict[str, Any] | None) -> int | None:
    if not sub:
        return None
    value = sub.get("trial_end")
    return int(value) if value else None


def create_checkout(
    *,
    client,
    settings,
    conn,
    install_id: str | None,
    trial_started_at_ms: int | None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    trial_days = stripe_trial_days(trial_started_at_ms, now_ms)
    license_key = generate_license_key()

    params: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": settings.stripe_price_id, "quantity": 1}],
        "success_url": f"{settings.public_base_url}/success?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{settings.public_base_url}/canceled",
        "allow_promotion_codes": True,
        "payment_method_collection": "always",
        "client_reference_id": (install_id or "")[:200] or None,
        "metadata": {
            "license_key": license_key,
            "install_id": install_id or "",
            "product": "nexus-ai-extension",
        },
        "subscription_data": {
            "metadata": {
                "license_key": license_key,
                "install_id": install_id or "",
                "product": "nexus-ai-extension",
            }
        },
        # Tag this flow in the Dashboard. Suffix is fixed per server start in app.py
        # so tests can pass a deterministic identifier via settings if needed.
        "integration_identifier": getattr(
            settings, "integration_identifier", "nexusai-c8f3kq1z"
        ),
    }
    # Never set payment_method_types — let Stripe pick eligible methods.
    if trial_days >= 1:
        params["subscription_data"]["trial_period_days"] = trial_days

    # Drop None client_reference_id rather than send it.
    if not params.get("client_reference_id"):
        params.pop("client_reference_id", None)

    session = _create_session(client, params)
    session_id = _obj_get(session, "id")
    url = _obj_get(session, "url")
    store.insert_pending(
        conn,
        license_key=license_key,
        install_id=install_id,
        checkout_session_id=session_id,
    )
    return {
        "url": url,
        "session_id": session_id,
        "trial_days": trial_days,
        # Key is only returned after checkout completes (success page / verify).
    }


def _create_session(client, params: dict[str, Any]):
    # Stripe Python SDK 17: StripeClient.v1.checkout.sessions.create(params=...)
    sessions = _sessions_api(client)
    try:
        return sessions.create(params=params)
    except TypeError:
        return sessions.create(**params)


def _sessions_api(client):
    v1 = getattr(client, "v1", None)
    if v1 is not None and hasattr(v1, "checkout"):
        return v1.checkout.sessions
    return client.checkout.sessions


def create_portal(*, client, settings, customer_id: str) -> dict[str, Any]:
    params = {
        "customer": customer_id,
        "return_url": f"{settings.public_base_url}/portal-done",
    }
    portal = _portal_api(client)
    try:
        session = portal.create(params=params)
    except TypeError:
        session = portal.create(**params)
    return {"url": _obj_get(session, "url")}


def _portal_api(client):
    v1 = getattr(client, "v1", None)
    if v1 is not None and hasattr(v1, "billing_portal"):
        return v1.billing_portal.sessions
    return client.billing_portal.sessions


def _obj_get(obj: Any, key: str, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def retrieve_subscription(client, subscription_id: str) -> dict[str, Any] | None:
    if not subscription_id:
        return None
    try:
        v1 = getattr(client, "v1", None)
        api = v1.subscriptions if v1 is not None else client.subscriptions
        try:
            sub = api.retrieve(subscription_id)
        except TypeError:
            sub = api.retrieve(subscription_id)
        if hasattr(sub, "to_dict"):
            return sub.to_dict()
        if isinstance(sub, dict):
            return sub
        return dict(sub)
    except Exception:
        return None


def apply_subscription(
    conn,
    *,
    license_key: str | None = None,
    subscription: dict[str, Any] | None = None,
    customer_id: str | None = None,
    email: str | None = None,
    checkout_session_id: str | None = None,
    extra_status: str | None = None,
) -> dict[str, Any] | None:
    rec = None
    if license_key:
        rec = store.get_by_key(conn, license_key)
    if rec is None and checkout_session_id:
        rec = store.get_by_session(conn, checkout_session_id)
    if rec is None and subscription:
        rec = store.get_by_subscription(conn, subscription.get("id") or "")
    if rec is None and customer_id:
        rec = store.get_by_customer(conn, customer_id)
    if rec is None:
        return None

    fields: dict[str, Any] = {}
    if subscription:
        fields["stripe_subscription_id"] = subscription.get("id") or rec.get("stripe_subscription_id")
        fields["status"] = map_subscription_status(subscription.get("status"), extra=extra_status)
        fields["trial_end"] = trial_end_from_subscription(subscription)
        fields["current_period_end"] = period_end_from_subscription(subscription)
        sub_customer = subscription.get("customer")
        if isinstance(sub_customer, dict):
            sub_customer = sub_customer.get("id")
        if sub_customer:
            fields["stripe_customer_id"] = sub_customer
    elif extra_status:
        fields["status"] = extra_status
    if customer_id:
        fields["stripe_customer_id"] = customer_id
    if email:
        fields["email"] = email
    if checkout_session_id:
        fields["stripe_checkout_session_id"] = checkout_session_id
    return store.update_license(conn, rec["license_key"], **fields)


def handle_event(conn, event: dict[str, Any], *, client=None) -> dict[str, Any]:
    event_id = event.get("id") or ""
    event_type = event.get("type") or ""
    if event_id and not store.mark_processed(conn, event_id, event_type):
        return {"ok": True, "duplicate": True}

    data = (event.get("data") or {}).get("object") or {}

    if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        if event_type == "checkout.session.completed":
            payment_status = data.get("payment_status")
            # For a subscription trial, payment_status may be "no_payment_required".
            if payment_status not in (None, "paid", "no_payment_required"):
                return {"ok": True, "skipped": "payment_not_complete"}
        return {"ok": True, "license": _from_checkout(conn, data, client=client)}

    if event_type in ("customer.subscription.updated", "customer.subscription.created", "customer.subscription.deleted"):
        license_key = ((data.get("metadata") or {}).get("license_key")) or None
        rec = apply_subscription(
            conn,
            license_key=license_key,
            subscription=data,
        )
        return {"ok": True, "license": rec}

    if event_type == "invoice.paid":
        rec = _from_invoice(conn, data, client=client, extra_status=None)
        return {"ok": True, "license": rec}

    if event_type == "invoice.payment_failed":
        rec = _from_invoice(conn, data, client=client, extra_status="past_due")
        return {"ok": True, "license": rec}

    if event_type == "charge.refunded":
        rec = _from_charge(conn, data, client=client, extra_status="refunded")
        return {"ok": True, "license": rec}

    if event_type == "charge.dispute.created":
        rec = _from_charge(conn, data, client=client, extra_status="disputed")
        return {"ok": True, "license": rec}

    if event_type == "radar.early_fraud_warning.created":
        rec = _from_charge(conn, data, client=client, extra_status="restricted")
        return {"ok": True, "license": rec}

    return {"ok": True, "ignored": event_type}


def _from_checkout(conn, session: dict[str, Any], *, client=None) -> dict[str, Any] | None:
    metadata = session.get("metadata") or {}
    license_key = metadata.get("license_key")
    customer_id = session.get("customer")
    if isinstance(customer_id, dict):
        customer_id = customer_id.get("id")
    email = session.get("customer_details", {}).get("email") if isinstance(session.get("customer_details"), dict) else None
    email = email or session.get("customer_email")
    subscription_id = session.get("subscription")
    if isinstance(subscription_id, dict):
        sub = subscription_id
        subscription_id = sub.get("id")
    else:
        sub = retrieve_subscription(client, subscription_id) if client and subscription_id else None
        if sub is None and subscription_id:
            sub = {"id": subscription_id, "status": "trialing" if session.get("payment_status") == "no_payment_required" else "active", "customer": customer_id}
    return apply_subscription(
        conn,
        license_key=license_key,
        subscription=sub,
        customer_id=customer_id,
        email=email,
        checkout_session_id=session.get("id"),
    )


def _subscription_id_from_invoice(invoice: dict[str, Any]) -> str | None:
    sub = invoice.get("subscription")
    if isinstance(sub, dict):
        return sub.get("id")
    if sub:
        return str(sub)
    # Newer invoice shape: parent.subscription_details.subscription
    parent = invoice.get("parent") or {}
    details = parent.get("subscription_details") or {}
    sub = details.get("subscription")
    if isinstance(sub, dict):
        return sub.get("id")
    return sub


def _from_invoice(conn, invoice: dict[str, Any], *, client=None, extra_status: str | None) -> dict[str, Any] | None:
    customer_id = invoice.get("customer")
    if isinstance(customer_id, dict):
        customer_id = customer_id.get("id")
    sub_id = _subscription_id_from_invoice(invoice)
    sub = retrieve_subscription(client, sub_id) if client and sub_id else None
    if sub is None and sub_id:
        sub = {"id": sub_id, "status": "past_due" if extra_status == "past_due" else "active", "customer": customer_id}
    email = invoice.get("customer_email")
    return apply_subscription(
        conn,
        subscription=sub,
        customer_id=customer_id,
        email=email,
        extra_status=extra_status,
    )


def _customer_id_from_charge(charge: dict[str, Any]) -> str | None:
    customer = charge.get("customer")
    if isinstance(customer, dict):
        return customer.get("id")
    if customer:
        return str(customer)
    return None


def _from_charge(conn, charge: dict[str, Any], *, client=None, extra_status: str) -> dict[str, Any] | None:
    """
    Resolve charge -> invoice -> subscription (Stripe object graph), then
    update the matching license. Metadata is only a fallback.
    """
    customer_id = _customer_id_from_charge(charge)
    invoice = charge.get("invoice")
    invoice_id = invoice.get("id") if isinstance(invoice, dict) else invoice
    sub = None
    if isinstance(invoice, dict):
        sub_id = _subscription_id_from_invoice(invoice)
        if sub_id:
            sub = retrieve_subscription(client, sub_id) if client else {"id": sub_id, "customer": customer_id}
    # Fallback: license_key in charge metadata if the object graph is missing.
    license_key = (charge.get("metadata") or {}).get("license_key")
    rec = apply_subscription(
        conn,
        license_key=license_key,
        subscription=sub,
        customer_id=customer_id,
        extra_status=extra_status,
    )
    return rec
