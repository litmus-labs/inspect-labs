"""An append-only, hash-chained journal written as a session happens.

A lab log is written when a session ends, so a crash would lose it. The journal
records each step as it happens, before the next one: an action's decision before
it reaches the Lab, any approval, the result, stops and monitor flags. Each line is
flushed to disk before the gateway continues.

Each entry's digest covers the previous digest and the entry, so changing,
removing or reordering an entry breaks every later digest. Hashes detect changes;
they do not stop someone with write access from rewriting the whole file. A witness
helps with that: every few entries the latest digest is handed to a witness, such
as a file on storage the lab doesn't control, so a later rewrite no longer matches.

This module does file I/O and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, JsonValue

EntryKind = Literal["started", "decided", "approval", "finished", "stopped", "flag", "ended"]

JOURNAL_START = hashlib.sha256(b"inspect-labs/journal/v1").hexdigest()

Witness = Callable[[int, str], None]
"""Called with an entry's sequence number and digest, to keep a copy elsewhere."""


class JournalEntry(BaseModel):
    """One step of a session, chained to the previous entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    sequence: int
    at: str
    kind: EntryKind
    body: dict[str, JsonValue]
    previous: str
    sha256: str


def _digest(previous: str, sequence: int, at: str, kind: str, body: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"sequence": sequence, "at": at, "kind": kind, "body": body},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256((previous + canonical).encode()).hexdigest()


class Journal:
    """Append entries to a new journal file, flushing each to disk.

    Args:
        path: A new file; an existing one is never overwritten or appended to.
        witness: Given the latest digest every ``witness_every`` entries and at close.
        witness_every: How often to call the witness, in entries.

    Raises:
        FileExistsError: ``path`` already exists.
    """

    def __init__(
        self, path: Path, *, witness: Witness | None = None, witness_every: int = 1
    ) -> None:
        if witness_every < 1:
            raise ValueError("witness_every must be at least 1")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o600)
        self.path = path
        self._stream = os.fdopen(descriptor, "w", encoding="utf-8")
        self.witness = witness
        self.witness_every = witness_every
        self.head = JOURNAL_START
        self.sequence = 0
        self._witnessed = 0

    def append(self, kind: EntryKind, body: dict[str, JsonValue]) -> JournalEntry:
        """Write one entry and flush it to disk before returning.

        Raises:
            ValueError: The journal is closed.
        """
        if self._stream.closed:
            raise ValueError("The journal is closed")
        sequence = self.sequence + 1
        at = datetime.now(UTC).isoformat()
        digest = _digest(self.head, sequence, at, kind, body)
        entry = JournalEntry(
            sequence=sequence, at=at, kind=kind, body=body, previous=self.head, sha256=digest
        )
        self._stream.write(entry.model_dump_json() + "\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self.head, self.sequence = digest, sequence
        if self.witness is not None and sequence - self._witnessed >= self.witness_every:
            self._witness()
        return entry

    def _witness(self) -> None:
        if self.witness is not None and self._witnessed != self.sequence:
            self.witness(self.sequence, self.head)
            self._witnessed = self.sequence

    def close(self) -> None:
        """Hand the final digest to the witness and close the file."""
        if not self._stream.closed:
            self._witness()
            self._stream.close()


class JournalCheck(BaseModel):
    """The result of reading a journal back."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    entries: list[JournalEntry]
    head: str
    ended: bool
    """True when the session ended normally (an ``ended`` entry is last)."""
    torn_tail: bool
    """True when the last line was cut off, as after a crash mid-write."""


def read_journal(path: Path) -> JournalCheck:
    """Read a journal and check every digest.

    A cut-off last line is reported, not treated as tampering: a crash can leave
    one. Anything else that doesn't check out raises.

    Raises:
        ValueError: An entry is malformed, out of order or doesn't match its digest.
        OSError: The file is unavailable.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    torn_tail = lines[-1] != ""
    complete = lines[:-1]
    entries: list[JournalEntry] = []
    head = JOURNAL_START
    for number, line in enumerate(complete, start=1):
        entry = JournalEntry.model_validate_json(line)
        if entry.sequence != number or entry.previous != head:
            raise ValueError(f"Journal entry {number} is out of order")
        if _digest(head, entry.sequence, entry.at, entry.kind, entry.body) != entry.sha256:
            raise ValueError(f"Journal entry {number} does not match its digest")
        head = entry.sha256
        entries.append(entry)
    return JournalCheck(
        entries=entries,
        head=head,
        ended=bool(entries) and entries[-1].kind == "ended",
        torn_tail=torn_tail,
    )


def witness_file(path: Path) -> Witness:
    """A witness that appends ``sequence digest`` lines to a file.

    It is only as independent as where the file lives: keep it on storage the lab's
    operators can't rewrite, such as another machine or an append-only store.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def witness(sequence: int, digest: str) -> None:
        with os.fdopen(
            os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a", encoding="utf-8"
        ) as stream:
            stream.write(f"{sequence} {digest}\n")
            stream.flush()
            os.fsync(stream.fileno())

    return witness


def check_witness(journal: JournalCheck, witness_path: Path) -> list[str]:
    """Compare a journal with a witness file; returns each disagreement.

    Every witnessed digest must match the journal's entry with that sequence, and a
    witnessed entry must not be missing from the journal.
    """
    by_sequence = {entry.sequence: entry.sha256 for entry in journal.entries}
    problems = []
    for line in witness_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sequence_text, _, digest = line.partition(" ")
        sequence = int(sequence_text)
        recorded = by_sequence.get(sequence)
        if recorded is None:
            problems.append(f"entry {sequence} was witnessed but is missing from the journal")
        elif recorded != digest.strip():
            problems.append(f"entry {sequence} differs from its witnessed digest")
    return problems
