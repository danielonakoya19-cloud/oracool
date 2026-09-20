# 🚀 Publish OraCool AI on Render — Step-by-Step (free)

You picked **Render** and you have **GitHub**. Total time: ~10 minutes.
At the end you get a permanent link with "oracool" in it that stays up.

Files I prepared for you:
- **`oracool-deploy.zip`** (in the workspace) — the code, ready to upload.
- **`RENDER_ENV.txt`** (in the `oracool` folder) — all your keys in `NAME=value`
  form, ready to paste into Render. ⚠️ Keep this file private; never upload it.

---

## PART 1 — Put the code on GitHub (5 min)

1. Go to **github.com** → sign in.
2. Top-right **`+`** → **New repository**.
3. Repository name: **`oracool`** (or `oracool-ai`).
4. Set it to **Private** ← important, keeps your code hidden.
5. Click **Create repository**.
6. On the new repo page, click **"uploading an existing file"** (or
   **Add file → Upload files**).
7. Unzip `oracool-deploy.zip` somewhere on your computer.
8. **Drag every file from inside the zip** into the GitHub upload area.
9. Scroll down → **Commit changes**.

> ✅ The zip already excludes `keys.json`, `data/`, and `RENDER_ENV.txt`, so
> your secrets are safe to upload this way.

---

## PART 2 — Deploy on Render (5 min)

1. Go to **render.com** → **Sign up with GitHub**.
2. Dashboard → **New +** → **Blueprint**.
3. Connect the **`oracool`** repository. Render reads `render.yaml`
   automatically and shows one Web Service: **oracool-ai**.
4. Click **Apply** (Create).
5. Wait 2–3 minutes. Render deploys it at:
   👉 **`https://oracool-ai.onrender.com`**

> There is no build step (pure Python standard library), so it deploys fast.

---

## PART 3 — Add your keys (critical — do this or the AI won't work)

1. Open your service on Render → **Environment** tab.
2. Scroll to **Environment Variables**.
3. Open **`RENDER_ENV.txt`** on your computer, **copy all of it**.
4. In Render, click **Add from .env** (or paste one per line in the box) and
   paste the whole thing.
5. Click **Save Changes**. Render restarts the app with your keys.

The most important variables:
| Variable | What it does |
|---|---|
| `ADMIN_EMAILS` | Your admin emails (JSON list) — gives you the Admin console |
| `GROQ_API_KEY` | The AI brain |
| `PAYSTACK_SECRET_KEY` / `PAYSTACK_PUBLIC_KEY` | Taking payments |
| `SUPABASE_URL/ANON/SERVICE` | Accounts, 2FA, OAuth |
| `JWT_SECRET` | Signs PRO/admin tokens |

---

## PART 4 — Make the link say "oracool"

1. Render → your service → **Settings** → **Rename**.
2. Change the name to **`oracool`**.
3. Your URL becomes 👉 **`https://oracool.onrender.com`**
   (if someone else already took `oracool`, use `oracool-ai` — still fine).

---

## PART 5 — Supabase auth URLs (so login/Google/GitHub work)

1. Open **supabase.com** → your project → **Authentication → URL Configuration**.
2. **Site URL** = `https://oracool.onrender.com`
3. Add redirect URL: `https://oracool.onrender.com/oauth`
4. Save.

---

## Updating later (when you earn)

- Edit files → push to GitHub → Render **auto-deploys** (or click
  Manual deploy → Deploy latest commit).
- Change prices: edit `PLANS` in `server.py`.
- Grant a paying user manually: Admin console → **Grant PRO**.

## Free-tier note (read this)

Render's free tier **sleeps after ~15 minutes of no visitors**; the first
person back waits ~30–60 s while it wakes. It's fine to start. When you're
earning, either:
- upgrade Render to **Starter (~$7/mo)** for always-on, or
- move to a **Railway** / **VPS** plan (same files — see `DEPLOY.md`).
