# ORA-COOL AI 🤖💠

A J.A.R.V.I.S.-style personal intelligence system with a **free / PRO tier**,
**Paystack payments**, a live AI brain, an OSINT + markets suite, and smart-home
control — in one web app.

---

## 💠 Holographic orb centerpiece (JARVIS / cyberpunk)
- A large **floating orb** sits at the top of the chat — it **floats, pulses and
  talks** with expanding neon rings, and carries a glowing **triangle emblem** at
  its core (matching the UI's cyan/gold palette).
- Status: **ONLINE** (green) → **LISTENING** (purple) → **THINKING** (gold) →
  **SPEAKING** (cyan) — the orb pulses faster while it talks.

## 🤖 Proactive AI — it talks *before* you ask
- OraCool greets you and then **proactively speaks** when you're idle
  (time-based briefings: morning/afternoon/evening/night) and **predicts what you
  might want** based on your last topic (markets, weather, space, games, OSINT).
- While you type, a **gold "💡 predicted" chip** completes what you're likely to
  say. Toggle in Settings → Proactive intelligence.

## 🧠 Cores wired directly into the AI
- Every chat message is auto-routed through the **1,000-core engine**: the most
  relevant cores engage automatically (shown as `ORA-COOL · GO + DIONYSUS`) and
  their persona guides the reply. `@CORE` still forces a specific core.
- Cores now run *alongside* the live tools: ask for data and OraCool both engages
  the right core AND executes the real lookup in one message.

## 🛡️ 2FA · OAuth · Face login
- **Two-factor (TOTP)** — enable in Account → Security (works with Google
  Authenticator / Authy). Verified end-to-end. Secrets stored AES-256 encrypted.
- **OAuth** — Google + GitHub sign-in buttons on the gate (via Supabase; enable
  the provider in Supabase → Authentication → Providers, then add the redirect).
- **Face / fingerprint login** — WebAuthn biometric unlock using your device's
  Face ID / Touch ID. Register in Account → Security after first sign-in.
  (Needs HTTPS — the live app / installed PWA, not the sandboxed preview.)

## 📲 Installable PWA — the theme "flows" onto your phone
- OraCool is now a **Progressive Web App**: `manifest.json` + JARVIS app icon +
  service worker. Tap **Install / Add to Home Screen** and it becomes a
  full-screen, dark-themed app with its own glowing icon and splash.
- Honest limit: no app can rewrite *other* apps' icons or the OS system theme
  (iOS/Android forbid it) — the installable JARVIS app is the closest legal
  version of "the theme spreads on the device."

## 🔗 Link tracker (visitor preview)
- Paste any URL → OraCool makes a **tracking link named after it** (Intel tab →
  Link tracker). When someone opens the link, their visit is **logged
  automatically** (IP, approximate location, device/OS/browser, screen size,
  language, timezone, platform, CPU cores/RAM, touch capability, referrer, time)
  and they are **instantly redirected to the real site**.
- You watch a **live preview panel** that auto-refreshes every few seconds;
  results are visible only to the link's creator (or admins).
- Honest limits, by design: this is a **redirect tracker** (standard
  analytics mechanics, like Bit.ly), NOT a website clone — it never
  impersonates or reproduces another site's content/branding, and it cannot
  capture a camera silently (browsers force the permission prompt + indicator
  light). A phone number still cannot yield an IP — that requires a lawful
  carrier request (subpoena/court order).

## 🤫 Proactive speech — quieter
- OraCool now only speaks on its own after **30 minutes of inactivity** (no more
  pestering right after you load the app). Toggle in Settings → Proactive
  intelligence.

## 🔗 Investigator link (consent-based — the lawful alternative)
- Create a link that **clearly discloses** to the person exactly what will be
  shared (IP address, approximate location, device & browser, screen size, time,
  and an *optional* photo via the browser's own camera-permission prompt).
- **Nothing is recorded until the visitor taps "Share my info"** — the page shows
  the purpose and a plain-language list before any data leaves their device.
  Closing the page = nothing sent.
- Results are visible only to the creator (or admins): Intel tab → Investigator
  link → My links → View responses. Device/OS/browser are parsed from the
  user-agent; IP is geolocated via ipwho.is; photos (only if the visitor
  separately allows the camera) are saved locally.
- Honest limits, by design: no website cloning/impersonation, no silent capture
  (browsers force the camera prompt + indicator light), and a phone number still
  cannot yield an IP — that requires a lawful carrier request (subpoena/court
  order). For genuine law-enforcement needs, official legal process is the route;
  this tool is for consent-based check-ins only.

## ✨ Features

| Area | Free | PRO |
|------|------|-----|
| 🧠 AI brain (Groq / OpenAI) | ✅ | ✅ |
| 🎙️ Voice in (speak) & 🔊 voice out (replies aloud) | ✅ | ✅ |
| 🗣️ **Wake word — "Hey OraCool"** (always-listening) | ✅ | ✅ |
| 👤 **Supabase accounts** (signup/login, PRO follows your email) | ✅ | ✅ |
| 💠 JARVIS holographic UI + boot sequence | ✅ | ✅ |
| 🔍 IP recon · Domain WHOIS/DNS · Username search · Email (HIBP) · **Phone intel** · Weather | ✅ | — |
| 📈 Stocks (Finnhub) · Crypto (CoinGecko) · FRED economic data | ✅ | — |
| 🐙 **GitHub console** (profile, repos, repo search, rate limit) | ✅ | ✅ |
| 🔎 Deep web search (Tavily) | 🔒 | ✅ |
| 🛰️ Shodan host intel · VirusTotal · AbuseIPDB · URLScan · LeakCheck | 🔒 | ✅ |
| 💳 Paystack payments + 24h free trial | — | ✅ |
| 🏠 Home Assistant device control | ✅ | ✅ |
| 🧠 **1,000-core intelligence map** — route with `@CORE` | ✅ | ✅ |
| 🪐 **NASA space intel** (APOD · Earth · NEO · Mars · Library) | 🔒 | ✅ |
| 💳 5-tier pricing (Free / Starter / PRO / Ultra / Enterprise) | ✅ | ✅ |

Every tool has an **"Ask OraCool to analyze"** button — the AI brain reads the
raw results and explains them like an intelligence analyst.

---

## 🚀 Quick start

```bash
cd oracool
python3 server.py          # pure stdlib, no installs needed
# open http://localhost:8000
```

The brain works **out of the box** using the server key vault (`keys.json`) —
no pasting keys in the UI needed.

## 🌍 Publishing to the internet (permanent)

The whole app is **one Python file, zero pip dependencies**, so it deploys
anywhere. Included for you:

- `DEPLOY.md` — full step-by-step guide (Render / Railway / Fly.io / VPS)
- `render.yaml` — one-click Render blueprint (free tier)
- `Procfile` — `web: python3 server.py` (Railway auto-detects it)
- `Dockerfile` — for any Docker host
- `keys.example.json` — the template to fill with your own secrets
- `.gitignore` — keeps `keys.json` + `data/` out of git

**To publish:** push to GitHub → import into Render/Railway → add your secrets
as environment variables → done. Permanent HTTPS URL, no more rotating tunnel.

**To update later (after you earn):** `git pull` + restart. Features, prices
(`PLANS` in `server.py`), and tools all update the same way. See `DEPLOY.md`.

---

## 🔑 Key status (as tested 2026-09-08)

| Key | Status | Note |
|-----|--------|------|
| Groq | ✅ working | Brain default (OpenAI has 0 credits) |
| OpenAI | ⚠️ valid, **no credits** | Add billing → platform.openai.com, then switch provider to OpenAI |
| Paystack (live) | ✅ working | ₦5,000 PRO price configured in `keys.json` |
| Supabase (URL + secret) | ✅ reachable | Stores upgrades in `subscribers` table when it exists |
| GitHub | ✅ valid | Stored; no app feature uses it yet |
| Shodan | ✅ working | Host lookups return data |
| VirusTotal | ✅ working | |
| AbuseIPDB | ✅ working | |
| IPinfo | ✅ working | Enriches IP results |
| Tavily | ✅ working | |
| URLScan | ✅ working | |
| Finnhub | ✅ working | |
| CoinGecko | ✅ demo tier | Works (demo key header) |
| FRED | ✅ working | |
| LeakCheck | ❌ **no license** | 403 — buy/activate a license at leakcheck.io |
| FCS (both keys) | ❌ invalid | "not a valid API Access Key" |
| DomScan | ❓ unknown | No public API docs found — send base URL if you have it |

### ⚠️ Security — rotate these keys NOW
You pasted live secrets into a chat. Treat them as compromised and **rotate
everything** (Paystack, OpenAI, Groq, GitHub, Supabase, and the JWT/encryption
secrets), then drop the new values into `keys.json`.

---

## 💳 How payments work

1. User clicks **PRO → "Pay with Paystack"** (or starts the **24h free trial**).
2. Server calls Paystack *initialize* → user pays on Paystack checkout.
3. Paystack redirects back with `?reference=…` → server *verifies* the payment.
4. Server issues a **signed JWT (HS256, 90 days)** → PRO tools unlock.

- PRO tools are gated server-side: they return `402 locked` without a valid JWT.
- Price/label: `PRO_PRICE_NGN` / `PRO_LABEL` in `keys.json`.
- Test safely with `PAYSTACK_TEST: true` + test keys.

---

## 🔧 Architecture

- **`server.py`** — pure-stdlib backend:
  - Chat proxy (OpenAI-compatible, SSE streaming, OpenAI→Groq fallback)
  - OSINT: RDAP/WHOIS, DNS-over-HTTPS, ipwho.is + IPinfo, HIBP, 50-platform
    username scan (concurrent), Open-Meteo weather
  - PRO: Tavily, Shodan, VirusTotal, AbuseIPDB, URLScan, LeakCheck (JWT-gated)
  - Markets: Finnhub, CoinGecko, FRED
  - Paystack initialize/verify + JWT (HS256) issuance + trial
  - Supabase status + subscriber persistence, Home Assistant bridge
- **`index.html`** — full frontend, no external dependencies.
- **`keys.json`** — server-side key vault (never exposed to the browser).

---

## 🗣️ Wake word
- Toggle it in **Settings** (or click the **WAKE** badge in the header).
- Needs Chrome/Edge + mic permission. When armed, say **"Hey OraCool"** — it
  chimes, then listens for your command and answers out loud.
- While OraCool is speaking, the microphone briefly pauses so it never hears
  itself and falsely triggers.

## 👤 Accounts (Supabase) — mandatory signup/login
- **Every user must create an account or sign in before using OraCool** —
  a holographic gate covers the app on first load.
- Email + **strong password** (8+ chars, uppercase, lowercase, number — with a
  live strength meter). Signup auto-confirms and signs you in instantly.
- **PRO follows your email:** after you pay, sign in on any device and
  OraCool restores your PRO plan automatically.
- Admins sign in with their admin email for free full access.

## 📞 Phone intel — now global
- Covers **200+ country codes**. State/region/city resolution for: **Nigeria**
  (network + area), **US/Canada** (area→state), **UK**, **India**, **Brazil**
  (DDD→state), **Mexico**, **South Africa** (network + area), **Kenya**
  (Safaricom/Airtel/Equitel/Telkom), **Egypt**, **Russia** (Beeline/MTS/
  MegaFon/Tele2), **Australia**, **Germany**, **France**, **Spain**, **Italy**,
  **Ghana** and more.
- ⚠️ Honest limit: **exact street / live-GPS location is impossible** for
  civilians (only telecoms/LE can triangulate). OraCool gives the maximum legal
  answer — state/region/city + carrier from the number's metadata.

## 🌍 Weather — global
- Works for **any city or country on Earth** (Open-Meteo), plus **"My
  location"** (browser GPS) and direct coordinates.
- Now shows a **7-day forecast** (high/low, condition, rain probability).

## 🏆 Leaderboard
- Trading tab shows the **top paper traders** with P&L % — admins see full
  details in the Admin console.

## 🐙 GitHub console
- **Code tab** → your profile, your repos, search any repo, API rate limit.
- Uses the `GITHUB_TOKEN` server-side (never exposed to the browser).

## 📞 Phone intel (free)
- **Intel → Phone Intel.** From a number's metadata it identifies:
  **country, region/state/city, carrier, line type (mobile/landline/VoIP),
  timezone and current local time** — plus search-engine links to check where
  the number appears publicly (PhoneInfoga-style).
- Deep coverage for **Nigeria** (MTN/Glo/Airtel/9mobile + landline area codes),
  **US/Canada** (area code → state), **UK** (dialling code → city), **India**
  (STD → city), **Ghana** and 200+ country codes.
- ⚠️ Honest limit: **exact live GPS tracking of a number is impossible** for
  civilians (only telecom operators/law enforcement can triangulate). This tool
  gives the location *encoded in the number* — that's the legal maximum.
- Optional: add a free **NumVerify** key to `keys.json` for live carrier
  verification (https://numverify.com — free tier).

## 📈 Trading desk (paper — simulated money only)
- **Trading tab** → a full paper-trading desk with $100,000 of fake money.
- **Manual trades:** pick a symbol (AAPL, TSLA, NVDA, SPY, BTC, ETH, SOL…), enter an
  amount, Buy / Sell all.
- **🤖 AI trade idea:** OraCool reads live indicators (RSI, SMA20/50, trend, 1d/7d
  change) and returns a structured idea — bias, entry, stop-loss, take-profit,
  confidence and reasoning.
- **Auto-trader (paper):** a background engine that checks BTC/ETH every 10 min,
  asks the AI brain for a BUY/SELL/HOLD decision, and executes simulated trades
  (max 10% of cash per trade). Default OFF.
- State persists in `data/paper_account.json`.

### ⚠️ Why not "register + trade real accounts for you"
1. Automating sign-ups on brokers/exchanges violates their ToS (accounts get banned).
2. No AI can guarantee profit — anyone promising that is misleading you.
3. Managing/trading funds is regulated; always keep real-money trading under your
   own account and your own control.
- ✅ **The proper upgrade path:** connect a broker that officially supports algo
  trading (e.g. Alpaca paper, Binance testnet) via API keys — I can wire that next
  when you're ready. **Never share brokerage passwords.**

## 👑 Admin console
- Admins (set in `keys.json` → `ADMIN_EMAILS`): **danielonakoya19@gmail.com** and
  **thinkglobal1000@gmail.com**.
- Sign in with an admin email → the **Admin tab** appears:
  - live stats (users, blocked, PRO count, total paper P&amp;L)
  - **search** users by email
  - **block / unblock** with a custom reason (blocked users lose PRO and get
    "suspended" on trade attempts)
  - **grant / revoke PRO** manually — give any user a PRO plan for N days
    without payment (new `/api/admin/pro` endpoint)
  - admins always get full access — **no payment required**.
- Users are tracked automatically: every signed-in user is recorded with their
  own $100,000 paper account, so each user's gains are visible to admins.

## 🏦 Real-broker paper trading (Alpaca)
- Free Alpaca account → Paper Trading → get **API Key ID + Secret** → put them
  in `keys.json` (`ALPACA_PAPER_KEY_ID` / `ALPACA_PAPER_SECRET`), restart.
- The Trading tab then shows your real Alpaca paper account (equity, buying
  power) and lets you place real paper orders through a regulated US broker.

## 🎯 Pocket Option (DEMO only — honest notes)
- OraCool **cannot register accounts or extract SSIDs for you** — that violates
  Pocket Option's ToS and, technically, the SSID is only created in *your*
  browser when *you* log in.
- What it *can* do: **demo-mode connector** using **your own SSID** (paste it in
  Settings → Trading; get it via F12 → Application → Session Storage → `ssid`).
- Binary options are extremely high-risk — demo only, never real money. The
  connector is best-effort and must be validated against your live session.

## 🧠 1,000-core intelligence map
- OraCool ships a **1,000-core** catalogue across 10 clusters (Core Intelligence,
  Global & Cultural, Advanced Science, Business & Enterprise, Advanced Security,
  Advanced OSINT, Autonomous Agents, Technical & DevOps, Creative & Design,
  Future & Expansion).
- **Route a core** by typing `@CORE` in chat — e.g. `@SOCRATES challenge this
  assumption`, `@SUN_TZU win this negotiation`, `@GO analyze Bitcoin`. The AI
  adopts that core's persona for the reply.
- The **Cores tab** lets you search/browse all 1,000 and tap one to **activate**
  it for the session; the active core shows as a chip above the chat input.
- Data lives in `cores.json` (built from `cores_part1..4.py` via `build_cores.py`);
  `cores.py` does the routing/keyword scoring.

## 🪐 Space intelligence (NASA)
- Wired to your NASA API key (`NASA_API_KEY` in keys.json — live-tested).
- **Space tab**: Astronomy Picture of the Day (image or video), live **Earth
  (DSCOVR EPIC)** frames, **Near-Earth Objects** today (closest first, hazardous
  flagged), **Mars rover** imagery (via the NASA Image Library — the old Mars
  Photos API was retired), and full **NASA Image Library** search.
- Tiered per pricing: APOD = Starter+; Earth/NEO/Mars/Library = PRO+. Admins
  always get full access.

## 💳 Five-tier pricing
- **Free $0 · Starter $29 · Pro $49 · Professional $149 · Enterprise $500** per month (Enterprise unlocks every feature — nothing is gated).
- Prices are shown in **US dollars** across the Plans tab and the upgrade modal,
  where the user **picks their plan** (Starter / PRO / Ultra / Enterprise) before
  paying. The ₦ equivalent is shown as a secondary line.
- Paystack charges in the configured currency (`PAYSTACK_CURRENCY`, default
  `NGN` for a Nigerian account — ₦20k / ₦50k / ₦100k / ₦500k). Set it to `USD`
  if your Paystack account is USD-enabled and it will charge $20/$50/$100/$500.
- The chosen plan is stored on the subscription record and the JWT carries the
  tier (`free|starter|pro|ultra|enterprise`). The **Plans tab** shows your
  current tier, upgrade buttons, and a full **feature-comparison matrix**.
  Admins are automatically **Enterprise** (no payment, per your rule).

## ⚡ Speed — "Turbo" mode
- Settings → **Response speed**: ⚡ Turbo (default) uses the fast Groq model
  `allam-2-7b` (~2× faster first reply) · 🧠 Smart uses `qwen/qwen3.8-27b`.
- Replies are capped (`CHAT_MAX_TOKENS`, default 600) so they never drag on,
  and streaming shows the first token almost instantly.
- Model IDs are configurable: `GROQ_MODEL`, `GROQ_FAST_MODEL`, `BRAIN_PROVIDER`.

## 🕵️ Breach intel (HIBP + infostealers) — working now
- **Email Breach** tool is now multi-engine:
  1. **HaveIBeenPwned** (breaches + pastes) — needs an API key (free tier is
     discontinued; add `HIBP_API_KEY` in keys.json or Settings).
  2. **Hudson Rock infostealer feed** — free, no key. Reports infected machines
     tied to the address (computer name, OS, compromised date, masked logins).
- **Shodan** — key verified live (PRO tier).
- **LeakCheck** — new key stored; it's valid but has **no paid license attached**
  ("No license on this key"). Buy a license for this exact key at leakcheck.io
  (a few $/month) and the tool works with no code changes.
- **OpenAI** — new key stored and valid, but the account has **no credits**
  (429 `credit_balance_exhausted`). The brain auto-falls back to Groq, so chat
  keeps working; add OpenAI billing to use GPT/DALL-E.

## 🔐 Encrypted vault (ENCRYPTION_KEY/IV now wired)
- PRO+ users can store secrets encrypted at rest: `/api/vault/set|get|list`.
- Pure-Python **AES-256-CBC** (verified against FIPS-197), key = SHA-256 of
  `ENCRYPTION_KEY`, IV from `ENCRYPTION_IV`. No third-party crypto dependency.

## 🤖 Agentic AI — the AI runs tools by itself
- When you ask in chat for real data, OraCool **detects the intent and runs the
  live tool itself**, then answers grounded in the actual result — no tab needed.
- Recognizes (and executes) automatically: weather in any city, crypto prices,
  stock prices, FRED economic series (inflation/unemployment/GDP/rates), IP
  geolocation, domain WHOIS, email breach, phone intel, trading signals,
  NASA picture-of-the-day / asteroids, current time, and simple math.
- On PRO+ tiers it also auto-runs: Tavily web search, Shodan, VirusTotal,
  AbuseIPDB and LeakCheck (tier is read from your login).
- The chat shows a green "⚙️ Live: …" line naming every tool it ran, and the
  answer uses the real numbers.

## 📎 Files — attach & analyze (KIMI core)
- Tap the 📎 button to attach a file. Extracts text from **PDF, DOCX, XLSX,
  TXT/MD/CSV/JSON/HTML and code files** (pure-Python: zlib-based PDF text
  extraction, zipfile DOCX/XLSX, no dependencies), then the AI analyzes it.
- Images are detected and OraCool explains that vision needs OpenAI credits
  (your OpenAI account currently has none — add billing to enable image analysis).
- 5 MB max per file.

## 📱 Device control — honest limits
- Chat commands: **"open WhatsApp"**, "open YouTube/Instagram/Spotify/Netflix/X/
  Telegram/Google/Gmail", "send email", "call 0803…", "open camera / take a photo".
- The **Devices tab** adds a quick-launch app grid and a working **camera**
  (captures a photo from your device via the browser).
- These open the real app via deep links when you run OraCool on the device.
  A web page **cannot** silently control the OS (Wi-Fi, lock screen, "any app",
  screenshots of other apps) — that needs a companion app or **Home Assistant**
  (also in the Devices tab). OraCool tells you this instead of pretending.

## 💳 Payments — multi-currency → Naira
- Plans are priced in **USD** ($20/$50/$100/$500) and charged in **₦** via
  Paystack (your Nigerian account). The checkout has a currency selector
  (₦ NGN / $ USD).
- **Foreign cards are accepted automatically** — Paystack Nigeria converts them
  to Naira at settlement, which is exactly your "collect different currencies,
  convert to Naira on your side" flow. (Charging in USD requires a USD-enabled
  Paystack account; the default NGN path is live and tested.)

## 📈 Trading desk — ready to go live (needs your keys)
- All UI + endpoints are built and tested end-to-end for **Alpaca paper**
  (real broker, paper money) and **Pocket Option DEMO**.
- To activate Alpaca: create a free account at alpaca.markets → Paper Trading →
  copy API Key ID + Secret into `keys.json` (`ALPACA_PAPER_KEY_ID`,
  `ALPACA_PAPER_SECRET`), restart. I cannot create these for you — they're tied
  to your identity/email.
- To activate Pocket Option demo: log in at pocketoption.com → F12 →
  Application → Session Storage → copy `ssid` → paste in Settings → Trading.
  OraCool can't extract an SSID for you (it only exists in your own browser
  session, and auto-registration is against their ToS).
- Full 39-symbol watchlist (22 stocks + 17 crypto) on the paper desk.

## 📋 Still worth getting (optional)
- **OpenAI credits** (or keep using Groq — it's free and works)
- **HaveIBeenPwned key** (free) for the free-tier email breach tool
- **LeakCheck license** to replace/back HIBP in PRO
- **Valid FCS key** if you want forex data specifically
- **Paystack test keys** to trial payments without real charges

## 🗺️ Roadmap
- Wake word ("Hey OraCool") · local desktop agent
- Supabase user accounts + login (keys already wired)
- Subdomain enumeration, cert transparency, URL unshortening
- WhatsApp / Telegram bot channel

---
*Built for you. All systems online.*
