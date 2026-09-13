# Video shoot checklist

Everything else is done. This is the only engineering work left before Devpost.

## What you can record today (no live call)

These beats use the public demo or local dry run only:

| Beat | Time | How |
| --- | --- | --- |
| Title / problem | 0:00–0:15 | Title card or voiceover |
| Refusal montage | 0:15–0:45 | https://clearedtocall.vercel.app — three refused rows |
| Timezone proof | 0:45–1:00 | Switch time preset on demo page |
| Audit chain | 2:50–3:00 | Audit panel, verification green |

No phone, no credits, no consent needed.

## What needs consent + a connected call

| Beat | Time | Blocker |
| --- | --- | --- |
| Live opt-out | 1:00–2:15 | Prior express consent + 08:00–21:00 **recipient local** |
| Suppression replay | 2:15–2:35 | Same call as above |
| Promise-to-pay | 2:35–2:50 | Optional second cleared account |

## Indian number (`+91`, `Asia/Kolkata`)

CALL-E supports **India / English**. Your gate policy is US federal (TCPA/FDCPA); say that in the video: the demo encodes US rules, while the test recipient happens to be in India.

### Before the live take

1. **Get consent.** Rule 2 requires `consent_on_file: true` and a real `consent_timestamp`. Verbal on camera is fine if you state it clearly: *"Do you consent to a test collection call from this demo?"* — then set the timestamp to that moment in `fixtures/demo-live.json`.
2. **Set the fixture** (gitignored, never commit):

   ```json
   {
     "account_id": "A-9001",
     "phone_e164": "+91XXXXXXXXXX",
     "timezone": "Asia/Kolkata",
     "consent_on_file": true,
     "consent_timestamp": "2026-09-14T10:30:00Z"
   }
   ```

3. **Shoot between 08:00 and 21:00 IST.** The live button re-checks real time, not the demo preset.
4. **Preflight (free):**

   ```powershell
   .venv\Scripts\python.exe -m cleared.cli preflight --accounts fixtures/demo-live.json --account-id A-9001 --region IN
   ```

   Wait for `ready_to_run: true` before spending a credit.

5. **Start demo armed:**

   ```powershell
   .venv\Scripts\python.exe -m demo.app --accounts fixtures/demo-live.json --region IN --allow-live
   ```

6. On the call: let the disclosure play, then say **"Stop calling me."** Run batch again to show `ON_SUPPRESSION_LIST`.

Budget three credits: rehearsal, opt-out take, spare.

## If consent is not ready yet

Submit with the dry-run beats only and note in Devpost that the live opt-out beat is pending documented consent. The gate, skill PR, tests, and demo still demonstrate the product. A connected call strengthens the entry but is not required to show refusal logic.

## After the shoot

1. Upload unlisted to YouTube
2. Paste URL into [`DEVPOST.md`](DEVPOST.md)
3. Submit Devpost
