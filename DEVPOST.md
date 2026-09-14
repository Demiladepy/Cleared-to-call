# Devpost submission (copy-paste)

Fill these fields on the hackathon Devpost page. Replace `[VIDEO_URL]` after upload.

## Project name

Cleared to Call

## Tagline

Your AI can place the call. This decides whether it is allowed to.

## Elevator pitch (about)

Cleared to Call is a pre-dial compliance gate for AI outbound collection calls. Before CALL-E dials, four rules check the recipient's local call window, consent on file, suppression list, and mandatory disclosure script. A fifth rule handles live opt-out and permanently suppresses the number. Every allow or refuse appends to a hash-chained audit log.

The public demo runs a batch of fictional accounts: three are refused with named reasons before anything dials. The companion Agent Skill ships in the CALL-E community repo; the Python implementation and demo live in the project repo.

## Links

| Field | Value |
| --- | --- |
| **GitHub repo** | https://github.com/Demiladepy/Cleared-to-call |
| **Upstream skill PR** | https://github.com/CALLE-AI/awesome-phone-call-agents/pull/574 |
| **Live demo** | https://clearedtocall.vercel.app |
| **Demo video** | `[VIDEO_URL]` (unlisted YouTube) |

## Built with

- CALL-E (`plan_call`, `run_call`, `get_call_run`)
- Python 3.11, FastAPI, Node.js (Agent Skill scripts)
- Agent Skills format (`skills/cleared-to-call/`)

## What it does (short)

A pre-dial compliance gate for AI collection calls: it refuses a call with a named reason when a policy precondition fails, ends and suppresses on a live opt-out, and records every allow or refuse in a hash-chained decision log.

## CALL-E account email

Use the address you logged into `calle auth login` with.

## Most valuable feedback

Paste items 1–7 and 8–13 from [`SUBMISSION.md`](SUBMISSION.md) (Most Valuable Feedback survey). They are written for the form.

## Try it (judges)

No login required for the public demo:

1. Open https://clearedtocall.vercel.app
2. See three refused rows (call window, no consent, suppression list) and four cleared
3. Switch the time preset to watch New York and Los Angeles flip opposite ways
4. Expand **Technical details** on any row for the block code and audit ref
5. Scroll to **Audit chain** and confirm verification passes

Local dry run (optional):

```bash
pip install -e ".[demo,dev]"
python -m demo.app
python -m cleared.cli run --now 2026-08-28T13:30:00Z --fresh
```

## Before you click Submit

- [ ] Video uploaded (unlisted YouTube is fine)
- [ ] Video URL pasted above
- [ ] Upstream skill PR open or merged (see `SUBMISSION.md`)
- [ ] Feedback survey pasted from `SUBMISSION.md`
