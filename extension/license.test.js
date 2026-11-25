/**
 * Copyright (c) 2026 Joshua Almeida
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const L = require("./license.js");

const DAY = 24 * 60 * 60 * 1000;
const now = Date.UTC(2026, 10, 25, 16, 0, 0);

test("ensureTrial assigns a start time and install id once", () => {
  const first = L.ensureTrial({}, now);
  assert.equal(first.trialStartedAt, now);
  assert.ok(first.installId);
  const second = L.ensureTrial(first, now + DAY);
  assert.equal(second.trialStartedAt, now);
  assert.equal(second.installId, first.installId);
});

test("local trial is allowed for 7 days and blocks after", () => {
  const state = { trialStartedAt: now, license: { status: "none" } };
  const mid = L.evaluateEntitlement(state, now + 3 * DAY);
  assert.equal(mid.allowed, true);
  assert.equal(mid.source, "local_trial");
  assert.ok(mid.trialRemainingMs > 0);

  const end = L.evaluateEntitlement(state, now + L.TRIAL_MS);
  assert.equal(end.allowed, false);
  assert.equal(end.status, "trial_ended");

  const almost = L.evaluateEntitlement(state, now + L.TRIAL_MS - 1);
  assert.equal(almost.allowed, true);
});

test("active Stripe subscription unlocks after the local trial ends", () => {
  const state = {
    trialStartedAt: now - 10 * DAY,
    license: {
      status: "active",
      currentPeriodEnd: now + 20 * DAY,
      lastVerifiedAt: now,
    },
  };
  const got = L.evaluateEntitlement(state, now);
  assert.equal(got.allowed, true);
  assert.equal(got.source, "active");
});

test("trialing Stripe subscription is allowed", () => {
  const state = {
    trialStartedAt: now,
    license: { status: "trialing", currentPeriodEnd: now + 7 * DAY, lastVerifiedAt: now },
  };
  const got = L.evaluateEntitlement(state, now + 2 * DAY);
  assert.equal(got.allowed, true);
  assert.equal(got.source, "trialing");
});

test("expired paid cache without a fresh verification is not trusted", () => {
  const state = {
    trialStartedAt: now - 10 * DAY,
    license: {
      status: "active",
      currentPeriodEnd: now - DAY,
      lastVerifiedAt: now - 2 * DAY,
    },
  };
  const got = L.evaluateEntitlement(state, now);
  assert.equal(got.allowed, false);
});

test("fresh offline cache still allows access shortly after period end", () => {
  const state = {
    trialStartedAt: now - 10 * DAY,
    license: {
      status: "active",
      currentPeriodEnd: now - 60 * 60 * 1000,
      lastVerifiedAt: now - 60 * 60 * 1000,
    },
  };
  const got = L.evaluateEntitlement(state, now);
  assert.equal(got.allowed, true);
  assert.equal(got.source, "active");
});

test("past_due keeps a short grace window, then blocks", () => {
  const state = {
    trialStartedAt: now - 10 * DAY,
    license: {
      status: "past_due",
      currentPeriodEnd: now - DAY,
      lastVerifiedAt: now - DAY,
    },
  };
  assert.equal(L.evaluateEntitlement(state, now).allowed, true);
  assert.equal(L.evaluateEntitlement(state, now).source, "past_due_grace");
  assert.equal(L.evaluateEntitlement(state, now + 3 * DAY).allowed, false);
});

test("canceled and refunded licenses are blocked after the trial", () => {
  for (const status of ["canceled", "refunded", "disputed", "restricted"]) {
    const state = {
      trialStartedAt: now - 10 * DAY,
      license: { status, lastVerifiedAt: now },
    };
    const got = L.evaluateEntitlement(state, now);
    assert.equal(got.allowed, false, status);
  }
});

test("license key format", () => {
  assert.equal(L.isValidLicenseKey("NEXUS-ABCD-EFGH-IJKL"), true);
  assert.equal(L.isValidLicenseKey("nexus-abcd-efgh-ijkl"), true);
  assert.equal(L.isValidLicenseKey("NEXUS-AB"), false);
  assert.equal(L.isValidLicenseKey(""), false);
  assert.equal(L.normalizeKey(" nexus-abcd-efgh-ijkl "), "NEXUS-ABCD-EFGH-IJKL");
});

test("formatRemaining", () => {
  assert.equal(L.formatRemaining(5 * DAY), "5 days left");
  assert.equal(L.formatRemaining(DAY), "1 day left");
  assert.equal(L.formatRemaining(2 * 60 * 60 * 1000), "2h left");
});
