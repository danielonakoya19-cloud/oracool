# 💳 Paystack Go-Live Checklist — OraCool AI

Use this when you switch from **test mode** to **collecting real money**.
Do it in order. ~20 minutes total.

---

## 1. Get your Paystack account ready (paystack.com)

- [ ] Sign up at **paystack.com** with your **Nigerian** details (you collect in
      NGN by default, USD display — already wired in the app).
- [ ] **Verify your business** (Dashboard → Settings → Your Business):
      bank account, BVN, business name/registration. Paystack pays out to this
      bank account — it must be correct.
- [ ] Go to **Settings → API Keys & Webhooks**.
      You'll see two pairs of keys:
      - **Test**: `pk_test_…` (public) and `sk_test_…` (secret)
      - **Live**: `pk_live_…` (public) and `sk_live_…` (secret)

---

## 2. Switch the app from TEST to LIVE

The app reads these settings (from `keys.json` locally, or Render env vars in
production — env vars win):

| Variable | Test mode (now) | Live mode (go-live) |
|---|---|---|
| `PAYSTACK_TEST` | `true` | **`false`** |
| `PAYSTACK_SECRET_KEY` | `sk_test_…` | **`sk_live_…`** |
| `PAYSTACK_PUBLIC_KEY` | `pk_test_…` | **`pk_live_…`** |
| `PAYSTACK_TEST_SECRET` / `PAYSTACK_TEST_PUBLIC` | (leave, unused in live) | (unused) |

**On Render:** edit the Environment variables, set the three above, Save →
it redeploys automatically.
**Locally:** edit `keys.json`, set `"PAYSTACK_TEST": false`, paste the live
keys, restart.

> ⚠️ Never use `sk_test_` with `PAYSTACK_TEST=false` (or vice-versa) — the
> server picks the secret key based on the `PAYSTACK_TEST` flag. Keep them in
> sync, or payments will fail.

---

## 3. Point the webhook at your app (so sales are recorded even if the
   buyer closes the tab after paying)

The app now has a webhook endpoint:

```
https://<your-domain>/api/paystack/webhook
```

- [ ] Paystack → **Settings → API Keys & Webhooks → Webhook URL** →
      paste `https://oracool.onrender.com/api/paystack/webhook`
- [ ] The app verifies every webhook with **HMAC-SHA512** (your secret key),
      so only genuine Paystack events are recorded.
- [ ] Send a **test webhook** from the Paystack dashboard and check it shows
      `200 OK` (or `{"status": "ok"}`).

> Why this matters: the app also records the sale when the buyer **returns to
> your site** after paying. The webhook covers the case where they **don't**
> return — without it you could receive money but the user never gets PRO.
> Set the webhook; don't skip it.

---

## 4. Do a REAL test payment before announcing

- [ ] With live keys + webhook set, open your app → Upgrade → pay with a
      small real amount on your own card.
- [ ] Confirm in the Paystack dashboard that the money arrived.
- [ ] Confirm the app turned your account **PRO**.
- [ ] Confirm the Admin console shows the user as **PRO**.
- [ ] (Then refund yourself if you want — Paystack → Transactions → Refund.)

---

## 5. Pricing sanity check (optional)

Your plans live in `server.py` (`PLANS` dict) **and** are sent to Paystack at
checkout. Make sure they match what you advertise:

| Plan | USD | NGN (default charge) |
|---|---|---|
| Starter | $20 | ₦20,000 |
| PRO | $50 | ₦50,000 |
| Ultra | $100 | ₦100,000 |
| Enterprise | $500 | ₦500,000 |

To change a price, edit `PLANS` in `server.py` and redeploy.

---

## 6. After you're earning — housekeeping

- [ ] Rotate `PAYSTACK_SECRET_KEY` if it was ever pasted in chat or a
      screenshot.
- [ ] Never commit `keys.json` / `RENDER_ENV.txt` to GitHub.
- [ ] Check payouts land in your Nigerian bank account (Paystack pays out on
      its own schedule; first payout is usually after ~1 business day once
      activated).

---

## Where the payment flow lives (for reference)

| Piece | File / route |
|---|---|
| Checkout init | `POST /api/paystack/initialize` → returns `authorization_url` |
| Verify on return | `POST /api/paystack/verify?reference=…` (frontend auto-calls) |
| Webhook (no return needed) | `POST /api/paystack/webhook` (HMAC-verified) |
| Subscriber storage | `data/subscribers.json` + Supabase `subscriptions` table |
| PRO token issued | JWT (`make_tier_token`) — 90-day expiry, follows the email |
