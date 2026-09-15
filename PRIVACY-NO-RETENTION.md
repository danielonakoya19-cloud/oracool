# OraCool AI — No-Retention & Privacy Policy (v1.0)

The authoritative copy is served by the app at **`/privacy`** (linked from the signup
gate). Summary for repo/deploy records:

## Data retention
- Investigative queries run at query time. The platform persists **audit metadata**
  (actor, endpoint, timestamp) — not the content of OSINT lookups — unless an
  investigator explicitly preserves it into a **Case** as evidence.
- Case files & evidence live in the operator's own deployment store (Supabase project
  controlled by the OraCool operator). Never sold, shared, or used for training.
- Deletion on demand: cases/artifacts are removed immediately and permanently.

## AI-model training — legal guarantee
- User queries and collected evidence are **never used to train public AI models**.
- Third-party inference uses the provider's non-training/no-store mode wherever the
  provider offers it; prompts carry only what a query itself requires.

## Attribution (OPSEC)
- All OSINT / dark-web index / monitoring requests are issued **server-side** from the
  platform's infrastructure — the investigator's personal IP/device never touches the
  sources queried.
- Passive, lawful collection only: public indexes and public databases. OraCool never
  connects to .onion sites directly, never uses stolen credentials, never purchases
  illicit data (DOJ guidance on dark-web purchasing respected), and never scans systems
  the investigator doesn't own or have written permission to test.

## Evidence integrity (Berkeley-Protocol aligned)
- Every artifact is **SHA-256 fingerprinted at collection time**; an append-only
  **chain-of-custody log** records collect/view/verify/export actions (who, when, what).
- Verify re-computes the hash; export produces a custody document with the case
  fingerprint for hand-off to counsel or law-enforcement channels.
- These records support an authenticity demonstration; they are not notarisation.

## Limits & lawful use
- Verification/lookup outputs are **indicators requiring further review**, never
  definitive verdicts about people or documents (e.g. a registry record existing ≠ a
  genuine physical card).
- Users in jurisdictions without PI licensing frameworks (e.g. Nigeria) remain
  responsible for lawful registration/permits; unlawful surveillance, harassment or
  unauthorized access are prohibited and unassisted by the platform.
