# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""A ``ChangeLedger`` that survives restarts.

Every staged, applied and discarded change — plus the exact write payloads a staged
change will replay on apply — is written to SQLite (``MERCHANT_LEDGER_DSN``, default
``sqlite:///./merchant/data/ledger.db``; ``:memory:`` for tests). On start the rows are
loaded back and the id sequence continues where it stopped, so ``chg-0007`` is never
handed out twice.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from merchant_agent import ActorKind, ChangeLedger, StagedChange
from merchant_agent.config import MerchantAgentConfig

logger = logging.getLogger(__name__)

DEFAULT_LEDGER_DSN = "sqlite:///./merchant/data/ledger.db"
MEMORY_DSN = ":memory:"
_SQLITE_PREFIX = "sqlite:///"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS changes (
    change_id  TEXT PRIMARY KEY,
    sequence   INTEGER NOT NULL,
    status     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    record     TEXT NOT NULL,
    payloads   TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS changes_status ON changes(status);
"""

WritePayload = tuple[str, dict[str, Any] | list[dict[str, Any]]]


def sqlite_path(dsn: str | None) -> str:
    """``sqlite:///./x.db`` → ``./x.db``; ``sqlite:////abs`` → ``/abs``; ``:memory:`` as is."""
    text = (dsn or DEFAULT_LEDGER_DSN).strip()
    if text in {MEMORY_DSN, f"{_SQLITE_PREFIX}{MEMORY_DSN}", "sqlite://"}:
        return MEMORY_DSN
    if text.startswith(_SQLITE_PREFIX):
        text = text[len(_SQLITE_PREFIX) :]
    if not text:
        raise ValueError(f"MERCHANT_LEDGER_DSN {dsn!r} names no file")
    return text


class SqliteChangeLedger(ChangeLedger):
    """The blueprint ledger's lifecycle and guardrails, with every transition persisted.
    ``payloads`` holds the write plan of a staged change so ``apply_change`` replays the
    payload the operator previewed rather than a recomputed one."""

    def __init__(self, config: MerchantAgentConfig, dsn: str | None = None) -> None:
        super().__init__(config)
        self.path = sqlite_path(dsn)
        if self.path != MEMORY_DSN:
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._payloads: dict[str, list[WritePayload]] = {}
        self._load()

    # ------------------------------------------------------------------ lifecycle

    def stage(self, **kwargs: Any) -> StagedChange:
        change = super().stage(**kwargs)
        self._save(change)
        return change

    def apply(self, change_id: str, actor: str) -> StagedChange:
        change = super().apply(change_id, actor)
        self._save(change)
        return change

    def discard(
        self, change_id: str, actor: str, actor_kind: ActorKind = ActorKind.OPERATOR
    ) -> StagedChange:
        change = super().discard(change_id, actor, actor_kind)
        self._save(change)
        return change

    def annotate(self, change_id: str, notes: list[str]) -> StagedChange:
        """Append notes to a change (the apply-time facts: what was written, a moved
        base) and persist the record."""
        change = self._changes[change_id]
        updated = change.model_copy(update={"guardrail_notes": [*change.guardrail_notes, *notes]})
        self._changes[change_id] = updated
        self._save(updated)
        return updated

    def all(self) -> list[StagedChange]:
        return list(self._changes.values())

    # ------------------------------------------------------------------ payloads

    def set_payloads(self, change_id: str, payloads: list[WritePayload]) -> None:
        self._payloads[change_id] = [(entity, payload) for entity, payload in payloads]
        change = self._changes.get(change_id)
        if change is not None:
            self._save(change)

    def payloads(self, change_id: str) -> list[WritePayload]:
        return list(self._payloads.get(change_id, []))

    # ------------------------------------------------------------------ storage

    def close(self) -> None:
        self._db.close()

    def _save(self, change: StagedChange) -> None:
        sequence = int(change.change_id.rsplit("-", 1)[-1])
        payloads = [
            [entity, payload] for entity, payload in self._payloads.get(change.change_id, [])
        ]
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO changes (change_id, sequence, status, created_at, record, payloads)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    change.change_id,
                    sequence,
                    change.status.value,
                    change.created_at.isoformat(),
                    change.model_dump_json(),
                    json.dumps(payloads),
                ),
            )

    def _load(self) -> None:
        rows = self._db.execute(
            "SELECT change_id, sequence, record, payloads FROM changes ORDER BY sequence"
        ).fetchall()
        for change_id, sequence, record, payloads in rows:
            try:
                change = StagedChange.model_validate_json(record)
            except ValueError:
                logger.error("ledger row %s is unreadable and was skipped", change_id)
                continue
            self._changes[change.change_id] = change
            self._payloads[change.change_id] = [
                (str(entity), payload) for entity, payload in json.loads(payloads or "[]")
            ]
            self._sequence = max(self._sequence, int(sequence))
        if rows:
            logger.info("ledger loaded %d change(s) from %s", len(rows), self.path)
