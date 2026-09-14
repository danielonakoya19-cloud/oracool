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

---

## STEP 2 — Paste the API keys (the critical step)

1. Open your new service → **Environment** tab
2. Scroll to **Environment Variables** → click **"Add from .env"**
3. Select & copy **ALL 42 lines** from the file **`RENDER_ENV.txt`** (this workspace)
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
   enter the app until they click it. Admin emails auto-skip verification.

---

## STEP 4 — Verify everything

1. Open **`https://oracool-ai.onrender.com/api/health`** → should say `online`
2. Open **`https://oracool-ai.onrender.com/api/config`** → `"keys"` should be all `true`;
   `image_ready`, `video_ready`, `nasa_ready` = `true`
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

- **Images**: always work — HiAPI → TokenMix → **OraCool free HD engine** (FLUX) fallback
  cascade. Today HiAPI (`402 insufficient balance`) and TokenMix (paid models need funds)
  are **out of credit**, so images render from the free engine with an honest note.
- **Video**: needs HiAPI credit. Top up at **hiapi.ai → Billing** — the app activates it
  instantly, no redeploy needed.
- **Pixazo**: its `reve-image` model was retired by the provider (HTTP 410, 2026-08-15) —
  removed from the cascade.
- **OpenAI**: key valid but 0 credits — only affects premium chat brain fallback (Groq is
  the primary brain and works).
- **No free PRO trial exists any more** — the 24-hour trial endpoint and button are removed,
  and any old trial tokens are cryptographically rejected.
