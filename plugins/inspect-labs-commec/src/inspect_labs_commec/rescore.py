"""Installed pure replay surface; execution belongs to native Inspect."""

import argparse
from pathlib import Path

from inspect_labs.bindings import rescore
from inspect_labs_commec.tasks import METRICS, review_outcome


def main() -> None:
    """Rescore existing native logs and linked private evidence without dispatch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native_log", type=Path)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rescore(args.native_log, args.evidence, args.output, review_outcome, metrics=METRICS)
    print(f"Rescored saved evidence: {args.output}")


if __name__ == "__main__":
    main()
