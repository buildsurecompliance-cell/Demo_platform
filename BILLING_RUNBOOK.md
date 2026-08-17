# Billing Runbook

This runbook covers Stripe Test Mode operations for BuildSure Compliance.

BuildSure uses Stripe as the external billing processor, but local access is
controlled by `Subscription` and Organization `plan_key`. Do not change
`User.paid` to repair Stripe issues.

## Safety Rules

- Do not commit real Stripe keys, webhook secrets, price IDs, customer IDs, or
  subscription IDs.
- Do not store raw webhook payloads.
- Do not log webhook signatures, checkout URLs, portal URLs, card data, or
  secret values.
- Do not map plans by invoice amount, invoice description, or substring.
- Do not edit production data directly without a database backup and a written
  incident note.

## Checkout Fails

1. Confirm `BILLING_PROVIDER=stripe`.
2. Confirm `STRIPE_SECRET_KEY` is present in the environment.
3. Confirm `STRIPE_STARTER_PRICE_ID` and `STRIPE_PROFESSIONAL_PRICE_ID` match
   Stripe Test Mode Price IDs.
4. Confirm success, cancel, and portal return URLs use the active environment
   domain.
5. Check logs for `stripe_checkout_created` or a safe reason code.
6. Do not manually upgrade the Organization plan without diagnosing the Stripe
   mapping.

## Webhook Returns 400

Likely causes:

- webhook secret does not match the endpoint;
- Stripe forwarded to the wrong URL;
- request body was modified before signature verification.

Use Stripe Test Mode or Stripe CLI to resend the event after fixing the
configuration.

## Webhook Returns 500

Stripe will retry failed webhook deliveries. Inspect `BillingEvent` by provider
and external event ID. Confirm:

- status;
- attempt count;
- last attempt time;
- safe error reason;
- linked Organization and Subscription.

After correcting the cause, let Stripe retry or resend the event. Do not delete
`BillingEvent` rows as a normal recovery step.

## Event Stuck In Processing

`BILLING_EVENT_PROCESSING_TIMEOUT_SECONDS` defaults to 300 seconds.

- If an event is still inside the timeout window, a duplicate delivery is left
  alone to avoid parallel processing.
- If the timeout has elapsed, a retry may resume processing.
- Do not force-delete the event. Use resend/retry and inspect logs.

## Plan Looks Incorrect

1. Confirm the Stripe Subscription has exactly one expected Price ID.
2. Confirm the Price ID exists in the central Stripe price mapping.
3. Confirm `BillingEvent` did not end as failed or ignored.
4. Use owner/admin `POST /billing/reconcile` after correcting configuration.
5. Do not map by invoice amount or metadata alone.

## Account Blocked After Payment

1. Open `/billing/status` as an owner/admin.
2. Confirm provider, status, current period end, last Stripe sync, and access
   decision.
3. Inspect recent `BillingEvent` rows for failures or out-of-order events.
4. If Stripe shows the Subscription as active/trialing, run reconciliation.
5. Do not use `User.paid` as a workaround.

## Manual Test Mode Scenarios

- checkout Starter;
- checkout Professional;
- successful payment;
- failed payment;
- cancel at period end;
- immediate cancellation;
- Customer Portal session;
- duplicate webhook delivery;
- out-of-order subscription update;
- invoice event followed by subscription snapshot reconciliation.

No real cards or production Stripe keys are required for these scenarios.

## Test Mode Setup Checklist

1. Use Stripe Test Mode only.
2. Create `BuildSure Starter` and `BuildSure Professional` Products.
3. Create one active recurring monthly Price for each Product.
4. Keep quantity fixed at `1`.
5. Configure local values in `.env.test.local`.
6. Start the app locally.
7. Start Stripe CLI forwarding to `/billing/webhook/stripe`.
8. Copy the CLI webhook secret only into the local ignored environment file.
9. Run `scripts/validate_stripe_test_configuration.py` with
   `STRIPE_E2E_ENABLED=true`.
10. Do not record full Price, Customer, Subscription, webhook, checkout, portal,
    or payment method values in versioned files.

## Stripe CLI On Windows

If `stripe version` is not recognized, install the Stripe CLI from an official
distribution channel before attempting local webhook forwarding.

Official options include:

- Scoop:
  `scoop bucket add stripe https://github.com/stripe/scoop-stripe-cli.git`
  then `scoop install stripe`.
- npm with Node.js 18 or newer: `npm install -g @stripe/cli`.

Do not download executables from unofficial mirrors, do not store the CLI binary
inside this repository, and do not commit the Stripe CLI config directory.

## Second Subscription Risk

An Organization must not accidentally create multiple active Stripe
Subscriptions. In V1, an existing active, trialing, or past-due Stripe
Subscription should be managed through the Customer Portal instead of a new
Checkout Session.

If Test Mode shows two active commercial subscriptions for one Organization:

1. Stop staging promotion.
2. Record the issue as Critical.
3. Preserve local and Stripe evidence with masked IDs only.
4. Do not manually edit `Organization.plan_key` as the primary fix.
5. Cancel or clean up Test Mode resources only after recording the failure.

## Success Page Without Webhook

The checkout success page is not an access grant. If the user lands on success
but local access is not updated:

1. Confirm the signed webhook was received.
2. Inspect `BillingEvent`.
3. Confirm the Stripe Subscription event has a known Price ID.
4. Use reconciliation only after confirming the remote Subscription snapshot.

## Out-Of-Order Events

Older Stripe events must be ignored when a newer event has already updated the
local Subscription. The expected local result is:

- `BillingEvent.status=ignored`;
- safe reason code `STRIPE_EVENT_OUT_OF_ORDER`;
- no regression of status, plan, period dates, or access decision.

If this can only be reproduced with automated boundary tests and not with the
Stripe CLI, mark the E2E row as automated evidence rather than real CLI
evidence.
