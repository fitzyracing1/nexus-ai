/**
 * Copyright (c) 2026 Joshua Almeida
 *
 * Trial and paid-license entitlement helpers for the Nexus AI extension.
 * This file is loaded by the service worker (importScripts) and by Node tests.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.NexusLicense = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const TRIAL_DAYS = 7;
  const TRIAL_MS = TRIAL_DAYS * 24 * 60 * 60 * 1000;
  const OFFLINE_GRACE_MS = 24 * 60 * 60 * 1000;
  const PAST_DUE_GRACE_MS = 3 * 24 * 60 * 60 * 1000;
  const DEFAULT_SERVER_URL = "http://127.0.0.1:8787";
  const LICENSE_KEY_RE = /^NEXUS(?:-[A-Z0-9]{4}){3,}$/;

  const PAID_OK = new Set(["active", "trialing"]);

  function ensureTrial(state, now) {
    const next = Object.assign({}, state || {});
    if (!next.trialStartedAt || !Number.isFinite(Number(next.trialStartedAt))) {
      next.trialStartedAt = now;
    }
    if (!next.installId) {
      next.installId = randomInstallId();
    }
    return next;
  }

  function randomInstallId() {
    if (typeof crypto !== "undefined" && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function remainingTrialMs(trialStartedAt, now) {
    const start = Number(trialStartedAt);
    if (!Number.isFinite(start)) return 0;
    return Math.max(0, start + TRIAL_MS - now);
  }

  function isValidLicenseKey(key) {
    return typeof key === "string" && LICENSE_KEY_RE.test(key.trim().toUpperCase());
  }

  function normalizeKey(key) {
    return String(key || "").trim().toUpperCase();
  }

  /**
   * Decide whether bridge commands may run.
   * Paid Stripe status wins while valid; otherwise the local 7-day trial applies.
   */
  function evaluateEntitlement(state, now) {
    const trialLeft = remainingTrialMs(state && state.trialStartedAt, now);
    const license = (state && state.license) || {};
    const status = license.status || "none";
    const periodEnd = Number(license.currentPeriodEnd || 0) || 0;
    const lastVerified = Number(license.lastVerifiedAt || 0) || 0;
    const verifiedFresh = lastVerified > 0 && (now - lastVerified) <= OFFLINE_GRACE_MS;

    if (PAID_OK.has(status)) {
      if (periodEnd && now > periodEnd && !verifiedFresh) {
        // Stale cache past the known period end: do not treat as paid.
      } else {
        return {
          allowed: true,
          source: status,
          status,
          trialRemainingMs: trialLeft,
          message: status === "trialing" ? "Stripe trial" : "Subscription active",
        };
      }
    }

    if (status === "past_due") {
      const graceEnd = (periodEnd || lastVerified) + PAST_DUE_GRACE_MS;
      if ((periodEnd || lastVerified) && now <= graceEnd) {
        return {
          allowed: true,
          source: "past_due_grace",
          status,
          trialRemainingMs: trialLeft,
          message: "Payment past due — update billing to stay subscribed",
        };
      }
    }

    if (trialLeft > 0) {
      return {
        allowed: true,
        source: "local_trial",
        status: status === "none" ? "trial" : status,
        trialRemainingMs: trialLeft,
        message: "Free trial",
      };
    }

    let message = "Your 7-day free trial has ended. Subscribe for $5/month to keep using Nexus AI.";
    if (status === "past_due") message = "Payment failed. Update your billing to keep using Nexus AI.";
    if (status === "canceled") message = "Your subscription is canceled. Subscribe again to keep using Nexus AI.";
    if (status === "refunded" || status === "disputed" || status === "restricted") {
      message = "This subscription is not active. Contact support or subscribe again.";
    }

    return {
      allowed: false,
      source: "none",
      status: status === "none" ? "trial_ended" : status,
      trialRemainingMs: 0,
      message,
    };
  }

  function formatRemaining(ms) {
    const total = Math.max(0, Math.ceil(ms / 1000));
    const days = Math.floor(total / 86400);
    const hours = Math.floor((total % 86400) / 3600);
    if (days > 1) return `${days} days left`;
    if (days === 1) return hours > 0 ? `1 day ${hours}h left` : "1 day left";
    if (hours >= 1) return `${hours}h left`;
    const minutes = Math.max(1, Math.floor(total / 60));
    return `${minutes}m left`;
  }

  return {
    TRIAL_DAYS,
    TRIAL_MS,
    OFFLINE_GRACE_MS,
    PAST_DUE_GRACE_MS,
    DEFAULT_SERVER_URL,
    LICENSE_KEY_RE,
    ensureTrial,
    remainingTrialMs,
    isValidLicenseKey,
    normalizeKey,
    evaluateEntitlement,
    formatRemaining,
  };
});
