# 🚀 OraCool AI — Fresh Deploy on Render (step-by-step)

Since the old Render service was deleted, create a new one. Total time: ~5 minutes.
Everything is already built and pushed to GitHub — you only click and paste.

---

## STEP 1 — Create the service on Render

1. Go to **https://dashboard.render.com** → sign in
2. Click **New +** → **Web Service**
3. Connect GitHub → pick the repo **`danielonakoya19-cloud/oracool`**
   (if GitHub isn't connected yet, Render will ask you to authorize it — allow it)
4. Render auto-detects **Python**. Fill in:
   - **Name:** `oracool-ai` (this makes your URL `oracool-ai.onrender.com` — or pick `oracool`)
   - **Runtime:** Python
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python3 server.py`
   - **Plan:** Free
5. Click **Create Web Service** (don't worry about env vars yet — paste them next)
   - **For professional use** (paid clients, court docs): upgrade the instance to Render's
     **Starter ($7/mo)** once revenue starts — no sleep, stable URL, credibility. The free
     tier is fine for beta testing.

---

## STEP 2 — Paste the API keys (the critical step)

1. Open your new service → **Environment** tab
2. Scroll to **Environment Variables** → click **"Add from .env"**
3. Select & copy **all current lines** from the file **`RENDER_ENV.txt`** (this workspace)
4. Paste into the box → **Save Changes**
5. Render restarts automatically (~1 min)

> Keep this file private. Never commit it or paste its contents into a public chat.

---

## STEP 3 — Supabase SQL + email verification wiring (one time)

1. **supabase.com/dashboard** → your project (`eyiawcqkdtoyvlssbfmg`)
2. Left sidebar → **SQL Editor** → **New query**
3. Paste the contents of **`SUPABASE-SETUP.sql`** (this workspace) → **Run**
   (it creates `subscribers` and `user_flags` tables — safe, `if not exists`)
4. **Password-only signup (Patch 9):** the backend creates ordinary users with the confirmation gate disabled, then signs them in. No email code is sent. Set the Supabase Auth Site URL/Redirect URLs to your Render domain for OAuth and password recovery.
5. In Supabase **Authentication → Sign In / Providers → Email**, keep email/password sign-in enabled. Turn **Confirm email** OFF if you also use Supabase's public signup endpoint elsewhere.
6. Existing unconfirmed accounts may still need an operator to confirm the existing user in Supabase. Do not recreate accounts or change existing passwords. Public signup cannot claim the reserved admin addresses; existing admins sign in normally.
7. **Optional — branded tracking links:** buy/point any domain (e.g. `go.yourname.com` CNAME
   to your Render service, add it as a custom domain in Render), then set env var
   `TRACKER_DOMAIN=go.yourname.com`. Tracking links become `go.yourname.com/waptrick.com`.
   Even without that, links are now clean (`…/t/waptrick.com`) and one-click
   shortenable via the "✨ Get short link" button (free os8.me shortener).

---

## STEP 3.7 — Google / GitHub sign-in (the two-minute enable)

The "Unsupported provider" error means the providers are simply **OFF** in Supabase —
the app now auto-detects this and says so instead of breaking. Enable them:

**Google**
1. console.cloud.google.com → **APIs & Services** → enable *Google Identity API* →
   **Credentials** → **Create Credentials → OAuth client ID → Web application**
2. Authorized redirect URI: `https://eyiawcqkdtoyvlssbfmg.supabase.co/auth/callback`
3. Copy **Client ID** + **Client secret**
4. Supabase dashboard → **Authentication → Sign In / Providers → Google → Enable** →
   paste both → Save
5. Supabase → **Authentication → URL Configuration → Redirect URLs** → add your app URL
   (`https://<your-render-url>/**`)

**GitHub**
1. github.com → Settings → **Developer settings → OAuth Apps → New OAuth App**
   - Homepage URL = your app URL · Authorization callback URL =
     `https://eyiawcqkdtoyvlssbfmg.supabase.co/auth/callback`
2. Generate a **Client secret**, copy both
3. Supabase → Authentication → Sign In / Providers → **GitHub → Enable** → paste → Save

(OraCool checks `/auth/v1/settings` every 5 min — the buttons light up automatically.)

## STEP 4 — Verify everything (includes the email-CODE check)

**Confirm the verification CODE really arrives:**
1. Sign up with a throwaway Gmail (not an admin email).
2. The email from Supabase must contain the numeric code. If it only shows a link,
   add this line inside the **Confirm signup** template body
   (Supabase → Authentication → Email Templates → Confirm signup → edit HTML):
   `Your OraCool activation code is: <b style="font-size:24px">{{ .Token }}</b>`
3. In the app's verify panel, enter the code → account activates instantly
   (endpoint `/api/auth/code/verify`). The link-paste box remains as backup.
4. Admin accounts (`ADMIN_EMAILS`) skip verification by design — never lock yourselves out.

1. Open **`https://oracool-ai.onrender.com/api/health`** → should say `online`
2. Open **`https://oracool-ai.onrender.com/api/config`** → `"keys"` should be all `true`;
   `image_ready`, `video_ready`, `nasa_ready`, `oauth_providers` all reported
3. Open **`/privacy`** → the no-retention policy page must render (link is in the signup gate)
3. Open the app → **Sign up** with `danielonakoya19@gmail.com` + your own password
   → badge shows **ENTERPRISE**, Admin tab appears with the user base
4. In chat type: **"create an image of a golden eagle at sunrise"**
5. In chat type: **"what's the weather in London"** and **"is 8.8.8.8 on Shodan"**

All good? Done. 🎉

---

## What the AI can do straight from chat (no clicking)

| Say this… | The AI runs… |
|---|---|
| weather in Lagos | weather tool (open-meteo → wttr.in fallback) |
| bitcoin price / AAPL stock | market tools |
| who owns example.com | domain WHOIS/DNS |
| is this IP on Shodan 8.8.8.8 | Shodan (PRO+) |
| check username @someone | username OSINT across ~50 platforms |
| open instagram / open settings | app launcher — intent on Android, scheme on iOS, web fallback |
| trace wallet bc1q… | BTC/ETH on-chain state (blockstream/blockscout/blockchair) |
| extract entities from this text | emails · IPs · wallets · PGP · handles · .onion |
| save this to my drive | Files/Drive agent — writes into the linked Drive folder |
| trace +234 803 123 4567 | phone intel |
| search the web for… | Tavily search (STARTER+) |
| virusTotal google.com | VirusTotal (PRO+) |
| check dark web for someone@gmail.com | dark-web intel: LeakCheck breach DBs + OnionLand hidden-service index (PRO+) |
| create an image of… | AI image generation (PRO+) |
| create a video of… | AI video generation (ULTRA) |
| search github for… | GitHub console (ULTRA) |
| turn on the living room lights | Home Assistant (after linking in Devices tab) |

Tier ladder: FREE ⊂ STARTER ⊂ PRO ⊂ ULTRA ⊂ ENTERPRISE (each inherits the ones below).
The AI honestly tells a user which plan unlocks a feature they ask for.

---

## Image & video generation status (as of this build)

Cascade (images): **NexaAPI → HiAPI → TokenMix → Pollinations FLUX → CVRON flux**.
Cascade (video): **HiAPI → CVRON free WAN-22** (auto frame-generation + animation).

- **Images always work** — free HD engines are the guaranteed floor. Today NexaAPI
  (`INSUFFICIENT_BALANCE`), HiAPI (`402`) and TokenMix (promo-credit restriction) are out
  of funds, so images render via the free engines with an honest note naming the fix.
- **Video always works too** — verified live: CVRON's free pipeline returned a real
  `video/mp4` clip (~1–3 min, retries built in). Top up **hiapi.ai** or **nexawapi.com**
  for HD/longer premium video; it activates instantly, no redeploy.
- **Pixazo**: `reve-image` retired by the provider (HTTP 410, 2026-08-15) — removed.
- **OpenAI**: key valid, 0 credits — only affects premium chat fallback (Groq is the
  primary brain and works).
- **No free PRO trial exists any more** — the 24h trial endpoint/button are removed and
  old trial tokens are cryptographically rejected.
- **Signups are code-verified**: Supabase emails a numeric code; the app (and the AI)
  verifies it before entry — see Step 3 note about the email template tweak.


---
## What's new in THIS build (2026-09-17 night)

- **Dark web UNLOCKED on every plan** — passive public indexes only (LeakCheck + OnionLand + Ahmia),
  now with a `quick_facts` summary + clearnet media previews for direct answers in chat.
- **Kairos key wired in** (`KAIROS_API_KEY`) — the doc-verify board now shows provider *kairos* as
  configured; results stay "indicators requiring further review", never verdicts.
- **Crypto payments (ATLOS gateway)** — `ATLOS_MERCHANT_ID` + `ATLOS_API_SECRET` env; hosted invoice
  link + auto-unlock on confirmation (postback + 15s polling fallback). AI can start it from chat
  ("pay with crypto"). NOTE: live test returned `Merchant id 'UCU7A0LYKD' doesn't exist` from
  api.atlos.io — double-check the Merchant ID on atlos.io (or activate the merchant). The app surfaces
  that error verbatim; nothing is faked.
- **Images & videos render INLINE in the chat** (no more plain links) — media frame rides the stream,
  history re-renders them on reload.
- **Talking video**: asking for a video "with sound/voice" auto-selects the audio model (Veo 3.1 via
  HiAPI) — needs HiAPI credits; the free engine honestly labels its clips silent.
- **You can upload images/videos/files to the AI** — images get forensic triage (SHA-256/EXIF/GPS/editor;
  indicators only) + vision description when a vision key is available; videos get hashed and preserved.
- **Long answers no longer stop mid-sentence** — the stream auto-continues up to 3 segments;
  `CHAT_MAX_TOKENS=3000` (was 900).
- **Admin board via AI**: with an admin login, say "block user x@y.com", "unblock …", "grant pro to
  x@y.com for 60 days", "revoke plan of …", "delete user x@y.com" (permanent), "list users", "revenue"
  — all execute server-side instantly. Admin UI also gained the 🗑 delete button.
- **Skills = user self-upgrade**: "add a skill called <name>: <instructions>" installs a personal
  feature the AI must obey in every future chat ("list my skills", "remove skill X").
- **App launcher honesty**: "open youtube" now returns a one-tap **Open** card (phones only allow app
  launches from a real tap — popup-blocked auto-launches were why nothing opened before).
- **Chat history lock**: 🔒 button (instant lock) + "Lock every time the app opens" setting
  (password/Face-ID to unlock).
- **Link tracker**: your own-domain link IS the short link now (`/t/<your-word>` supported via the
  custom-ending box; AI: "create a tracking link for https://x called gift"). os8.me is demoted to an
  optional extra and never replaces your link.
- **Trading desk moves**: auto-refresh every 10s while open (live repricing), the equity curve now
  includes the live mark so it visibly trends.
- **News**: "news <topic>" — live sourced headlines on every plan.


---
## Pre-deploy patch (2026-09-18): Agnes brain, crypto LIVE, admin live DB

- **AGNES_API_KEY wired as primary brain + media engine** (free OpenAI-compatible gateway,
  `apihub.agnes-ai.com/v1`, model `agnes-2.5-flash`, 512K ctx). Chat verified answering through it.
  Images now generate through **Agnes FIRST** (`agnes-image-2.1-flash` — produced live PNGs in tests);
  videos try **Agnes t2v** first (free tier is rate-limited — the cascade falls through to HiAPI →
  CVRON silently-honest chain, never fakes audio). Groq/OpenAI remain automatic fallbacks.
- **ATLOS crypto is LIVE end-to-end**: merchant ID corrected to **`UCU7AOLYKD`** (letter L — the earlier
  `0` digit version was rejected by ATLOS). Test invoice created: `https://atlos.io/payment/…` with
  status-polling active. Postback URL auto-derived from the serving host.
- **Kairos passthrough**: doc-verify now ACTUALLY calls api.kairos.com with App ID `2ff711fe` + API key
  and reports the provider's answer verbatim (currently HTTP 403 — the dashboard key belongs to the
  Kairos IDV QR-pairing flow; server REST access needs the paid/contract credentials). No fake verdicts.
