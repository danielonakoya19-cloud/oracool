# Patch 10 — hands-free voice and real telephone alarms

## Status and scope

**Phone reminders and alarm calls now require a signed-in, non-suspended account that is either an administrator or an enterprise subscriber; client-supplied flags cannot grant this. Normal hands-free voice conversation remains available to everyone.**

For ordinary telephone calls, SMS and email to other consenting people, see `COMMUNICATIONS-SETUP.md` (same eligibility rule: Enterprise or admin).

The interface shows phone reminders only to Enterprise/admin accounts; server-side checks remain authoritative. Losing eligibility cancels that account’s queued reminders. Every account still verifies its own recipient number and confirms each alarm. The configured Twilio number is the outgoing caller, not the recipient.

- Build marker: `patch11-enterprise-communications`.
- Hands-free voice is opt-in. Tap **Start conversation**, allow the microphone, then speak after each reply. No repeated mic taps are needed.
- Stop / mute releases the microphone. Interrupt reply cancels the current browser request and speech. Voice pauses when the page is hidden, locked, or signed out. Listening pauses during speech to avoid feedback; this is turn-taking, not uninterrupted full-duplex audio.
- Replies are spoken sentence-by-sentence as they stream. Voice turns use the configured fast model when available. Ordinary admin small-talk no longer fetches the entire admin board first. Chat cloud syncing and periodic connection telemetry no longer block every first answer token.
- Proactive speech is OFF until explicitly enabled. It offers grounded suggestions while conversation mode is on, with a 10 PM–7 AM quiet period and throttling. It does not claim to have scanned information it has not fetched or predict questions with certainty.
- Chrome/Edge-style Web Speech recognition is used where available. Other capable browsers use short MediaRecorder clips with silence detection, sent to the configured Groq/OpenAI transcription endpoint. Provider credit, browser support, microphone permissions and HTTPS are required. Provider privacy terms apply; this backend does not save audio clips.
- Timer and clock-alarm commands are deterministic. The user reviews the exact date, time, timezone, masked phone number and call consent before saving. “Confirm alarm” can be spoken; “cancel alarm” cancels the preview.
- Phone alarms are actual telephone calls, not simulated browser calls. They ring using the phone's own ringtone/settings and play a short greeting after answer. They do not support a live two-way LLM conversation or recording on the telephone call. Open OraCool for a detailed day plan.
- **Real calling is not enabled or end-to-end tested.** The supplied Account SID/Auth Token authenticated to an active Trial account in read-only checks. That account returned no incoming numbers, no verified outgoing caller IDs, and no Verify services. The supplied originating number could not be confirmed as authorized. No SMS/call was sent and no number or service was purchased/created.

## Required operator setup: Twilio and always-on hosting

1. Obtain your own Twilio account, an appropriate voice-capable originating number, and a Twilio Verify service with SMS verification enabled. Add provider credit and enable destination-country permissions as required. Trial accounts/country restrictions may limit the recipients you can call.
2. Use a continuously running Render instance. A sleeping free web service cannot reliably wake itself to dispatch 6 AM calls. Also keep Supabase available. **Do not merely set a flag while leaving the host able to sleep.**
3. Run ONE active scheduler/application replica. This snapshot-based state store is not designed for horizontal scaling. An atomic database dispatch reservation limits duplicate call attempts across brief restart/deploy overlap; it is not a general distributed job queue.
4. Add these environment variables privately in Render. Edit an existing row if it already exists—do not import duplicates:

| Variable | Value |
|---|---|
| `TWILIO_ACCOUNT_SID` | Your Twilio Account SID |
| `TWILIO_AUTH_TOKEN` | Your secret Twilio Auth Token |
| `TWILIO_FROM_NUMBER` | Your authorized voice-capable Twilio number in E.164 format |
| `TWILIO_VERIFY_SERVICE_SID` | Your Twilio Verify Service SID |
| `PUBLIC_BASE_URL` | Exact public HTTPS origin, e.g. `https://YOUR-APP.onrender.com` (no path) |
| `REMINDER_CALLS_ENABLED` | `true` only after you authorize SMS/call spending |
| `REMINDERS_ALWAYS_ON` | `true` only after arranging continuously running hosting |

Keep `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_ANON_KEY`, `JWT_SECRET` and the original `ENCRYPTION_KEY` configured. Do not rotate the encryption key casually: saved phone credentials then become unreadable.

5. Build with `pip install -r requirements.txt`, start with `python3 server.py`, then deploy the latest commit.
6. Check `/api/health` for the Patch 11 build marker. Open the **Voice settings** drawer (Communications → phone reminders section) to see missing configuration, verify a number and test a short countdown while you are awake.

