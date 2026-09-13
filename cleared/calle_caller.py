"""The live caller: places a real call through the CALL-E MCP server.

Reached only for an account the gate already cleared. The flow follows the one
the CALL-E repository documents:

    auth status -> plan_call -> inspect plan -> run_call -> get_call_run

Two things here are safety rather than plumbing:

- the plan is inspected before it runs. CALL-E echoes the destination masked,
  so a plan is refused when a full number differs from the cleared one, when a
  masked destination ends in different digits, or when it names no destination
  at all (unless the operator explicitly accepts that);
- the returned transcript is normalized into `agent` / `recipient` turns, because
  the opt-out re-check downstream only reads recipient turns.

Requires `fastmcp` and a logged-in `calle` CLI. Imported lazily, so nothing in
the gate or the dry-run path depends on it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .callers import (
    DEFAULT_BASE_URL,
    DEFAULT_CACHE_ROOT,
    DEFAULT_CHANNEL,
    STATUS_TO_OUTCOME,
    TERMINAL_STATUSES,
    CallerError,
    build_call_metadata,
    build_plan_arguments,
)
from .policy import Policy, default_policy
from .schema import OUTCOMES, Account, CallReport, TranscriptTurn, mask_phone
from .script import CallScript

INTEGRATION_HEADER = "cleared-to-call/0.1.0"
E164_IN_TEXT = re.compile(r"\+\d{8,15}")
# A number written without its `+`: `2349056215207`, `15550101234`, or the
# national form `09056215207`. Providers do this in free text and in numeric
# fields, and E164_IN_TEXT cannot see it.
BARE_PHONE_DIGITS = re.compile(r"(?<![\d+*])\d{10,15}(?!\d)")
# A destination the provider echoes back masked, such as `…9724`,
# `...9724` or `+2********9724`. Symbol masks only: the goal text we send says
# "your account ending 1001", and matching words like "ending" would read that
# account tail as a wrong phone number and refuse every live call.
MASKED_DESTINATION = re.compile(r"(?:\u2026|\.{3}|\*{2,})(\d{4})(?!\d)")
PROMISE_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
INLINE_TURN = re.compile(r"\[\d{2}:\d{2}:\d{2}\]\s*([A-Za-z_]+)\s*:\s*")

SECRET_KEY_HINTS = (
    "token",
    "secret",
    "authorization",
    "password",
    "api_key",
    "apikey",
    "credential",
)

AGENT_LABELS = {"bot", "agent", "ai", "assistant", "system", "robot", "callee_bot"}
RECIPIENT_LABELS = {"user", "customer", "human", "recipient", "consumer", "callee", "caller"}

OUTCOME_PATTERNS = (
    ("opt_out", re.compile(r"\bopt[\s_-]?out\b", re.IGNORECASE)),
    ("promise_to_pay", re.compile(r"\bpromise[\s_-]?to[\s_-]?pay\b", re.IGNORECASE)),
    ("dispute", re.compile(r"\bdisputed?\b", re.IGNORECASE)),
    ("refusal", re.compile(r"\brefusal\b|\brefused\b", re.IGNORECASE)),
    ("no_answer", re.compile(r"\bno[\s_-]?answer\b", re.IGNORECASE)),
)
REPORT_OUTCOMES = frozenset(item for item in OUTCOMES if item != "not_called")


def resolve_server_url(base_url: str, channel: str, server_url: str | None) -> str:
    if server_url:
        return server_url
    return f"{base_url.rstrip('/')}/mcp/{channel.strip().lower() or DEFAULT_CHANNEL}"


def token_cache_path(cache_root: str, server_url: str) -> Path:
    digest = hashlib.md5(server_url.encode("utf-8")).hexdigest()
    return Path(os.path.expanduser(cache_root)) / digest / "token.json"


def resolve_calle_command(command: str | None = None) -> str:
    """Find the CALL-E CLI. On Windows, bare `calle` is not enough for subprocess."""
    if command:
        return command
    resolved = shutil.which("calle")
    if resolved:
        return resolved
    raise CallerError(
        "the CALL-E CLI is not on PATH. Install it with `npm install -g @call-e/cli` "
        "or pass --calle-command."
    )


def read_access_token(cache_root: str, server_url: str) -> str:
    """Read the access token the `calle` CLI cached at login."""
    path = token_cache_path(cache_root, server_url)
    if not path.is_file():
        raise CallerError(
            f"no CALL-E token cache at {path}. Run `calle auth login` before calling with --execute."
        )
    document = json.loads(path.read_text(encoding="utf-8"))
    token = document.get("token", {}).get("access_token")
    if not isinstance(token, str) or not token:
        raise CallerError(f"CALL-E token cache has no access token: {path}")
    return token


def check_auth(calle_command: str, base_url: str, channel: str, server_url: str) -> dict[str, Any]:
    """Ask the CLI whether it is logged in. Never prints or returns the token."""
    command = [
        *calle_command.split(),
        "auth",
        "status",
        "--json",
        "--base-url",
        base_url,
        "--channel",
        channel,
        "--server-url",
        server_url,
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as error:
        raise CallerError(
            f"the CALL-E CLI is not available as `{calle_command}`. "
            "Install it with `npm install -g @call-e/cli` or pass --calle-command."
        ) from error
    except subprocess.TimeoutExpired as error:
        raise CallerError("`calle auth status` timed out") from error
    if completed.returncode != 0:
        raise CallerError(
            f"`calle auth status` failed with exit code {completed.returncode}. "
            "Run `calle auth login` and try again."
        )
    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def normalize_tool_result(result: Any) -> Any:
    """The plain payload inside whatever the MCP client handed back.

    fastmcp 3.x returns a `CallToolResult` object with no `model_dump`. Depending
    on the tool, the payload arrives on `structured_content`, on `data`, or only
    as JSON in `content[i].text`. Everything downstream walks dicts and lists, so
    a result left as an object reads as empty: every extractor returns None,
    `ready_to_run: true` reads as missing, and `plan_targets_only` sees no
    numbers to object to. Unwrap once, here.

    The text-content path is not optional. Without it, a server that answers only
    in text makes a dialable plan look like `ready_to_run: false`, which is
    indistinguishable from the destination being unsupported.
    """
    if result is None or isinstance(result, (dict, list, tuple)):
        return result

    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict) and structured:
        return structured

    data = getattr(result, "data", None)
    if isinstance(data, dict) and data:
        return data
    if data is not None and hasattr(data, "model_dump"):
        dumped = data.model_dump()
        if isinstance(dumped, dict) and dumped:
            return dumped

    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text.strip():
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue

    if hasattr(result, "model_dump"):
        dumped = result.model_dump()
        if isinstance(dumped, dict):
            nested = dumped.get("structured_content")
            if isinstance(nested, dict) and nested:
                return nested
            return dumped
    return result


def _payloads(value: Any) -> list[dict[str, Any]]:
    """Every dict inside a nested MCP response, so key lookups can be shallow."""
    value = normalize_tool_result(value)
    found: list[dict[str, Any]] = []
    stack: list[Any] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            found.append(current)
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
        elif hasattr(current, "model_dump"):
            stack.append(current.model_dump())
    return found


def first_value(value: Any, keys: tuple[str, ...]) -> Any:
    for payload in _payloads(value):
        for key in keys:
            if key in payload and payload[key] not in (None, ""):
                return payload[key]
    return None


def extract_plan_feedback(plan: Any) -> dict[str, Any]:
    """Human-readable block reasons from a `plan_call` response."""
    summary = first_value(plan, ("confirm_summary",))
    questions = first_value(plan, ("clarifying_questions",)) or []
    if not isinstance(questions, list):
        questions = []
    options: list[str] = []
    for payload in _payloads(plan):
        raw_questions = payload.get("questions")
        if not isinstance(raw_questions, list):
            continue
        for item in raw_questions:
            if not isinstance(item, dict):
                continue
            for option in item.get("options") or []:
                if isinstance(option, dict) and option.get("label"):
                    options.append(str(option["label"]))
    block_reason = None
    if isinstance(summary, str) and summary.strip():
        block_reason = summary.strip()
    elif questions:
        block_reason = str(questions[0])
    return {
        "block_reason": block_reason,
        "clarifying_questions": [str(item) for item in questions],
        "supported_region_language": options,
    }


def extract_status(value: Any) -> str | None:
    status = first_value(value, ("status", "call_status", "final_status", "state"))
    return str(status).upper() if isinstance(status, str) else None


def extract_summary(value: Any) -> str | None:
    summary = first_value(value, ("post_summary", "postsummary", "summary", "result_summary"))
    return str(summary) if isinstance(summary, str) else None


def normalize_speaker(label: str) -> str:
    lowered = label.strip().lower()
    if lowered in AGENT_LABELS:
        return "agent"
    if lowered in RECIPIENT_LABELS:
        return "recipient"
    return lowered or "unknown"


def split_inline_transcript(text: str) -> list[TranscriptTurn]:
    """Split `[00:00:00] BOT: hello [00:00:04] USER: hi` into turns."""
    matches = list(INLINE_TURN.finditer(text))
    if not matches:
        return [TranscriptTurn("unknown", text.strip())] if text.strip() else []
    turns: list[TranscriptTurn] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        if body:
            turns.append(TranscriptTurn(normalize_speaker(match.group(1)), body))
    return turns


def normalize_turn(item: Any) -> TranscriptTurn | None:
    if isinstance(item, str):
        return TranscriptTurn("unknown", item.strip()) if item.strip() else None
    if not isinstance(item, dict):
        return None
    speaker = ""
    for key in ("speaker", "role", "from", "side", "who", "source"):
        if isinstance(item.get(key), str) and item[key]:
            speaker = item[key]
            break
    text = ""
    for key in ("text", "message", "content", "utterance", "transcript", "asr_text"):
        if isinstance(item.get(key), str) and item[key]:
            text = item[key]
            break
    if not text.strip():
        return None
    return TranscriptTurn(normalize_speaker(speaker), text.strip())


def extract_transcript(value: Any) -> tuple[TranscriptTurn, ...]:
    """Pull a normalized transcript out of whatever shape the server returned."""
    for payload in _payloads(value):
        for key in ("transcript", "asr", "conversation", "messages", "turns", "transcript_turns"):
            raw = payload.get(key)
            if isinstance(raw, str) and raw.strip():
                turns = split_inline_transcript(raw)
                if turns:
                    return tuple(turns)
            if isinstance(raw, (list, tuple)) and raw:
                turns = [turn for turn in (normalize_turn(item) for item in raw) if turn]
                if turns:
                    return tuple(turns)
    return ()


def extract_structured_result(value: Any) -> dict[str, str] | None:
    """Read a flat outcome object when CALL-E returns one on `get_call_run`."""
    for payload in _payloads(value):
        for key in ("structured_result", "structuredResult", "result", "call_result"):
            raw = payload.get(key)
            if not isinstance(raw, dict):
                continue
            outcome = raw.get("outcome")
            if not isinstance(outcome, str) or not outcome.strip():
                continue
            promise_date = raw.get("promise_date", "")
            return {
                "outcome": outcome.strip(),
                "promise_date": str(promise_date).strip() if promise_date is not None else "",
            }
    return None


def resolve_call_outcome(
    value: Any,
    summary: str | None,
    transcript: tuple[TranscriptTurn, ...],
    status: str | None,
) -> tuple[str, str | None, str]:
    """Prefer structured results; fall back to prose and provider status."""
    structured = extract_structured_result(value)
    if structured:
        outcome = structured["outcome"]
        promise_date = structured["promise_date"] or None
        if outcome in REPORT_OUTCOMES:
            if outcome == "promise_to_pay" and not promise_date:
                promise_date = extract_promise_date(summary, transcript)
            return outcome, promise_date, "structured"
    outcome = extract_outcome(summary, transcript, status)
    promise_date = extract_promise_date(summary, transcript) if outcome == "promise_to_pay" else None
    return outcome, promise_date, "inferred"


def extract_outcome(summary: str | None, transcript: tuple[TranscriptTurn, ...], status: str | None) -> str:
    """Read the outcome the agent reported, falling back to the provider status."""
    haystacks = [summary or ""]
    haystacks.extend(turn.text for turn in transcript if turn.speaker == "agent")
    for text in haystacks:
        for outcome, pattern in OUTCOME_PATTERNS:
            if pattern.search(text):
                return outcome
    if status and status in STATUS_TO_OUTCOME:
        return STATUS_TO_OUTCOME[status]
    if not transcript:
        return "no_answer"
    return "refusal"


def extract_promise_date(summary: str | None, transcript: tuple[TranscriptTurn, ...]) -> str | None:
    for text in [summary or "", *(turn.text for turn in transcript if turn.speaker == "agent")]:
        match = PROMISE_DATE.search(text)
        if match:
            return match.group(1)
    return None


def looks_like_epoch(digits: str) -> bool:
    """A Unix timestamp in seconds or milliseconds between 2017 and 2033.

    Provider payloads are full of these, and they are the one common kind of
    10- or 13-digit run that is not a phone number. Masking them would make a
    captured sample useless without making it any safer.
    """
    return len(digits) in (10, 13) and digits[0] == "1" and digits[1] in "56789"


def _mask_bare_number(match: re.Match[str]) -> str:
    digits = match.group(0)
    return digits if looks_like_epoch(digits) else mask_phone(digits)


def _names_a_credential(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in SECRET_KEY_HINTS)


def redact_payload(value: Any) -> Any:
    """A copy of a provider payload that is safe to commit.

    B1 needs the real `get_call_run` shape in the repository, but the response
    carries the number that was dialled and the credential used to dial it.
    Numbers are masked with the same `mask_phone` the audit log uses, so the
    committed sample and the audit trail agree. Anything whose key names a
    credential is dropped whole rather than masked: a partially masked token is
    still a token. Keys are matched by substring, so `confirm_token` and
    `refresh_token` are caught without being listed one by one.
    """
    if isinstance(value, dict):
        return {
            key: "<redacted>" if _names_a_credential(str(key)) else redact_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_payload(item) for item in value)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and BARE_PHONE_DIGITS.fullmatch(str(abs(value))):
        digits = str(abs(value))
        return value if looks_like_epoch(digits) else mask_phone(digits)
    if isinstance(value, str):
        masked = E164_IN_TEXT.sub(lambda match: mask_phone(match.group(0)), value)
        return BARE_PHONE_DIGITS.sub(_mask_bare_number, masked)
    return value


def sanitize_call_run(payload: Any) -> dict[str, Any]:
    """A provider payload ready to write to disk: unwrapped, redacted, labelled.

    Both capture paths go through here - `run --execute --capture-payload` during
    a batch, and `capture-run --run-id` for a run that already happened - so a
    committed sample has the same shape whichever one produced it.
    """
    normalized = normalize_tool_result(payload)
    if not isinstance(normalized, dict):
        normalized = {"payload": normalized}
    document = redact_payload(normalized)
    document.setdefault(
        "_note",
        "Captured from get_call_run. Phone numbers masked; secrets redacted.",
    )
    return document


def _plan_strings(plan: Any) -> list[str]:
    strings: list[str] = []
    for payload in _payloads(plan):
        for value in payload.values():
            if isinstance(value, str):
                strings.append(value)
            elif isinstance(value, (list, tuple)):
                strings.extend(item for item in value if isinstance(item, str))
    return strings


def plan_targets_only(plan: Any, phone: str) -> str:
    """Refuse a plan aimed at anyone but the cleared number. Report how it was checked.

    CALL-E's plan response does not echo the full destination; it shows a masked
    one, such as `…9724`. Comparing full numbers alone therefore found nothing to
    compare and passed every plan, including one aimed at the wrong person. So
    both forms are checked:

    - a full E.164 number must equal the cleared number;
    - a masked destination must end in the cleared number's last four digits.

    Returns `full_number`, `last_four`, or `unverified` when the plan names no
    destination at all. Raises on any mismatch.
    """
    full: set[str] = set()
    suffixes: set[str] = set()
    for text in _plan_strings(plan):
        full.update(E164_IN_TEXT.findall(text))
        suffixes.update(MASKED_DESTINATION.findall(text))

    unexpected = {number for number in full if number != phone}
    if unexpected:
        raise CallerError(
            f"plan_call returned a plan targeting {len(unexpected)} number(s) other than the "
            "cleared account. Refusing to run it."
        )
    wrong_suffix = {suffix for suffix in suffixes if suffix != phone[-4:]}
    if wrong_suffix:
        raise CallerError(
            "plan_call returned a plan whose destination ends in "
            f"{', '.join(sorted(wrong_suffix))}, not the cleared account's last four digits. "
            "Refusing to run it."
        )
    if full:
        return "full_number"
    if suffixes:
        return "last_four"
    return "unverified"


def require_verified_destination(plan: Any, phone: str, *, allow_unverified: bool) -> str:
    """The destination check as the live dialler applies it: fail closed.

    A plan that names no destination cannot be checked against the cleared
    account, and a check that cannot run is not a pass. Refuse it unless the
    operator has explicitly accepted that, after seeing it in `preflight`.
    """
    result = plan_targets_only(plan, phone)
    if result == "unverified" and not allow_unverified:
        raise CallerError(
            "plan_call echoed no destination number, so the plan cannot be checked "
            "against the cleared account. Refusing to run it. If `preflight` shows the "
            "same and you accept the risk, pass --allow-unverified-destination."
        )
    return result


@dataclass
class CalleCaller:
    """Places one real CALL-E call per cleared account."""

    policy: Policy | None = None
    region: str = "US"
    language: str = "English"
    base_url: str | None = None
    channel: str | None = None
    server_url: str | None = None
    cache_root: str | None = None
    calle_command: str | None = None
    poll_interval_seconds: float = 10.0
    poll_timeout_seconds: float = 900.0
    # Called with the run id as soon as the provider returns one, so the runner
    # can record that a call is in flight before it can be lost (B3).
    dispatch_hook: Callable[[str], None] | None = None
    # Where to save the terminal `get_call_run` payload, redacted. A live call
    # is expensive and unrepeatable; without this the provider's real response
    # shape is seen once, at runtime, and then thrown away (B1).
    capture_path: str | Path | None = None
    # A plan that echoes no destination cannot be checked against the cleared
    # number. Refused unless the operator opts in, having seen it in preflight.
    allow_unverified_destination: bool = False
    audit_ref: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    _token: str | None = field(default=None, init=False, repr=False)
    _captured: list[Path] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url or DEFAULT_BASE_URL
        self.channel = self.channel or DEFAULT_CHANNEL
        self.cache_root = self.cache_root or DEFAULT_CACHE_ROOT
        self.calle_command = resolve_calle_command(self.calle_command)
        self.server_url = resolve_server_url(self.base_url, self.channel, self.server_url)

    def token(self) -> str:
        if self._token is None:
            check_auth(self.calle_command, self.base_url, self.channel, self.server_url)
            self._token = read_access_token(self.cache_root, self.server_url)
        return self._token

    def record(self, event: str, **fields: Any) -> None:
        self.events.append({"event": event, "at": time.time(), **fields})

    def place_call(self, account: Account, script: CallScript) -> CallReport:
        return asyncio.run(self._place_call(account, script))

    def recover(self, run_id: str) -> CallReport:
        """Recover the outcome of a call that was dispatched but never recorded.

        This is the other half of the duplicate-call interlock: a run that is
        already in flight is finished by asking the provider what happened, never
        by dialling the person a second time.
        """
        return asyncio.run(self._recover(run_id))

    def fetch_run(self, run_id: str) -> Any:
        """Fetch a raw `get_call_run` payload for inspection or fixture capture."""
        return asyncio.run(self._fetch_run(run_id))

    async def _fetch_run(self, run_id: str) -> Any:
        async with self._client() as client:
            return await self._call_tool(client, "get_call_run", {"run_id": run_id}, {})

    async def _recover(self, run_id: str) -> CallReport:
        async with self._client() as client:
            final = await self._poll(client, run_id, {}, None)
        return self._report_from(final, run_id)

    def plan_only(self, account: Account, script: CallScript) -> dict[str, Any]:
        """Plan a call without running it: checks auth, region and payload for free.

        `plan_call` is the provider's validation step. If the region is not
        dialable or the payload is malformed, it fails here, before `run_call`
        has spent anything.
        """
        return asyncio.run(self._plan_only(account, script))

    async def _plan_only(self, account: Account, script: CallScript) -> dict[str, Any]:
        client_factory = self._client()
        policy = self.policy or default_policy()
        async with client_factory as client:
            plan = await self._call_tool(
                client,
                "plan_call",
                build_plan_arguments(account, script, region=self.region, language=self.language),
                build_call_metadata(account, self.audit_ref, policy),
            )
        destination_check = plan_targets_only(plan, account.phone_e164)
        feedback = extract_plan_feedback(plan)
        return {
            "account_id": account.account_id,
            "phone_masked": account.masked_phone,
            "region": self.region,
            "ready_to_run": bool(first_value(plan, ("ready_to_run",))),
            "plan_id": first_value(plan, ("plan_id",)),
            "has_confirm_token": isinstance(first_value(plan, ("confirm_token",)), str),
            "destination_check": destination_check,
            **feedback,
        }

    def _client(self) -> Any:
        """An authenticated MCP client. Imports fastmcp only when a call is wanted."""
        try:
            from fastmcp import Client
            from fastmcp.client.transports import StreamableHttpTransport
        except ImportError as error:  # pragma: no cover - depends on the environment
            raise CallerError(
                "fastmcp is required for live calls: pip install 'cleared-to-call[live]'"
            ) from error

        return Client(
            StreamableHttpTransport(
                self.server_url,
                headers={
                    "Authorization": f"Bearer {self.token()}",
                    "X-Call-E-Integration": INTEGRATION_HEADER,
                },
            )
        )

    async def _place_call(self, account: Account, script: CallScript) -> CallReport:
        policy = self.policy or default_policy()
        arguments = build_plan_arguments(
            account, script, region=self.region, language=self.language
        )
        meta = build_call_metadata(account, self.audit_ref, policy)

        async with self._client() as client:
            plan = await self._call_tool(client, "plan_call", arguments, meta)
            plan_targets_only(plan, account.phone_e164)

            if not first_value(plan, ("ready_to_run",)):
                raise CallerError("plan_call did not return ready_to_run=true")
            destination_check = require_verified_destination(
                plan,
                account.phone_e164,
                allow_unverified=self.allow_unverified_destination,
            )
            self.record(
                "destination_check", account_id=account.account_id, result=destination_check
            )
            plan_id = first_value(plan, ("plan_id",))
            confirm_token = first_value(plan, ("confirm_token",))
            if not isinstance(plan_id, str) or not isinstance(confirm_token, str):
                raise CallerError("plan_call did not return plan_id and confirm_token")
            self.record("plan_call", account_id=account.account_id, plan_id=plan_id)

            run = await self._call_tool(
                client, "run_call", {"plan_id": plan_id, "confirm_token": confirm_token}, meta
            )
            run_id = first_value(run, ("run_id",))
            if not isinstance(run_id, str) or not run_id:
                raise CallerError("run_call did not return run_id")
            self.record("run_call", account_id=account.account_id, run_id=run_id)
            if self.dispatch_hook is not None:
                self.dispatch_hook(run_id)

            final = await self._poll(client, run_id, meta, account)

        return self._report_from(final, run_id)

    def _capture_target(self, run_id: str) -> Path:
        """Where this run's payload goes, without overwriting an earlier one.

        `{run_id}` in the path is filled in. Without it, the first capture in a
        batch takes the path as given and later ones get the run id appended, so
        a batch of three calls leaves three files rather than one.
        """
        raw = os.path.expanduser(str(self.capture_path))
        if "{run_id}" in raw:
            return Path(raw.replace("{run_id}", run_id))
        target = Path(raw)
        if not self._captured:
            return target
        return target.with_name(f"{target.stem}-{run_id}{target.suffix}")

    def _capture(self, final: Any, run_id: str) -> None:
        """Save the terminal payload, redacted, for the extractor tests to use.

        This runs after the call has connected and before its outcome is recorded.
        A capture is evidence for developers; the outcome is a compliance record.
        A full disk or a bad path must never turn a completed call into a provider
        failure in the audit log, so nothing here is allowed to raise.
        """
        if self.capture_path is None:
            return
        try:
            target = self._capture_target(run_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(sanitize_call_run(final), indent=2, sort_keys=True, default=str) + "\n",
                encoding="utf-8",
            )
            self._captured.append(target)
            self.record("capture", run_id=run_id, path=str(target))
        except Exception as error:  # noqa: BLE001 - see the docstring
            self.record("capture_failed", run_id=run_id, error=f"{type(error).__name__}: {error}")

    def _report_from(self, final: Any, run_id: str) -> CallReport:
        """Turn a terminal `get_call_run` payload into a CallReport."""
        self._capture(final, run_id)
        status = extract_status(final)
        summary = extract_summary(final)
        transcript = extract_transcript(final)
        outcome, promise_date, outcome_source = resolve_call_outcome(
            final, summary, transcript, status
        )
        return CallReport(
            outcome=outcome,
            transcript=transcript,
            promise_date=promise_date,
            provider_status=status,
            provider_run_id=run_id,
            raw={"summary": summary, "status": status, "outcome_source": outcome_source},
        )

    async def _call_tool(self, client: Any, name: str, arguments: dict[str, Any], meta: dict[str, Any]) -> Any:
        result = await client.call_tool(
            name=name, arguments=arguments, meta=meta or None, raise_on_error=False
        )
        if getattr(result, "is_error", False):
            payload = normalize_tool_result(result)
            raise CallerError(f"{name} failed: {json.dumps(payload, default=str)[:400]}")
        return normalize_tool_result(result)

    async def _poll(
        self, client: Any, run_id: str, meta: dict[str, Any], account: Account | None
    ) -> Any:
        deadline = time.monotonic() + self.poll_timeout_seconds
        last: Any = None
        while True:
            last = await self._call_tool(client, "get_call_run", {"run_id": run_id}, meta)
            status = extract_status(last)
            self.record(
                "get_call_run",
                account_id=account.account_id if account else "<recovery>",
                run_id=run_id,
                status=status,
            )
            if status in TERMINAL_STATUSES:
                return last
            if time.monotonic() >= deadline:
                raise CallerError(
                    f"timed out after {self.poll_timeout_seconds:.0f}s waiting for run {run_id}"
                )
            await asyncio.sleep(self.poll_interval_seconds)