- **Admin AI = real-time backend access**: `inspect user <email>` / `user table` / `database stats` now
  stream a merged live record: Supabase auth profile (id/created/last sign-in/verified/ban), effective
  tier, payments, cases + evidence counts, trackers + visits, skills, paper-trading — plus the same
  block/unblock/grant/revoke/delete commands. Route `/api/admin/record` (admin-token only).
- **generate-random.org linked to the AI** ("generate anything"): live JSON API for passwords/uuids/
  tokens/numbers/hashes/phones/emails/addresses/iban/colors/… + 200-generator catalog search.
  Chat: "generate a random password/uuid/…" works; endpoint `/api/generate/random`.
- **RENDER_ENV.txt = 49 lines** (adds AGNES_API_KEY, AGNES_MODEL, KAIROS_APP_ID; fixes ATLOS id +
  BRAIN_PROVIDER).

## Patch 6 (2026-09-18) — honest numbers, IP telemetry, password RESET (never reveal), findable Admin tab
- **The AI can no longer invent numbers.** Every admin chat now carries a "LIVE ADMIN BOARD SNAPSHOT" (exact user/revenue/subscriber figures the server just pulled). Rule: quote it verbatim — ₦0 means "no payments yet", never "we earned X". Live-checked against Agnes: asked "how many users and what is the revenue" → replied "**Users — 9 total** [Source: admin board]" — matched the board exactly.
- **"how much did we earn", "who just signed up", "show IPs" etc. now fire the board tools even without the word "admin".**
- **Real-time IP + activity:** every chat/login touches `last_ip`/`last_seen` (X-Forwarded-For aware). `inspect user <email>` shows last IP, city/region (MaxMind), Supabase `last_sign_in_ip`, and last password reset — for YOUR OWN platform's connection logs, which is what a service operator is entitled to.
- **Passwords: never readable, now resettable.** Signups are bcrypt-hashed — no one, not even you or me, can "see" them (and a feature that could would be a crime, so it will never exist). What admins CAN do: `reset password of x@y.com` (fresh strong temp password) or `... to MyNewPw123!`, plus the `/api/admin/reset-password` button endpoint. Every reset writes to the audit log; old password dies instantly (verified: new accepted / old rejected).
- **Admin tab is self-finding:** if your session predates the admin flag, the app silently re-verifies with the server 1.4s after load and reveals the Admin tab itself.
- Test leftovers purged: board is now only real accounts; **total revenue ₦0 — that is the truth until first payments.**

