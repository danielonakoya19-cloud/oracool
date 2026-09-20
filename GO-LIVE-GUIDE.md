# ✅ OraCool AI — Fresh Start (v3) — Go-Live Guide

**Live URL:** https://oracool.onrender.com

Everything below is already built, tested, and pushed to GitHub (Render auto-deploys).
The new code is LIVE. Two small manual steps remain at the bottom.

---

## 1. What changed in this fresh start

| Feature | Status |
|---|---|
| **Glowing orb that changes colour** (replaces the brain at the centre) | ✅ built — cyan→blue→violet→pink→gold→green, with the gold triangle emblem inside |
| **AI creates images from chat** | ✅ built & tested — "create an image of…" works end-to-end |
| **AI creates videos from chat** | ✅ built — "create a video of…" (ULTRA plan) |
| **AI runs ALL tools itself** | ✅ built — weather, stocks, crypto, IP/domain/email/phone OSINT, NASA, web search, Shodan, VirusTotal, etc. — just tell the AI, no clicking around |
| **Tier cascade** | ✅ built — Free ⊂ Starter ⊂ PRO ⊂ Ultra ⊂ Enterprise |
| **Mobile / every-device layout** | ✅ rebuilt — chat-first single column on phones, big touch targets, decluttered header |
| **All your new API keys wired** | ✅ wired in code — HiAPI, Pixazo, ShortAPI, TokenMix, FCS, DomScan, Google OAuth, Tavily, Shodan, VirusTotal, AbuseIPDB, IPInfo, LeakCheck, URLScan, Finnhub, CoinGecko, FRED, NASA |
| **Admins see the user base** | ✅ built — admin console shows every registered user from Supabase Auth |
| **No preset admin password** | ✅ done — admins sign up with their **own** password; they're auto-flagged as admin by their email |
| **Smart control (Home Assistant)** | ✅ wired — Devices tab lets you link HA URL + token; the AI then controls lights/switches/fans by voice |

### Tier cascade (each tier inherits everything below it)

| Tier | Gets everything in → plus |
|---|---|
| **FREE** | IP / domain / email / phone / username OSINT, weather, stocks, crypto, FRED, NASA picture-of-the-day, time, math |
| **STARTER** | + web search, NASA Earth & asteroids |
| **PRO** | + Shodan, VirusTotal, AbuseIPDB, URLScan, LeakCheck, **image creation** |
| **ULTRA** | + **video creation**, GitHub console, DomScan, FCS, smart-home control |
| **ENTERPRISE** | everything, unlimited (admins) |

The AI politely tells a user which plan unlocks a feature they ask for (it never fakes a result).

---

## 2. Admin accounts (important — read this)

**There is NO preset password.** The two admins are recognised by email only:

- `danielonakoya19@gmail.com`
- `thinkglobal1000@gmail.com`

**To set up an admin:** open the app → **Sign up** with that email and your own strong
password. The app automatically grants that account full ADMIN + Enterprise access.

---

## 3. Step you MUST do #1 — put the keys on Render (~1 min)

The new code is live, but Render only has 4 old keys, so image/video/OSINT tools
will say "not configured" until you paste the new ones.

1. Open **dashboard.render.com** → your **oracool** service → **Environment** tab
2. Click **"Add from .env"**
3. Open **`RENDER_ENV.txt`** (this workspace) → select all 48 lines → copy
4. Paste into the box → **Save Changes** (Render restarts automatically)

> 🔒 `RENDER_ENV.txt` is PRIVATE — never share it or commit it to GitHub.

### Verify
Open `https://oracool.onrender.com/api/config` — scroll to `"keys"`: every key
should show `true`. Also `image_ready`, `video_ready`, `nasa_ready` should be `true`.

---

## 4. Step you MUST do #2 — run the SQL in Supabase (~1 min)

Needed so PRO subscriptions and block/bans survive Render's restarts.

1. Open **supabase.com/dashboard** → your project `eyiawcqkdtoyvlssbfmg`
2. Left sidebar → **SQL Editor** → **New query**
3. Paste everything from **`SUPABASE-SETUP.sql`** (this workspace) → **Run**

It creates two tables (`subscribers`, `user_flags`), fully safe (`if not exists`).

---

## 5. Final check after both steps

- Sign up as an admin email → badge shows **ENTERPRISE**, Admin tab appears
- Tell the AI: *"create an image of a golden eagle at sunrise"* → it generates + shows the image
- Tell the AI: *"what's the weather in London"* and *"is 8.8.8.8 on Shodan"* → it runs the tools
- Open on your phone → single column, orb at top, everything fits

Any of these don't work? Tell me the exact error and I'll fix it.