## No new SQL needed after your successful setup

This patch reuses the private `case_store` table you already created. It stores alarm state at key `reminders`, and one unique `reminder_dispatch_<id>` reservation per dispatched call attempt. Do not manually insert an empty reminders snapshot or delete dispatch reservations for active/previous alarms.

## Recipient setup and commands

1. Sign in with an Enterprise or admin account → **Voice settings** (voice toolbar) and open the phone-reminder section.
2. Enter YOUR number with country code, such as `+234…`, and consent to verification SMS/requested alarm calls.
3. Send the verification SMS and enter its code in the form (not in chat). A code for email signup is still not required; this separate SMS verifies ownership of the telephone number.
4. Confirm the IANA timezone (normally taken from your browser), such as `Africa/Lagos`.
5. Say or type:
   - “Set a timer for 20 minutes.”
   - “OraCool, wake me at 6 AM.”
   - “Call me when it is 6 AM.”
6. Check the date/time shown, then say **“confirm alarm”** or click the confirmation button.
7. Review delivery state in the alarm list. Cancel pending calls there. Disconnecting a phone cancels all pending calls; a call already dispatched may still ring.

Times without AM/PM or a clear 24-hour clock are rejected. Ambiguous/nonexistent daylight-saving times are rejected. This version supports one-time alarms only, between one minute and 30 days for voice-command previews; use one duration (90 minutes, not “one hour and thirty minutes”).

## Delivery, privacy and cost safeguards

- Real calls and SMS verification cost provider credit; they are not an unlimited free feature.
- Calls go only to that account's verified, consenting number, not a number supplied by a model or arbitrary chat message.
- Limits: 10 pending alarms, 5 non-cancelled scheduled calls per account per UTC day, 100 platform-wide per UTC day. Verification attempts are also limited by account, number, IP and global budget.
- Number values are encrypted at rest. Status responses return only a masked number. Twilio credentials stay server-side.
- Provider callbacks require a valid Twilio signature and matching account/call IDs.
- A saved alarm is not a promise of delivery: server outages, deployment downtime, provider limits, network/carrier failures, phone reception, voicemail and Do Not Disturb can prevent a heard ring.
- If the server resumes more than five minutes late, the alarm is marked `missed`, not called unexpectedly hours later.
- Ambiguous network/dispatch outcomes are marked `unknown` and are not automatically retried, to avoid duplicate calls/charges. Inspect the provider console before rescheduling.
- `completed` means provider call completion, not proof the user heard the greeting; voicemail may answer.
- Not suitable for emergency, medication-critical, life-safety or guaranteed wake-up alerts. Keep a native device alarm as a backup.

## Validation performed

- Python regression suite (including time parsing, timezone/DST handling, account isolation, consent, verification, cancellation, duplicate requests, dispatch claims, storage failures, missed alarms and signed callbacks).
- Automated Chromium browser checks: simulated speech recognition → streamed spoken reply → automatic re-listening; early sentence speech; preview before confirmation; Stop; 390px viewport controls.
- Separate Chromium test with a synthetic microphone: real browser MediaRecorder capture → mocked transcription → spoken reply → Stop.
- Simple live chat checks succeeded. Latency for small questions is not a guarantee for searches, media generation or slow external tools.
- Physical Android/iPhone microphone behavior and actual Twilio SMS/telephone delivery still need testing after deployment and provider setup.

No unrestricted safety or authentication bypass is part of this patch.


## Credential handoff and remaining setup

The provided Account SID, Auth Token and caller number were saved only in the private workspace `keys.json` and `RENDER_ENV.txt`. They are excluded from GitHub and the deployment ZIP. A GitHub push does not install private environment values in Render: set/edit the corresponding Render variables separately.

Since the token was shared in chat, rotate it in Twilio and update the private environment settings (including Render) with the replacement. Do not post it in chat or commit it.

Before enabling delivery:
- Check that the outgoing number is owned/authorized in the SAME Twilio account/subaccount as the Account SID. The read-only inventory check found no matching number or caller ID.
- Create/configure a Twilio Verify service and set `TWILIO_VERIFY_SERVICE_SID`.
- Set `PUBLIC_BASE_URL` to the actual deployed HTTPS origin.
- Arrange always-on hosting; then explicitly set the two enablement flags.
- Follow trial/country/recipient restrictions in Twilio; verify the receiving admin phone in the application.

Latest authorization tests cover authenticated non-admin attempts at all seven reminder routes, ignored client-supplied admin flags/emails, service-layer denial, queued-reminder revocation, and separate ordinary/admin browser flows. Provider delivery remains mocked in those tests.
