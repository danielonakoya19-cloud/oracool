# Communications — confirmed email (Enterprise + admins)

Telephone calling and SMS were removed from OraCool permanently (Patch 20). **Email is the only outbound channel.**

## What is implemented

| Channel | Provider | Access | Notes |
|---|---|---|---|
| Email to one verified consenting recipient | SendGrid | Enterprise or admin | Sent from the **operator's** verified sender, not the user's Gmail address |

Access is decided server-side from the **verified signed-in identity**: `is_admin(email)` or a durable **Enterprise** entitlement. Client-supplied email addresses, `admin: true` flags and unrelated tier tokens are ignored. When Supabase is configured, the cloud `subscribers` row is authoritative; a storage outage fails closed. Blocked and suspended accounts are always excluded.

## End-user flow (e.g. "OraCool email name@example.com")

1. Say or type the command. OraCool collects the **address**, then asks for the exact message.
2. **Email is never sent from a language model.** The address and the message are parsed locally, so a model cannot send, invent a recipient or relay a hidden instruction.
3. OraCool shows a **review card** with the full recipient address, the exact subject and text, the provider status and a two-minute expiry.
4. You confirm with the button or by saying **“confirm send”**. Only then is the request sent. Say “cancel” to abandon it.
5. The result reports provider acceptance — **not** proof the email arrived.

The **Communications** button in the voice toolbar opens a side drawer with setup status, recipient verification, the review card and recent activity. On phones it opens full-width.

## Recipient verification — mandatory

Nobody can be contacted until they are verified **for that destination**: the recipient receives a one-time code **at the address itself**, compared against a salted hash held server-side; the plaintext code is never stored. Verified for 30 days. Consent is required from the person who prepared the message.

Encrypted at rest (Fernet, `ENCRYPTION_KEY`): destinations, message bodies and codes. Only masks are returned to the interface. Do not rotate `ENCRYPTION_KEY` casually — saved recipients become unreadable.

## Limits and safeguards

- Single recipient per request. No bulk sending, lists, campaigns, cold outreach or marketing blasts.
- 10 sends per account per day, 5 per recipient per day, 100 platform-wide per day; 3 verification requests per account/hour, 5 per IP/hour, 30 platform-wide/day; 20 verified recipients per account.
- Preview expires after 2 minutes. A second confirmation for the same request never sends twice — the draft status is checked and a unique `comms_<id>` reservation is claimed in Supabase before the provider is called.
- Ambiguous network/provider outcomes are recorded as `unknown` and are **never retried automatically**; check the provider console first.

## Operator setup (email)

1. SendGrid: create an API key (Mail Send scope) and set `SENDGRID_API_KEY` in Render → Environment.
2. `SENDGRID_FROM_EMAIL` — a verified sender address in that SendGrid account.
3. `SENDGRID_ENABLED=true` and `COMMUNICATIONS_ENABLED=true`.

Nothing else is required: no Twilio, no phone numbers, no webhook configuration.
