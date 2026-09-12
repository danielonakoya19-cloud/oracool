# 🚀 Publishing OraCool AI — Step-by-Step Guide

OraCool AI is a **single Python file with zero dependencies** (pure standard
library). That means you can run it almost anywhere. This guide covers the
quick ways to put it on the public internet **permanently** (unlike the
temporary preview link).

---

## 0. Before you publish — one-time checks

1. **Fill in `keys.json`** (copy from `keys.example.json`).
   - `ADMIN_EMAILS` — your own email(s). These accounts get the Admin console
     with no payment.
   - `JWT_SECRET` — a long random string (used to sign PRO/admin tokens).
   - At minimum for a working launch: `GROQ_API_KEY` (the AI brain),
     `PAYSTACK_SECRET_KEY` + `PAYSTACK_PUBLIC_KEY` (payments),
     `SUPABASE_URL/ANON/SERVICE` (accounts/2FA/OAuth). Everything else is
     optional and lights up more tools.
2. **Never commit `keys.json` or `data/`** to a public git repo. They're
   already in `.gitignore`.
3. Test locally once: `python3 server.py` → open http://localhost:8000.

---

## Option A — Render.com (easiest, free tier)

1. Put the project in a GitHub repo (or push directly from Render).
2. Render → **New +** → **Blueprint**, point at the repo.
   - It reads `render.yaml` automatically (already included).
3. In the service's **Environment** tab, add every secret from `keys.json` as
   an environment variable (Render env vars override `keys.json`):
   - `ADMIN_EMAILS` as a JSON array string, e.g. `["you@example.com"]`
   - `GROQ_API_KEY`, `PAYSTACK_SECRET_KEY`, `SUPABASE_URL`, … etc.
4. Deploy. Render gives you a permanent `https://yourapp.onrender.com` URL.

> Free-tier note: Render free web services sleep after ~15 min of inactivity
> and wake on the next request (first request may take ~30 s). Pay ~$7/mo for
> always-on.

---

## Option B — Railway.app (easy, free trial)

1. Railway → **New Project** → **Deploy from GitHub repo**.
2. It auto-detects the `Procfile` (`web: python3 server.py`).
3. Add the secrets in the **Variables** tab (same list as Option A).
4. Railway gives a permanent `https://*.up.railway.app` URL.

---

## Option C — Fly.io / any Docker host

`Dockerfile` is included:
```bash
docker build -t oracool .
docker run -p 8000:8000 --env-file <(python3 -c "import json;print('\n'.join(f'{k}={v}' for k,v in json.load(open('keys.json')).items() if isinstance(v,str)))") oracool
```
(Or pass secrets one by one with `-e`.)

---

## Option D — VPS (full control, ~$5/mo)

```bash
# on the server
git clone <your-repo> && cd <repo>
cp keys.example.json keys.json   # then edit it
nohup python3 server.py > oracool.log 2>&1 &
# point a domain at it, or use Caddy/nginx for HTTPS
```

---

## After publishing — updating later ("when we earn from the AI")

Because the whole app is one repo, updates are one command:
```bash
git pull && systemctl restart oracool        # or: restart the Render/Railway service
```
- **To add features/tools:** edit `server.py` + `index.html`, push, redeploy.
- **To change prices/plans:** edit `PLANS` in `server.py` (or Paystack plans).
- **To grant a paying user manually:** Admin console → *Grant PRO* (uses the
  new `/api/admin/pro` endpoint).
- **Revenue view:** Paystack dashboard shows every payment; the Admin console
  shows user/PRO/blocked stats in-app.

---

## Security checklist (do NOT skip)

- [ ] `JWT_SECRET` is long/random and **not** the example value.
- [ ] `ADMIN_EMAILS` contains only your own emails.
- [ ] `keys.json` is NOT in the git repo (check `.gitignore`).
- [ ] Supabase Auth: set Site URL + redirect URLs to your public domain
      (for OAuth Google/GitHub to work).
- [ ] Paystack: set your callback/return URL if you change domain.
- [ ] Rotate any key that was ever pasted in chat or screenshots.
