# Communications — calls, SMS, email and phone reminders (Enterprise + admins)

## What is implemented

| Channel | Provider | Access | Notes |
|---|---|---|---|
| Telephone call that reads your message | Twilio Calls API | Enterprise or admin | Automated message read aloud; no impersonation, no two-way AI dialogue, no call recording |
| SMS to one verified consenting recipient | Twilio Messages API | Enterprise or admin | Includes a `Reply STOP to opt out.` line; inbound STOP is honoured |
| Email to one verified consenting recipient | SendGrid | Enterprise or admin | Sent from the **operator's** verified sender, not the user's Gmail address |
| Phone reminder / wake-up alarm | Twilio Calls API + Verify | Enterprise or admin | Owner-verified number, previewed time, explicit confirmation |

Access is decided server-side from the **verified signed-in identity**: `is_admin(email)` or a durable **Enterprise** entitlement (`check_tier(email) == "enterprise"`). Client-supplied email addresses, `admin: true` flags and unrelated tier tokens are ignored. When Supabase is configured, the cloud `subscribers` row is authoritative; a storage outage fails closed and a stale local payment record cannot unlock paid sending. Blocked and suspended accounts are always excluded.

## End-user flow (e.g. “OraCool call 07052405515”)

1. Say or type the command. OraCool collects the **number first**, then asks: *“What should I say on the call?”*
2. **Calls are never placed from a language model.** The number and the message are parsed locally, so a model cannot place a call, invent a recipient or relay a hidden instruction.
3. Answer with the exact text to read.
4. OraCool shows a **review card** with the full recipient (`+234…`, not just a mask), the exact text that will be read, the provider status and a two-minute expiry.
5. You confirm with the button or by saying **“confirm call”**. Only then is the request sent. Say “cancel” to abandon it.
6. The result reports provider acceptance — **not** proof that the phone rang, was answered or was heard.

SMS and email work the same way (`OraCool text 0705…`, `OraCool email name@example.com`). Email additionally needs a subject.

The **Communications** button in the voice toolbar opens a side drawer with per-channel setup status, recipient verification, the review card and recent activity. On phones it opens full-width.

## Recipient verification — mandatory

Nobody can be contacted until they are verified **for that destination**:

- **Phone:** Twilio Verify sends an SMS code. Enter the code in the drawer — never in chat. Verified for 30 days.
- **Email:** the replacement for proof of ownership is a code emailed to that address, compared against a salted hash held server-side; the plaintext code is never stored.
- Consent is required from the person who prepared the message. Do not use this to contact someone who has not agreed.

Encrypted at rest (Fernet, `ENCRYPTION_KEY`): destinations, message bodies and codes. Only masks are returned to the interface. Do not rotate `ENCRYPTION_KEY` casually — saved recipients become unreadable.

## Limits and safeguards

- Single recipient per request. No bulk sending, lists, campaigns, cold outreach or marketing blasts.
- 10 sends per account per day, 5 per recipient per day, 100 platform-wide per day; 3 verification requests per account/hour, 5 per IP/hour, 30 platform-wide/day; 20 verified recipients per account.
- Preview expires after 2 minutes. A second confirmation for the same request never sends twice — the draft status is checked and a unique `comms_<id>` reservation is claimed in Supabase before the provider is called.
- Ambiguous network/provider outcomes are recorded as `unknown` and are **never retried automatically**; check the provider console first.
- Signed Twilio status callbacks are verified for exact URL, signature, AccountSid and CallSid/MessageSid, and status is applied in order. A forged callback changes nothing.
- Signed inbound STOP/START webhook suppression is implemented; the operator must point the number's messaging webhook at it (below).
- Provider acceptance is not delivery. Carriers, Do Not Disturb, voicemail and country rules can prevent a heard call or message.

## Operator setup

### 1. Twilio (calls, SMS, reminders)

