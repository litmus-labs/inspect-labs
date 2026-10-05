"""Tiered release of lab logs: what each tier sees, and checking a release."""

import json
import os
from pathlib import Path

import anyio
import pytest

pytest.importorskip("pylabrobot")

from inspect_labs.actions import DEFAULT_RULES  # noqa: E402
from inspect_labs.gateway import ActionRefused  # noqa: E402
from inspect_labs.liquid import DeckLayout, Labware, WellContent  # noqa: E402
from inspect_labs.liquid_handling import LiquidHandlingEnvironment  # noqa: E402
from inspect_labs.release import (  # noqa: E402
    FieldRule,
    ReleasePolicy,
    release_lab_log,
    verify_release,
)
from inspect_labs.serve import LabSession  # noqa: E402

LAYOUT = DeckLayout(
    labware=(
        Labware(name="tips", kind="tiprack_300ul", slot=1),
        Labware(name="plate", kind="plate_96_360ul", slot=2),
    ),
    contents={"plate": {"A1": WellContent(volume_ul=200, solutes={"dye": 2000})}},
)
POLICY = ReleasePolicy(
    version="test-1",
    rules=(
        FieldRule(path="samples.*.actions.*.action.tool", tier="public"),
        FieldRule(path="samples.*.payload", tier="vetted"),
        FieldRule(path="samples.*.actions.*.action.arguments", tier="vetted"),
        FieldRule(path="lab", tier="public"),
    ),
)


@pytest.fixture
def lab_log(tmp_path) -> Path:
    session = LabSession(LiquidHandlingEnvironment(tmp_path / "lab", LAYOUT), DEFAULT_RULES)

    async def scenario():
        await session.call("read_volume", {"labware": "plate", "well": "A1"})
        with pytest.raises(ActionRefused):
            await session.call("aspirate", {"labware": "plate", "well": "A1", "volume_ul": 5})
        await session.finish(tmp_path / "session.json")

    anyio.run(scenario)
    return tmp_path / "session.json"


def sample(document):
    return next(iter(document["samples"].values()))


def test_the_public_tier_sees_what_happened_but_not_the_details(lab_log, tmp_path):
    release = release_lab_log(lab_log, POLICY, "public", tmp_path / "public.json", tmp_path / "k")
    record = sample(release.document)
    assert release.document["lab"] == "litmus-liquid-handling"
    assert [a["action"]["tool"] for a in record["actions"]] == ["read_volume", "aspirate"]
    assert [a["status"] for a in record["actions"]] == ["ran", "refused"]
    assert record["chain_sha256"] == sample(json.loads(lab_log.read_text()))["chain_sha256"]
    assert set(record["payload"]) == {"withheld", "sha256"}
    assert record["payload"]["withheld"] == "vetted"
    assert record["actions"][0]["action"]["arguments"]["withheld"] == "vetted"
    # Unclassified fields are withheld at the default (most restrictive) tier.
    assert record["collected_at"]["withheld"] == "restricted"


def test_the_vetted_tier_sees_more(lab_log, tmp_path):
    release = release_lab_log(lab_log, POLICY, "vetted", tmp_path / "audit.json", tmp_path / "k")
    record = sample(release.document)
    assert "withheld" not in record["payload"]
    assert record["actions"][1]["action"]["arguments"]["well"] == "A1"


def test_a_release_checks_against_the_full_lab_log(lab_log, tmp_path):
    release_lab_log(lab_log, POLICY, "public", tmp_path / "public.json", tmp_path / "k")
    assert verify_release(tmp_path / "public.json", lab_log, tmp_path / "k") == []
    assert os.stat(tmp_path / "k").st_mode & 0o777 == 0o600


def test_a_changed_release_is_caught(lab_log, tmp_path):
    release_lab_log(lab_log, POLICY, "public", tmp_path / "public.json", tmp_path / "k")
    document = json.loads((tmp_path / "public.json").read_text())
    record = sample(document["document"])
    record["actions"][1]["status"] = "ran"
    record["payload"]["sha256"] = "0" * 64
    (tmp_path / "public.json").write_text(json.dumps(document))
    problems = verify_release(tmp_path / "public.json", lab_log, tmp_path / "k")
    assert any("status differs" in p for p in problems)
    assert any("payload.sha256 differs" in p for p in problems)


def test_a_release_from_another_lab_log_or_key_is_caught(lab_log, tmp_path):
    release_lab_log(lab_log, POLICY, "public", tmp_path / "a.json", tmp_path / "ka")
    release_lab_log(lab_log, POLICY, "public", tmp_path / "b.json", tmp_path / "kb")
    problems = verify_release(tmp_path / "a.json", lab_log, tmp_path / "kb")
    assert "this key does not belong to the release" in problems
    other = tmp_path / "other.json"
    other.write_text(lab_log.read_text().replace("litmus-liquid-handling", "other-lab"))
    assert "the release was not cut from this lab log file" in verify_release(
        tmp_path / "a.json", other, tmp_path / "ka"
    )


def test_commitments_are_keyed(lab_log, tmp_path):
    first = release_lab_log(lab_log, POLICY, "public", tmp_path / "a.json", tmp_path / "ka")
    second = release_lab_log(lab_log, POLICY, "public", tmp_path / "b.json", tmp_path / "kb")
    # A different key per release, so withheld values can't be guessed or linked.
    assert sample(first.document)["payload"] != sample(second.document)["payload"]


def test_files_are_never_overwritten_and_tiers_are_checked(lab_log, tmp_path):
    release_lab_log(lab_log, POLICY, "public", tmp_path / "a.json", tmp_path / "k")
    with pytest.raises(FileExistsError):
        release_lab_log(lab_log, POLICY, "public", tmp_path / "a.json", tmp_path / "k2")
    with pytest.raises(ValueError, match="Unknown tier"):
        release_lab_log(lab_log, POLICY, "secret", tmp_path / "c.json", tmp_path / "k3")
    with pytest.raises(ValueError, match="Unknown tier"):
        ReleasePolicy(version="x", rules=(FieldRule(path="lab", tier="secret"),))


def test_the_most_specific_rule_wins():
    policy = ReleasePolicy(
        version="x",
        rules=(
            FieldRule(path="samples.*.payload", tier="restricted"),
            FieldRule(path="samples.*.payload.summary", tier="public"),
        ),
    )
    assert policy.tier_of(("samples", "s1", "payload", "summary")) == "public"
    assert policy.tier_of(("samples", "s1", "payload", "raw", "0")) == "restricted"
    assert policy.tier_of(("samples", "s1", "chain_sha256")) == "public"


def test_cli_releases_and_verifies(lab_log, tmp_path):
    import subprocess
    import sys

    policy = tmp_path / "policy.json"
    policy.write_text(POLICY.model_dump_json())
    cli = [sys.executable, "-m", "inspect_labs.cli"]
    subprocess.run(
        [*cli, "release", str(lab_log), "--policy", str(policy), "--tier", "public"]
        + ["--output", str(tmp_path / "public.json"), "--key", str(tmp_path / "public.key")],
        check=True,
        capture_output=True,
    )
    verified = subprocess.run(
        [*cli, "verify-release", str(tmp_path / "public.json")]
        + ["--lab-log", str(lab_log), "--key", str(tmp_path / "public.key")],
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0 and json.loads(verified.stdout)["faithful"]
