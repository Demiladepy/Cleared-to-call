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

## Category (dropdown)

**Customer outreach / engagement.**

The application places outbound calls to borrowers about a past-due balance,
which is outreach. Not *Lead qualification* (nobody is being qualified), and not
*Order / exception follow-up* (that is orders and invoices). If a judge reads the
gate itself as the product, *Workflow & back-office automation* is defensible,
but the task the calls perform is outreach.

## In one sentence, what real-world task does your CALL-E application handle?

Before an AI agent places an outbound collections call, it checks the borrower's
local call window, recorded consent, do-not-call status and required disclosure,
refuses the call with the named rule if any check fails, and otherwise places it
through CALL-E, suppressing the number if the borrower asks to stop being called.

## Testing instructions for application

**No login, no credentials, nothing dials.** Live calling is disabled on the
public deployment by design: a URL anyone can open should not be able to ring a
real phone or spend credits.

**1. Public demo (2 minutes)** — https://clearedtocall.vercel.app

1. The batch holds seven fictional borrowers. Three are refused before anything
   dials, each for a different reason: `OUTSIDE_CALL_WINDOW` (06:30 local in Los
   Angeles), `NO_CONSENT`, and `ON_SUPPRESSION_LIST`.
2. Switch the time preset from **Mid-morning** to **Late evening**. At the same
   instant New York flips ALLOW → BLOCK and Los Angeles flips BLOCK → ALLOW,
   because rule 1 reads each borrower's stored timezone and never infers it from
   the phone number.
3. Expand **Technical details** on any row for the block code, the evidence behind
   it, and the audit reference.
4. Open the opt-out borrower's transcript: the recipient says "stop calling me",
   the call ends, and the number is added to the suppression list. That call is
   simulated, and the page says so.
5. Scroll to **Audit chain**: each entry carries the hash of the one before it,
   and verification passes.

**2. Local run (5 minutes)** — Python 3.11+ and Node 20+.

```bash
git clone https://github.com/Demiladepy/Cleared-to-call.git
cd Cleared-to-call
python3 -m venv .venv
.venv/bin/pip install -e ".[demo,dev]"      # Windows: .venv\Scripts\pip
.venv/bin/python -m pytest                  # 277 tests
.venv/bin/python -m cleared.cli run --now 2026-08-28T13:30:00Z --fresh
.venv/bin/python -m cleared.cli verify      # audit chain check
```

To see tamper detection, edit any line in `runtime/audit.jsonl` and run
`verify` again: it reports the first broken entry.

**3. The Agent Skill on its own** — merged upstream as
https://github.com/CALLE-AI/awesome-phone-call-agents/pull/574. No dependencies:

```bash
node skills/cleared-to-call/scripts/evaluate-account.mjs --file skills/cleared-to-call/assets/example-accounts.json --account-id A-1002 --now 2026-08-28T13:30:00Z
node skills/cleared-to-call/scripts/check-revocation.mjs --utterance "take me off your list"
node skills/cleared-to-call/scripts/check-revocation.test.mjs
```

Exit codes: `0` allow, `2` refuse, `3` opt-out detected.

**Live calling** needs a logged-in CALL-E CLI and `--execute`. It was run against
the real provider; see `FEEDBACK.md` for what happened, including why calls to
Nigerian and Indian numbers never connected.

## Before you click Submit

- [ ] Video uploaded (unlisted YouTube is fine)
- [ ] Video URL pasted above
- [ ] Upstream skill PR open or merged (see `SUBMISSION.md`)
- [ ] Feedback survey pasted from `SUBMISSION.md`
