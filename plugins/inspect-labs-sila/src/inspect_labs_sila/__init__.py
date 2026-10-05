"""A Lab backed by a mock SiLA 2 instrument, for native Inspect evaluations."""

from inspect_labs_sila.lab import SilaReaderLab, sila_mock_reader
from inspect_labs_sila.tasks import absorbance_read, read_outcome

__all__ = ["SilaReaderLab", "absorbance_read", "read_outcome", "sila_mock_reader"]
