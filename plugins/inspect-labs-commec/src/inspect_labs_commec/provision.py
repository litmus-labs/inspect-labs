"""Record a pre-provisioned local database snapshot; never screen or download data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from inspect_labs.bindings import artifact_digest
from inspect_labs_commec.environment import RuntimeProfile, digest, private_write


def snapshot(databases: Path, image: str, output: Path) -> RuntimeProfile:
    """Hash installed database files and write an explicit trusted runtime profile.

    Args:
        databases: Existing database directory populated by upstream commec setup.
        image: Local Docker image ID, not a mutable tag.
        output: New private profile path. Existing artifacts are never overwritten.

    Returns:
        Runtime profile whose composition verifies the snapshot before evaluation.
    """
    root = databases.resolve(strict=True)
    manifest = root / "snapshot.json"
    if manifest.exists() or output.exists():
        raise FileExistsError("Use a new snapshot/profile path for changed databases")
    revisions = {}
    for component in ("biorisk", "best_match", "low_concern", "control_lists"):
        data = json.loads((root / component / "manifest.json").read_text())
        if data.get("component") != component or not isinstance(data.get("revision"), str):
            raise ValueError(f"Invalid native database manifest: {component}")
        revisions[component] = data["revision"]
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Snapshot must contain files, not external symlinks")
        if path.is_file():
            files[str(path.relative_to(root))] = artifact_digest(path)
    manifest_text = json.dumps({"revisions": revisions, "files": files}, indent=2)
    profile = RuntimeProfile(
        image=image,
        databases=str(root),
        revisions=revisions,
        snapshot_sha256=digest(manifest_text.encode()),
    )
    private_write(manifest, manifest_text)
    private_write(output, profile.model_dump_json(indent=2))
    return profile


def main() -> None:
    """Create provenance only; runtime installation remains an explicit operator step."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("databases", type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot(args.databases, args.image, args.output)
    print(f"Wrote private runtime profile: {args.output}")


if __name__ == "__main__":
    main()
