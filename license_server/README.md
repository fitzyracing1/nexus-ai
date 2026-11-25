# Nexus AI license server

Copyright (c) 2026 Joshua Almeida

Small local (or later, hosted) server that:

1. Starts a Stripe Checkout **subscription** for the Nexus AI Chrome extension
   at **$5/month**, with a **7-day trial** aligned to the remaining local trial.
2. Records the Stripe customer and subscription against a license key.
3. Lets the extension verify whether that subscription is still good.
4. Opens the Stripe Customer Portal so the buyer can update payment details or cancel.

The Chrome Web Store does not process payments. Checkout and billing happen here,
through Stripe, not through Google.

## Hard limits in this tree

- **Test mode only.** The process refuses `sk_live_` / `rk_live_` keys and will
  not start.
- **Do not deploy** this to Railway or anywhere else until Joshua says so.
- **Do not put secrets in git.** Copy `.env.example` to `.env`. Prefer a
  [restricted test API key](https://docs.stripe.com/keys.md#manage-your-api-keys)
  (`rk_test_...`) over a secret key. Store production secrets in the host’s
  secrets vault, not in source.

## Local test-mode setup

1. Create or sign in to a Stripe account and stay in **test mode** (or a
   [sandbox](https://docs.stripe.com/sandboxes.md)).
2. Create a restricted test key with permission to create Checkout Sessions,
   read and write Customers and Subscriptions, and create Billing Portal
   Sessions.
3. Create a test-mode product/price of **$5.00 USD / month**, either in the
   Dashboard or with:

   ```bash
   python -m pip install -r requirements.txt
   STRIPE_SECRET_KEY=rk_test_... python scripts/create_test_price.py
   ```

4. Copy `.env.example` to `.env` and fill `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID`,
   and `STRIPE_WEBHOOK_SECRET`.
5. In another terminal, forward webhooks (required for subscriptions):

   ```bash
   stripe listen --forward-to localhost:8787/v1/webhook
   ```

   Paste the printed `whsec_...` into `.env`.
6. Enable the [Customer Portal](https://dashboard.stripe.com/test/settings/billing/portal)
   in the test Dashboard (cancellation and payment-method updates).
7. Start the server:

   ```bash
   python app.py
   ```

8. In the unpacked 0.2.0 extension, the default license server is
   `http://127.0.0.1:8787`. Open the popup, use the trial, then click
   **Subscribe — $5/month** and pay with a
   [Stripe test card](https://docs.stripe.com/testing.md) such as
   `4242 4242 4242 4242`.

### What the trial does

- On first install the extension starts a **local 7-day trial**. No card and
  no account are required. Bridge commands work during that week.
- **Subscribe** opens Stripe Checkout. If some of the local trial remains,
  Checkout starts a Stripe subscription trial of that remaining length
  (at most 7 days) and collects a payment method. The first $5 charge happens
  when that trial ends. If the local trial is already over, Checkout charges
  immediately.
- After Checkout completes, the success page shows a `NEXUS-...` license key.
  The extension stores it and re-checks the license server periodically.
- When the trial is over and there is no active or trialing subscription,
  the extension stops carrying out bridge commands and asks the user to
  subscribe. Connecting and pasting a token still work so they can see status.

Existing `license.py` (the optional LemonSqueezy / daily free-tier limit inside
`bridge.py`) is unchanged and is a separate system.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness |
| POST | `/v1/checkout` | Create a Checkout Session (`{install_id, trial_started_at}`) |
| GET | `/v1/checkout/cs_...` | Status; returns `license_key` only after the subscription is trialing or active |
| POST | `/v1/license/verify` | `{license_key}` → current status |
| POST | `/v1/portal` | `{license_key}` → Customer Portal URL |
| POST | `/v1/webhook` | Stripe webhooks (signature required) |
| GET | `/success`, `/canceled` | Human-facing Checkout return pages |

Webhook events handled: `checkout.session.completed`,
`checkout.session.async_payment_succeeded`, `customer.subscription.*`,
`invoice.paid`, `invoice.payment_failed`, `charge.refunded`,
`charge.dispute.created`, `radar.early_fraud_warning.created`.

## Going live (needs Joshua’s OK)

Nothing here is production-ready until these are done on purpose:

1. A real Stripe account, with live-mode keys kept in a secrets vault — not
   in this repo. Switch the server’s key check only when live mode is approved
   (it currently refuses live keys).
2. A live `STRIPE_PRICE_ID` for $5/month, and a live webhook endpoint
   pointing at `https://<host>/v1/webhook`.
3. A place to host this server (for example Railway). **Do not deploy until
   Joshua says so.**
4. Set `PUBLIC_BASE_URL` to that HTTPS origin, and change
   `extension/license-config.js` to the same origin before packing the 0.2.0
   Chrome Web Store zip.
5. [Stripe Tax](https://docs.stripe.com/billing/taxes/collect-taxes.md): if
   you will charge customers in the US or EU, register and enable Stripe Tax
   before turning on `automatic_tax`. This tree leaves automatic tax off on
   purpose so a missing registration cannot silently collect no tax.
6. Test the live webhook and Customer Portal, then upload a **new 0.2.0 zip**
   to the Chrome Web Store (do not interrupt the pending 0.1.1 review). See
   the pull request for listing-text implications.

## Tests

From the repository root:

```bash
python -m pip install -r license_server/requirements.txt
python -m unittest discover -s license_server/tests -v
node --test extension/license.test.js
```