1. Own a voice+SMS capable number in the **same account/subaccount** as `TWILIO_ACCOUNT_SID`. The read-only inventory check of the supplied credentials found **no incoming numbers, no verified caller IDs and no Verify service**, so this still has to be created/purchased.
2. Create a **Verify Service** (SMS) → set `TWILIO_VERIFY_SERVICE_SID`.
3. Set `TWILIO_FROM_NUMBER` to that owned number and `PUBLIC_BASE_URL` to the exact public origin (e.g. `https://oracool-ai.onrender.com`, no trailing path).
4. Numbers that will be messaged/verified may need to be verified or allowed for the destination country, especially on trial accounts. Fund the account for calls, SMS segments and Verify.
5. Messaging webhook on the number: `POST https://YOUR-ORIGIN/api/comms/inbound` (STOP/START handling). Call status callbacks are attached automatically per request.
6. Set `COMMUNICATIONS_ENABLED=true` only after the above.

### 2. SendGrid (email)

1. Create a SendGrid account, verify a sender/domain and create an API key.
2. Set `SENDGRID_API_KEY` and `SENDGRID_FROM_EMAIL` (must be the verified sender).
3. Set `SENDGRID_ENABLED=true`. Twilio credentials do **not** enable email; SendGrid is a separate provider (or substitute your own transactional email provider in `communications.py`).
4. Keep this transactional-only: no bulk marketing, and honour unsubscribe requests.

### 3. Reminders (see `PHONE-CALLS-SETUP.md`)

`REMINDER_CALLS_ENABLED=true` and `REMINDERS_ALWAYS_ON=true` remain separate opt-ins for alarm calls, and only make sense on always-on hosting.

### 4. Security

- Rotate the Twilio Auth Token that was shared in chat, then update Render and the private workspace files.
- Never commit or paste `keys.json`, `RENDER_ENV.txt` or `RENDER-ENV-UPDATED.txt`; the repository is public.
- One application/scheduler replica. The state store is a durable snapshot, not a distributed queue.

## Testing performed (all provider IO mocked)

32 focused unit tests in `tests/test_communications.py` plus route-level tests in `tests/test_patch9.py` cover: Nigerian local-number conversion, invalid recipient/channel rejection, denial for free/pro/blocked accounts and forged tier tokens, Enterprise access using the verified identity, preview-without-setup, confirmation required, exact read-back of the message with XML escaping, ignoring client-supplied `to`/`message`, duplicate-confirmation safety, expired previews, cross-account draft denial, entitlement revocation, removed/changed recipients, disabled channels, claim/storage failures, unknown outcomes across restart, rate limits, encrypted storage, phone/email verification, signed callbacks with wrong signature or wrong CallSid, and STOP suppression.

`tests/browser_patch11_check.py` runs a real Chromium session against the mocks to verify the console layout, the number→message→review→single-send flow, email readiness messaging, foreign-token denial and the mobile drawer.

**No real call, SMS or email has been sent, and no Twilio number, Verify service or Chargebee/SendGrid sender has been created.**

## Twilio API key (optional, recommended)

You can authenticate outbound calls, SMS and Verify requests with a Twilio **API key** instead of the account Auth Token:

| Setting | Value |
|---|---|
| `TWILIO_API_KEY_SID` | the key SID, starts with `SK…` |
| `TWILIO_API_KEY_SECRET` | the secret shown once when the key is created |

- Create it in Twilio Console → Account → **API keys & tokens** → *Create API key*. Choose **Standard** (a *Restricted* key must be granted Messages, Voice and Verify permissions or Twilio answers `401 actor doesn't have any assertions`). Click **Finish** — the key is not usable until the wizard completes.
- When both are set, OraCool tries the API key first. If Twilio rejects it with 401 (unfinished, restricted, or revoked) the request automatically falls back to `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN`, so an unfinished key never takes calling down.
- `TWILIO_AUTH_TOKEN` stays required: Twilio signs status callbacks and inbound STOP/START webhooks with the Auth Token, and OraCool rejects unsigned callbacks. If you rotate the Auth Token, update the row in Render.
- The **Admin console → Diagnostics → Twilio readiness** card runs read-only checks (which credential works, account type, owned numbers, verified caller IDs, whether `TWILIO_FROM_NUMBER` is owned, Verify service) and lists the exact blockers. It never sends anything.