## Patch 7 (2026-09-18) — 24/7 CONNECTORS, ALERTS & API GATEWAY
* New tab sections replace the old Drive-folder widget: **Devices → Connectors & Alerts**.
* **Event engine (server background thread, 15 s tick):** login / payment / security / watchlist / tracker / gateway / daily-digest events per account, persisted outbox with 3× retries — missed sends catch up after Render sleep.
* **Outbound connectors:** Telegram bot, WhatsApp (Meta Cloud API), Discord webhook, Slack incoming webhook, ANY custom webhook (HMAC-SHA256 `X-OraCool-Signature`), email (needs RESEND_API_KEY on host). All tokens **AES-256 sealed at rest** (same vault cipher); the UI and chat only ever show masked values.
* **Inbound API gateway:** every user gets a personal hook URL `GET /hook/<token>?source=&text=` (Zaper/IFTTT/uptime robots just GET it) and an API key for `POST /api/gateway/v1/ingest` (`X-OraCool-Key`) + `GET-style /api/gateway/v1/events`. 120 events/min per key. Key shown ONCE, rotatable.
* **Browser push:** VAPID keys auto-generate on first boot if `pywebpush` + `cryptography` are installed (they are in requirements.txt now); sw.js handles push + click-to-focus. Without them the in-app alert inbox still notifies while the PWA is open — connectors carry everything when closed.
* **Daily digest:** per-account UTC hour, includes live board figures for admins.
* **Skill marketplace:** `share skill <name>` publishes (author masked `d***@gmail.com`), `browse community skills`, `install skill <name>` copies into your account — no server code touched.
* **Chat control:** "connect my telegram", "send me a test alert", "turn off login alerts", "rotate my api key", "browse community skills", "install skill x" — all run server-side instantly.
* **Login telemetry on every login path** (password + 2FA) records last_ip; login event carries IP + city.
* Bluetooth radar now states the truth when the browser sandbox blocks the adapter (e.g. embedded previews) instead of pretending.
* Storage: `data/alerts.json` + outbox, mirrored to Supabase `case_store` row `k='alerts'` (no new SQL needed). Secrets only ever on server disk, never in the repo or zip.

