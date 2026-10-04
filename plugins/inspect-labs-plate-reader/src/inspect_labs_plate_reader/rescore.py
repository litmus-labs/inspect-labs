"""Installed pure replay for linked plate-reader evidence."""

import argparse
from pathlib import Path

from inspect_labs.bindings import rescore
from inspect_labs_plate_reader.tasks import METRICS, qc_outcome


def main() -> None:
    """Rescore a saved native log without model or reader dispatch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native_log", type=Path)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    rescore(args.native_log, args.evidence, args.output, qc_outcome, metrics=METRICS)
    print(f"Rescored saved evidence: {args.output}")


if __name__ == "__main__":
    main()
