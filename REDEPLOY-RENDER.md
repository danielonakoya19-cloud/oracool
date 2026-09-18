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
   - **Build Command:** leave empty (or `pip install -r requirements.txt`)
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
3. Select & copy **ALL 43 lines** from the file **`RENDER_ENV.txt`** (this workspace)
4. Paste into the box → **Save Changes**
5. Render restarts automatically (~1 min)

> If you can't copy from the file, the same 42 lines are printed in the chat
> message that came with this guide — copy them from there.

---

## STEP 3 — Supabase SQL + email verification wiring (one time)

1. **supabase.com/dashboard** → your project (`eyiawcqkdtoyvlssbfmg`)
2. Left sidebar → **SQL Editor** → **New query**
3. Paste the contents of **`SUPABASE-SETUP.sql`** (this workspace) → **Run**
   (it creates `subscribers` and `user_flags` tables — safe, `if not exists`)
4. **Email verification (signup flow)** — Authentication → **URL Configuration**:
   - **Site URL** = your Render URL (e.g. `https://oracool-ai.onrender.com`)
   - Under **Redirect URLs**, Add URL → `https://oracool-ai.onrender.com/**` (also add
     `http://localhost:8000/**` while testing)
5. Confirm **Authentication → Sign In / Providers → Email** is ENABLED and
   **Confirm email** is ON. New signups then get a real verification email; they can't
   enter until they enter the code. Admin emails auto-skip verification.
6. **Show the code inside the email (recommended):** Authentication → **Email Templates →
   "Confirm signup"** → edit the HTML body and add this line where you want it:
   `Your OraCool activation code is: <b style="font-size:24px;letter-spacing:6px">{{ .Token }}</b>`
   OraCool verifies that numeric code in-app (AI verifies it — no link clicking needed).
   Without this edit, users can still paste the email link instead — the app accepts both.
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