## Patch 8 (2026-09-18) — chat UX, media library, mail watch, admin logs, honest coverage
* **No more leaked tool-call markup.** Some models occasionally print their function-call syntax
  (`<tool_call><arg_key>…`) as visible text. The server now (a) intercepts that markup mid-stream so it
  can never reach a user, (b) *executes* the call it contained (market/weather/OSINT/gateway tools),
  (c) asks the model for a clean prose answer with those results, and (d) strips any residue from
  non-streamed replies, saved history and the client renderer. Prompt-level rule added as well.
* **Chat sidebar (☰ Chats)** — every conversation is stored server-side per account
  (`data/conversations.json`, mirrored to Supabase `case_store` k=`conversations`), with a
  **+ New chat** button, per-chat titles from the first message, previews, timestamps and delete.
  Switching chats reloads the full transcript; history follows the account across devices.
* **Jump-to-latest arrow** — appears bottom-right of the chat when you scroll up into old messages;
  tap to fly back to the newest message.
* **Creations gallery (Create tab → 🧰 My creations)** — every image/video generated in chat or the
  studio is downloaded to `data/media/<user>/` and indexed, so it survives provider link expiry.
  Served from `/media/...` (`/api/media/list`, `/api/media/delete`).
* **📧 Mail watch connector (IMAP, read-only)** — users connect their own mailbox with an app password
  (sealed at rest like all connector secrets). OraCool reports unread counts, senders and subjects
  ("check my email"), fires **new-mail alerts** to their channels every 5 minutes, and never reads
  message bodies. Gmail/Outlook/Zoho need an app password — the error message says so plainly.
