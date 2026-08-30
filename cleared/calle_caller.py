"""The live caller: places a real call through the CALL-E MCP server.

Reached only for an account the gate already cleared. The flow follows the one
the CALL-E repository documents:

    auth status -> plan_call -> inspect plan -> run_call -> get_call_run

Two things here are safety rather than plumbing:

- the plan is inspected before it runs, and a plan that targets any number other
  than the cleared one is refused;
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
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
from .schema import Account, CallReport, TranscriptTurn
from .script import CallScript

INTEGRATION_HEADER = "cleared-to-call/0.1.0"
E164_IN_TEXT = re.compile(r"\+\d{8,15}")
PROMISE_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
INLINE_TURN = re.compile(r"\[\d{2}:\d{2}:\d{2}\]\s*([A-Za-z_]+)\s*:\s*")

AGENT_LABELS = {"bot", "agent", "ai", "assistant", "system", "robot", "callee_bot"}
RECIPIENT_LABELS = {"user", "customer", "human", "recipient", "consumer", "callee", "caller"}

OUTCOME_PATTERNS = (
    ("opt_out", re.compile(r"\bopt[\s_-]?out\b", re.IGNORECASE)),
    ("promise_to_pay", re.compile(r"\bpromise[\s_-]?to[\s_-]?pay\b", re.IGNORECASE)),
    ("dispute", re.compile(r"\bdisputed?\b", re.IGNORECASE)),
    ("refusal", re.compile(r"\brefusal\b|\brefused\b", re.IGNORECASE)),
    ("no_answer", re.compile(r"\bno[\s_-]?answer\b", re.IGNORECASE)),
)


def resolve_server_url(base_url: str, channel: str, server_url: str | None) -> str:
    if server_url:
        return server_url
    return f"{base_url.rstrip('/')}/mcp/{channel.strip().lower() or DEFAULT_CHANNEL}"


def token_cache_path(cache_root: str, server_url: str) -> Path:
    digest = hashlib.md5(server_url.encode("utf-8")).hexdigest()
    return Path(os.path.expanduser(cache_root)) / digest / "token.json"


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


def _payloads(value: Any) -> list[dict[str, Any]]:
    """Every dict inside a nested MCP response, so key lookups can be shallow."""
    found: list[dict[str, Any]] = []
    stack: list[Any] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            found.append(current)
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
    return found


def first_value(value: Any, keys: tuple[str, ...]) -> Any:
    for payload in _payloads(value):
        for key in keys:
            if key in payload and payload[key] not in (None, ""):
                return payload[key]
    return None


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


def plan_targets_only(plan: Any, phone: str) -> None:
    """Refuse a plan that mentions any number other than the cleared one."""
    numbers = set()
    for payload in _payloads(plan):
        for value in payload.values():
            if isinstance(value, str):
                numbers.update(E164_IN_TEXT.findall(value))
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, str):
                        numbers.update(E164_IN_TEXT.findall(item))
    unexpected = {number for number in numbers if number != phone}
    if unexpected:
        raise CallerError(
            f"plan_call returned a plan targeting {len(unexpected)} number(s) other than the "
            "cleared account. Refusing to run it."
        )


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
    audit_ref: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    _token: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url or DEFAULT_BASE_URL
        self.channel = self.channel or DEFAULT_CHANNEL
        self.cache_root = self.cache_root or DEFAULT_CACHE_ROOT
        self.calle_command = self.calle_command or "calle"
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
        plan_targets_only(plan, account.phone_e164)
        return {
            "account_id": account.account_id,
            "phone_masked": account.masked_phone,
            "region": self.region,
            "ready_to_run": bool(first_value(plan, ("ready_to_run",))),
            "plan_id": first_value(plan, ("plan_id",)),
            "has_confirm_token": isinstance(first_value(plan, ("confirm_token",)), str),
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

            final = await self._poll(client, run_id, meta, account)

        status = extract_status(final)
        summary = extract_summary(final)
        transcript = extract_transcript(final)
        outcome = extract_outcome(summary, transcript, status)
        return CallReport(
            outcome=outcome,
            transcript=transcript,
            promise_date=extract_promise_date(summary, transcript) if outcome == "promise_to_pay" else None,
            provider_status=status,
            provider_run_id=run_id,
            raw={"summary": summary, "status": status},
        )

    async def _call_tool(self, client: Any, name: str, arguments: dict[str, Any], meta: dict[str, Any]) -> Any:
        result = await client.call_tool(
            name=name, arguments=arguments, meta=meta or None, raise_on_error=False
        )
        payload = result.model_dump() if hasattr(result, "model_dump") else result
        if getattr(result, "is_error", False):
            raise CallerError(f"{name} failed: {json.dumps(payload, default=str)[:400]}")
        return payload

    async def _poll(self, client: Any, run_id: str, meta: dict[str, Any], account: Account) -> Any:
        deadline = time.monotonic() + self.poll_timeout_seconds
        last: Any = None
        while True:
            last = await self._call_tool(client, "get_call_run", {"run_id": run_id}, meta)
            status = extract_status(last)
            self.record("get_call_run", account_id=account.account_id, run_id=run_id, status=status)
            if status in TERMINAL_STATUSES:
                return last
            if time.monotonic() >= deadline:
                raise CallerError(
                    f"timed out after {self.poll_timeout_seconds:.0f}s waiting for run {run_id}"
                )
            await asyncio.sleep(self.poll_interval_seconds)
