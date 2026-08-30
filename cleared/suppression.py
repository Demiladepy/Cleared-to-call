"""The opt-out / do-not-call suppression list.

Rule 3 reads this list before every dial; rule 5 writes to it the moment a
recipient revokes consent. It is stored as JSONL so an entry can only ever be
appended, and it is loaded into a set for lookups.

Numbers are stored in full because this list exists to be matched against real
dial targets. Everything that leaves the process - summaries, the audit log, the
demo view - shows the masked form instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .schema import mask_phone


def normalize_phone(phone: str) -> str:
    """Strip formatting so `+1 (555) 010-1234` and `+15550101234` match."""
    if not phone:
        return ""
    stripped = "".join(character for character in phone if character.isdigit())
    return f"+{stripped}" if stripped else ""


@dataclass(frozen=True)
class SuppressionEntry:
    phone_e164: str
    reason: str
    source: str
    added_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "phone_e164": self.phone_e164,
            "reason": self.reason,
            "source": self.source,
            "added_at": self.added_at,
        }

    def to_masked_dict(self) -> dict[str, Any]:
        return {**self.to_dict(), "phone_e164": mask_phone(self.phone_e164)}


class SuppressionList:
    """Append-only set of numbers that must never be dialled again."""

    def __init__(
        self,
        path: str | Path | None = None,
        entries: Iterable[SuppressionEntry] | None = None,
    ) -> None:
        self.path = Path(path) if path else None
        self._entries: list[SuppressionEntry] = list(entries or ())
        self._numbers = {normalize_phone(entry.phone_e164) for entry in self._entries}
        if self.path and self.path.is_file():
            self._load()

    def _load(self) -> None:
        assert self.path is not None
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"{self.path}:{line_number} is not valid JSON: {error}"
                ) from error
            entry = SuppressionEntry(
                phone_e164=str(raw.get("phone_e164", "")),
                reason=str(raw.get("reason", "unspecified")),
                source=str(raw.get("source", "seed")),
                added_at=str(raw.get("added_at", "")),
            )
            self._entries.append(entry)
            self._numbers.add(normalize_phone(entry.phone_e164))

    def contains(self, phone: str) -> bool:
        return normalize_phone(phone) in self._numbers

    def __contains__(self, phone: object) -> bool:
        return isinstance(phone, str) and self.contains(phone)

    def __len__(self) -> int:
        return len(self._entries)

    def entries(self) -> tuple[SuppressionEntry, ...]:
        return tuple(self._entries)

    def add(
        self,
        phone: str,
        *,
        reason: str,
        source: str,
        timestamp: datetime | None = None,
    ) -> SuppressionEntry | None:
        """Add a number. Returns None when it was already suppressed."""
        if self.contains(phone):
            return None
        moment = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc)
        entry = SuppressionEntry(
            phone_e164=phone,
            reason=reason,
            source=source,
            added_at=moment.isoformat(),
        )
        self._entries.append(entry)
        self._numbers.add(normalize_phone(phone))
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(entry.to_dict(), separators=(",", ":"), ensure_ascii=False)
                    + "\n"
                )
        return entry