* **Crypto checkout now shows the address.** The modal renders the receiving wallet
  (`CRYPTO_WALLET_EVM`, default your USDT/USDC/ETH address), a scannable QR (SVG, generated locally),
  tap-to-copy, "I have paid — check now" with a keyless on-chain peek (ethplorer), live status text,
  plus the ATLOS hosted page (exact coin amount, auto-unlock) and its URL in plain text for browsers
  that block pop-ups.
* **Enterprise = $500/month with every feature unlocked** (₦750,000) — no longer "custom quote".
  Plans tab also gains a 🔐 **Access map** and Cases/Trading/Code tabs are hard-locked with an upgrade
  panel for tiers below them (strict ladder, nothing above your plan opens).
* **Admin AI: raw platform logs + diagnostics.** Server stdout is mirrored into `data/platform.log`;
  admin accounts can ask "show me the platform logs", "run diagnostics", "audit trail", "list payments",
  "who is online" — and the Admin tab now renders both the raw log tail and the diagnostics block
  (`/api/admin/logs`, `/api/admin/diagnostics`).
* **Dead paid-key features removed from the UI**: LeakCheck (invalid key) is gone; the HIBP section no
  longer shows a dead end when no key exists (free infostealer/leak indexes remain). Shodan,
  VirusTotal, AbuseIPDB and URLScan are verified live with the server keys and stay.
* **Honest market coverage**: NGX/African tickers (DANGCEM, GTCO, MTNN…) now resolve and explicitly say
  the configured feed (Finnhub) does not cover the Nigerian Exchange instead of showing a fake 0.00.
* `qrcode` added to requirements (pure-python SVG QR, no Pillow). Email is still NOT a free relay —
  outbound email needs RESEND_API_KEY; **inbound mail watch works without any server key.**


## Escaped tool-markup follow-up

- Handles HTML-escaped and double-escaped tool blocks as well as literal tags.
- Streaming guard retains split closing tags and preserves prose spacing.
- Only a named `command` is recovered; provider metadata/user IDs are not commands.
- Offline regression suite: `python tests/test_markup.py`.
- Enterprise remains $500 per 30-day billing period; Professional is the internal `ultra` tier.

This follow-up must be deployed before the live site changes. The downloadable ZIP excludes secrets.


## Patch 9 — password-only signup, durable chat restore, working video API

See `PATCH9-DEPLOY.md` for the exact deployment and private-connection steps.
Health build marker: `patch9-password-history-config`.
Build command MUST install requirements for encryption, push and QR codes.
Do not import environment variables a second time: edit existing rows and add only missing names.

**Live Patch 9 check:** `public.case_store` is missing in the configured Supabase project. Run `CHAT-STORAGE-SETUP.sql` in its SQL Editor before relying on durable chat history.

For the complete current schema, run **SUPABASE-COMPLETE-SETUP.sql** (supersedes the earlier partial scripts). Then deploy the matching latest backend persistence fixes. Admin security checks remain enabled.


## Patch 10 — hands-free voice and real phone alarms

