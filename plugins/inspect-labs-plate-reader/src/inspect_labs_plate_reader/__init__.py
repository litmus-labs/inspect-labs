"""A device-free Inspect workflow through PyLabRobot's plate-reader API."""

from inspect_labs_plate_reader.tasks import absorbance_qc, qc_outcome

__all__ = ["absorbance_qc", "qc_outcome"]
