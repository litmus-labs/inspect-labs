"""Release a lab log in tiers: each reader sees what their tier allows, and can
check that what they see came from the original record.

A release policy gives each field of a lab log file a tier. The default tiers are
named for who may read them, the way managed-access schemes for sensitive
biological data work:

- ``public``: anyone.
- ``vetted``: identity-checked reviewers under an agreement, such as auditors,
  third-party evaluators and regulators.
- ``restricted``: named people for hazardous detail; others see only that it
  exists and its commitment.

Releasing at a tier keeps fields at or below it and
replaces each field above it with a commitment: its tier and a keyed digest of its
value. The digest is keyed with a release key, written to a separate private file,
so a short value such as "yes" can't be guessed from its digest.

Someone holding the full lab log and the release key can check a release with
`verify_release`: every kept field must equal the original, and every withheld
field's commitment must match the original value. The full lab log's own hash
chain shows it wasn't changed after the run. Together these let an auditor confirm
that a public summary is a faithful cut of the record without the public seeing
withheld detail.

Unmatched fields get the policy's default tier, which should be the most
restrictive one: fields nobody classified are withheld (fail closed).

This module is pure apart from reading and writing the files it is given.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Path_ = tuple[str, ...]

STRUCTURAL = (
    "kind",
    "schema_version",
    "samples.*.sample_uuid",
    "samples.*.chain_sha256",
    "samples.*.actions.*.sequence",
    "samples.*.actions.*.status",
    "samples.*.actions.*.decision.outcome",
    "samples.*.actions.*.decision.rule",
    "samples.*.actions.*.decision.source",
)
"""Fields every tier keeps, so any reader can follow what happened and match a
release to the hash chains recorded elsewhere (the native log, a witness)."""


class FieldRule(BaseModel):
    """Give the field at ``path``, and everything under it, a tier.

    ``path`` is dotted, with ``*`` matching one segment, such as
    ``samples.*.payload.events.*.host``. List positions are segments too. A more
    specific rule (a longer path) overrides it further down.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str = Field(min_length=1)
    tier: str = Field(min_length=1)


class ReleasePolicy(BaseModel):
    """Ordered tiers, least restricted first, and rules; the most specific rule wins."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str = Field(min_length=1)
    tiers: tuple[str, ...] = ("public", "vetted", "restricted")
    rules: tuple[FieldRule, ...] = ()
    default_tier: str = "restricted"

    @model_validator(mode="after")
    def _known_tiers(self) -> ReleasePolicy:
        if len(set(self.tiers)) != len(self.tiers) or not self.tiers:
            raise ValueError("tiers must be unique and non-empty")
        for tier in (self.default_tier, *(rule.tier for rule in self.rules)):
            if tier not in self.tiers:
                raise ValueError(f"Unknown tier {tier!r}")
        return self

    def tier_of(self, path: Path_) -> str:
        """The tier of one field.

        Structural fields are always in the first tier. Otherwise the rule for the
        field or its nearest ancestor applies (on a tie, the one with fewer
        wildcards), else the default tier.
        """
        if any(_matches(path, pattern) for pattern in STRUCTURAL):
            return self.tiers[0]
        covering = [rule for rule in self.rules if _covers(rule.path, path)]
        if not covering:
            return self.default_tier
        best = max(covering, key=lambda r: (len(r.path.split(".")), -r.path.count("*")))
        return best.tier


def _matches(path: Path_, pattern: str) -> bool:
    wanted = pattern.split(".")
    return len(path) == len(wanted) and all(
        fnmatch.fnmatchcase(part, want) for part, want in zip(path, wanted, strict=True)
    )


def _covers(pattern: str, path: Path_) -> bool:
    """Whether ``pattern`` matches ``path`` or one of its ancestors."""
    length = len(pattern.split("."))
    return length <= len(path) and _matches(path[:length], pattern)


class Withheld(BaseModel):
    """What a release shows in place of a field above its tier."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    withheld: str
    """The field's tier."""
    sha256: str


def _commit(key: bytes, path: Path_, value: Any) -> str:
    canonical = json.dumps(
        {"path": list(path), "value": value}, sort_keys=True, separators=(",", ":")
    )
    return hmac.new(key, canonical.encode(), hashlib.sha256).hexdigest()