Read `PHONE-CALLS-SETUP.md`. Your completed SQL setup is sufficient; no new SQL is needed.
Health marker: `patch10-voice-phone-alarms`. Real phone calls are disabled until Twilio, verified recipients, provider credit and always-on hosting are configured. Keep exactly one active scheduler replica.
Browser voice requires an explicit Start action and microphone permission. It does not keep listening while the phone is locked/the app is hidden.


## Admin-only telephone alarms follow-up

Current build: `patch10-admin-only-phone-alarms`. Twilio phone verification, calling and reminders are admin-only, enforced by authenticated server identity, service checks and the scheduler. Normal voice conversation stays available to other users. Supplied Twilio credentials are private, not included in this repository/ZIP. Read `PHONE-CALLS-SETUP.md`: the trial account authenticated, but the supplied caller number could not be found in that account and no Verify service was configured. No SMS/call was sent. Delivery remains disabled until setup is completed.


## Patch 11 (2026-09-20) — public landing page, roomier console, Enterprise communications

- **Routes changed:** `/` now serves the standalone marketing/landing page (`landing.html`) and the app console moved to **`/app`**. `/index.html` still opens the console for older bookmarks and the manifest/service worker now start at `/app`. Payment returns, OAuth returns and password links are forwarded to the correct page, so existing links keep working.
- **Privacy policy** is now a plain page (`/privacy`); it was simplified because the older text mixed browser-storage facts with non-public security claims.
- **Roomier layout:** chat bubbles, spacing and side panel were widened; the detailed voice controls moved out of the chat into a modal drawer (`Voice settings`), which the browser checks confirm no longer reduces the chat height.
- **Communications drawer:** Enterprise/admin users get Call / SMS / Email with recipient verification, an exact-content review card and a single confirmed send. Ordinary users see an upgrade card. Entering the Twilio number is no longer required for anyone to use voice chat.
- **Access rule:** calls, SMS, email and phone reminders = verified admin **or** durable Enterprise entitlement (cloud `subscribers` row is authoritative; outages fail closed). Suspended accounts excluded.
- **New environment names (both default to disabled):** `COMMUNICATIONS_ENABLED`, `SENDGRID_ENABLED`, `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL`. Twilio credentials do not enable email.
- **No new SQL** is required; communications reuse `case_store` under the key `communications` with `comms_<id>` dispatch reservations.
- **Landing page pricing** states the ladder as implemented: Free $0, Starter $29, Pro $49, Professional $149, Enterprise $500/30 days, no free paid-plan trial.
- **Build marker** `/api/health` → `patch12b-twilio-api-key`.

## Patch 12 — Server Key Vault and provider overrides are administrator-only

- **Build marker** `/api/health` → `patch12b-twilio-api-key`.
- `GET /api/config` no longer lists which server secrets are loaded. It carries capability flags only (`brain.ready`, `payments_ready`, public Paystack key, plan ladder).
- New `POST /api/admin/keys` (administrators only, signed token required) returns the loaded/not-loaded booleans the **Server Key Vault** panel shows. Values never leave the server.
- The Settings panel shows **Operator controls** (brain provider, override API key / base URL / model, HaveIBeenPwned key) and the **Server Key Vault** only to signed-in administrators. Ordinary accounts see personal preferences only.
- The server strips `api_key`, `base_url`, `hibp_key` and any model name that is not one of the configured server models from every non-administrator request before provider resolution — so a modified client cannot redirect the assistant to another endpoint or pick an unconfigured model. Turbo/Smart still works for everyone.
- No environment changes are required for this patch. Redeploy commit `patch12` → Manual Deploy → confirm `/api/health` shows `patch12b-twilio-api-key` and `/api/config` has no `keys` object.

## Patch 12b — Twilio API key + readiness diagnostics

- **Build marker** `/api/health` → `patch12b-twilio-api-key`.
- New optional env rows `TWILIO_API_KEY_SID` (`SK…`) and `TWILIO_API_KEY_SECRET`. Outbound Twilio requests use the API key first and fall back to the Auth Token on 401. `TWILIO_AUTH_TOKEN` is still required for signed webhooks.
- New admin-only `POST /api/admin/twilio` (read-only readiness; `refresh:true` bypasses the 5-minute cache). Surfaced in Admin console → Diagnostics → **Twilio readiness** with a plain blockers list.
- Deploy: paste the updated env file (adds the two rows), Manual Deploy the latest commit, confirm the build marker.
