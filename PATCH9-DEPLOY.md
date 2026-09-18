# OraCool Patch 9 — deployment and account setup

## What changed

- Ordinary users sign up with email/password and enter immediately; no email-code gate. Reserved admin addresses cannot be claimed through public signup.
- Expired sessions refresh using the saved Supabase refresh token. Last selected chat is scoped per account and restored from server history on login.
- Server reloads conversations from Supabase after a cold disk restart. Cloud outages cannot overwrite an unreachable archive with an empty one. Sidebar shows sync warnings.
- Render now loads ATLOS, receiving-wallet and Agnes environment variables. Enterprise remains **$500 / 30 days**.
- If ATLOS fails and a direct Ethereum wallet is configured, checkout still shows its address/QR. **Direct transfers require manual review.** Wallet activity alone never unlocks a plan. ATLOS still needs valid merchant credentials for hosted checkout.
- Agnes video: corrected `mode: text`, `seconds` string, size and async polling/metadata handling. A live 4-second test returned a valid downloadable MP4. This confirms the current configured key worked for that test, not permanent free capacity.
- Create-tab video generation runs as a background job. Browser reloads can resume checking a running job; jobs themselves do not survive server restarts.
- Links to the official FLUX.1-schnell image and Wan2.1 video demos are in Create. They open external websites; quotas/queues/login restrictions can apply and outputs do not auto-import.
- Admin → OraCool-owned accounts supports Gmail read-only inbox checks, Facebook Page connections and Telegram bots/channels. Credentials are encrypted; no credentials are sent to the AI. AI can inspect connection status/inbox and draft posts. Publishing requires explicit review and confirmation in Admin.

## Deploy the code, then check the version

1. Deploy the latest Patch 9 commit from the connected GitHub repository (or upload the supplied ZIP source). Confirm GitHub/Render are on the same latest commit; a running older build does not gain these fixes until redeployed.
2. Render service → Settings:
   - Build command: `pip install -r requirements.txt`
   - Start command: `python3 server.py`
3. Render → Environment: **edit existing rows; do not import duplicates**.
   - `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY`
   - `ATLOS_MERCHANT_ID`, `ATLOS_API_SECRET`
   - `CRYPTO_WALLET_EVM` (the designated receiving address)
   - `AGNES_API_KEY`, optionally `AGNES_VIDEO_MODEL=agnes-video-2.5-flash`
   - Keep existing `JWT_SECRET`, `ENCRYPTION_KEY`, and `ENCRYPTION_IV` unchanged.
4. Deploy the latest uploaded commit; reload the browser after deploy.
5. Open `/api/health`: `build` must be `patch9-password-history-config`. If not, you are still running an older build.

The secret environment file and keys.json are NOT in the deployment ZIP. Do not commit them. Do not post secrets in chat.

## Supabase and existing users

**Required: the configured Supabase project returned `PGRST205` (missing `public.case_store`) during the live check.** Open Supabase → SQL Editor and run **`CHAT-STORAGE-SETUP.sql`** once. This creates the private backend-only table for conversation and encrypted brand-account mirrors. Do this before expecting chat recovery across Render restarts.
- The new backend uses server-side account creation with email confirmation satisfied; no global Supabase setting change is required for this signup form.
- If other clients call Supabase public signup, disable Confirm email in Supabase's Email provider settings too.
- Existing unconfirmed users may need manual confirmation by the project operator. Do not create duplicate accounts or reset everyone’s passwords.
- Email-address input is no longer proof of inbox ownership. Connecting a mailbox is a separate consent/credential step.
- Histories never saved by an earlier build cannot be reconstructed. A persistent server disk is still needed for generated media files; download important outputs.

## OraCool Gmail

1. **Change the normal Gmail password shared in chat.** Review Google account sessions.
2. Enable Google 2-Step Verification. Create a fresh Google app password, if supported by that Google account.
3. Sign in to OraCool as an existing admin → Admin → OraCool-owned accounts.
4. Choose Gmail. The designated mailbox is `oracoolai19@gmail.com`.
5. Enter the app password in the secure form, confirm ownership, then Verify & connect.
6. Ask the admin AI: “Check OraCool inbox.” It reads unread counts and sender/subject headers, not message bodies. Background inbox checks notify admins when detected unread-message headers change. Gmail sending is not implemented.

The Gmail account has NOT been connected by this patch. Its normal password was not stored or used.

## Social pages

- Facebook: supply the numeric Page ID and an official Page access token with appropriate permissions. The connection verifies that token's Page identity.
- Telegram: supply your bot token and your channel/chat ID; the bot must be able to access that destination.
- The admin attests that each connected destination belongs to OraCool. Credentials prove access, not legal ownership.
- Review the destination and exact post text before confirming publication. No automatic posting from inbox instructions or page content.
- Instagram, X, TikTok and other platforms are not automatically connected. Supply the intended platform/page URL so its official API/permission requirements can be configured separately.
- Revoking an account in OraCool removes its saved credential. For a suspected leak, also revoke the token at its provider.

## Validation

`python -m unittest discover -s tests -v`

Regression tests mock signup/payment/social providers and do not create real users, charge customers, or publish posts. Live Agnes video generation was tested separately. Gmail/Facebook/Telegram need real credentials and have not been end-to-end tested against an owned account.

No unrestricted “obey anything” mode was added. Admin privileges remain scoped to supported, authorized actions; credentials, account isolation and explicit publishing confirmation remain enforced.
