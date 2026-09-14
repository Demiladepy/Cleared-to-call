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

## The live call: CALL-E's official US test hotline

Two personal numbers were tried and both failed at the carrier, in NG and IN:
zero duration, identical start and end timestamps, `hangup_type: ByCallee`, no
transcript. The phone never rang either time. That is the international line
being rejected, not a country problem, so a third non-US number is not worth a
credit.

The maintainer posted a US testing hotline in Discord #announcements on
7 September for exactly this:

```text
+1 276-322-9632  (English)  ->  +12763229632, America/New_York
```

A published test line, not a private individual: no consent to obtain, nobody
woken up. It is already row `A-9003` in `fixtures/operator-test.json`
(gitignored).

**Already verified, without dialling:** the gate clears `A-9003` once the window
opens, and `plan_call` accepts the US plan — `ready_to_run: true`, a plan id, a
confirm token, and the destination check matching on `...9632`.

### The window is the one constraint

Area code 276 is Virginia, UTC-4 in September, so rule 1 allows the call only
between **12:00 and 01:00 UTC** (13:00-02:00 Lagos). Before 12:00 UTC it refuses
with `OUTSIDE_CALL_WINDOW`. That is the product working, but it costs a take.

### The take

1. **Capture the payload first, off camera.** This is the B1 evidence:

   ```powershell
   .venv\Scripts\python.exe -m cleared.cli run --execute --accounts fixtures/operator-test.json --account-id A-9003 --region US --capture-payload tests/data/call_run_connected.json
   ```

   If it returns a transcript, B1 is closed: the payload carries CALL-E's real
   speaker labels, the last unverified assumption in the system.

2. **Then arm the demo for the on-camera take:**

   ```powershell
   .venv\Scripts\python.exe -m demo.app --accounts fixtures/operator-test.json --region US --allow-live
   ```

3. Let the disclosure play. Show the structured result, the audit entry, and the
   chain still verifying.

**The opt-out beat cannot be done against a hotline** — it cannot ask to be taken
off a list. Film it from the dry-run batch and say plainly on camera that that
beat is simulated while the call you just placed was real. Claiming otherwise is
the one thing that would actually sink the entry.

Budget two credits: one capture run, one on-camera take.

## If consent is not ready yet

Submit with the dry-run beats only and note in Devpost that the live opt-out beat is pending documented consent. The gate, skill PR, tests, and demo still demonstrate the product. A connected call strengthens the entry but is not required to show refusal logic.

## After the shoot

1. Upload unlisted to YouTube
2. Paste URL into [`DEVPOST.md`](DEVPOST.md)
3. Submit Devpost