def _cut(value: Any, path: Path_, allowed: set[str], policy: ReleasePolicy, key: bytes) -> Any:
    if path:
        tier = policy.tier_of(path)
        # A withheld container is withheld whole (hiding its shape too), unless some
        # field actually inside it is kept.
        if tier not in allowed and not _keeps_something(value, path, allowed, policy):
            return Withheld(withheld=tier, sha256=_commit(key, path, value)).model_dump()
    if isinstance(value, dict):
        return {k: _cut(v, (*path, str(k)), allowed, policy, key) for k, v in value.items()}
    if isinstance(value, list):
        return [_cut(v, (*path, str(i)), allowed, policy, key) for i, v in enumerate(value)]
    return value


def _keeps_something(value: Any, path: Path_, allowed: set[str], policy: ReleasePolicy) -> bool:
    """Whether any field actually present under ``path`` would be kept."""
    if isinstance(value, dict):
        children = [((*path, str(k)), v) for k, v in value.items()]
    elif isinstance(value, list):
        children = [((*path, str(i)), v) for i, v in enumerate(value)]
    else:
        return False
    return any(
        policy.tier_of(child) in allowed or _keeps_something(item, child, allowed, policy)
        for child, item in children
    )


class Release(BaseModel):
    """A lab log file released at one tier."""

    model_config = ConfigDict(extra="forbid")
    kind: str = "release"
    tier: str
    policy: ReleasePolicy
    source_sha256: str
    """Digest of the full lab log file this release was cut from."""
    key_sha256: str
    """Digest of the release key, so a key can be matched to its release."""
    document: dict[str, JsonValue]


def release_lab_log(
    lab_log_file: Path, policy: ReleasePolicy, tier: str, output: Path, key_file: Path
) -> Release:
    """Write a release of ``lab_log_file`` at ``tier``, and its key to ``key_file``.

    The key file is written privately (mode 600) and should travel only with the full
    lab log, to readers allowed to see everything. Neither file is ever overwritten.

    Raises:
        ValueError: ``tier`` is not one of the policy's tiers.
        FileExistsError: ``output`` or ``key_file`` already exists.
    """
    if tier not in policy.tiers:
        raise ValueError(f"Unknown tier {tier!r}")
    raw = lab_log_file.read_bytes()
    document = json.loads(raw)
    allowed = set(policy.tiers[: policy.tiers.index(tier) + 1])
    key = secrets.token_bytes(32)
    release = Release(
        tier=tier,
        policy=policy,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        key_sha256=hashlib.sha256(key).hexdigest(),
        document=_cut(document, (), allowed, policy, key),
    )
    # Write both or neither, so a failed release leaves nothing behind.
    for path in (output, key_file):
        if path.exists():
            raise FileExistsError(f"{path} already exists")
    _write_new(output, release.model_dump_json(indent=2) + "\n")
    try:
        _write_new(key_file, key.hex() + "\n")
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return release


def _write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        stream.write(text)


def verify_release(release_file: Path, lab_log_file: Path, key_file: Path) -> list[str]:
    """Check a release against the full lab log it claims to come from.

    Returns each problem found; an empty list means the release is a faithful cut:
    every kept field equals the original and every withheld field's commitment
    matches it. Check the full lab log's hash chains separately (for example with
    ``read_lab_logs``).
    """
    release = Release.model_validate_json(release_file.read_text())
    raw = lab_log_file.read_bytes()
    key = bytes.fromhex(key_file.read_text().strip())
    problems: list[str] = []
    if hashlib.sha256(raw).hexdigest() != release.source_sha256:
        problems.append("the release was not cut from this lab log file")
    if hashlib.sha256(key).hexdigest() != release.key_sha256:
        problems.append("this key does not belong to the release")
    allowed = set(release.policy.tiers[: release.policy.tiers.index(release.tier) + 1])
    expected = _cut(json.loads(raw), (), allowed, release.policy, key)
    _compare(expected, release.document, (), problems)
    return problems


def _compare(expected: Any, found: Any, path: Path_, problems: list[str]) -> None:
    where = ".".join(path) or "the document"
    if isinstance(expected, dict) and isinstance(found, dict):
        if set(expected) != set(found):
            problems.append(f"{where} has different fields")
        for name in sorted(set(expected) & set(found)):
            _compare(expected[name], found[name], (*path, name), problems)
    elif isinstance(expected, list) and isinstance(found, list):
        if len(expected) != len(found):
            problems.append(f"{where} has a different length")
        for index, (a, b) in enumerate(zip(expected, found, strict=False)):
            _compare(a, b, (*path, str(index)), problems)
    elif expected != found:
        problems.append(f"{where} differs from the lab log")
