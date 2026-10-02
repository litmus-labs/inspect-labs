"""Advisory device claims: a second evaluation fails instead of double-driving hardware.

A claim is an exclusive ``flock`` on a per-user lock file and vanishes with the
process. It guards against your own concurrent evaluations; it is not a security
boundary, and it does not coordinate different users, hosts or lab schedulers.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import stat
import tempfile
from pathlib import Path


class DeviceBusy(RuntimeError):
    """Another evaluation in this user session already holds the device."""


def _lock_directory() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    directory = Path(base) / f"inspect-labs-devices-{os.getuid()}"
    directory.mkdir(mode=0o700, exist_ok=True)
    _validate_directory(directory)
    return directory


def _validate_directory(directory: Path) -> None:
    declaration = directory.lstat()
    if not stat.S_ISDIR(declaration.st_mode) or declaration.st_uid != os.getuid():
        raise PermissionError(
            f"Refusing a device lock directory that is not an owned directory: {directory}"
        )


def normalize(kind: str, identity: str) -> str:
    """Canonical device identity: resolved paths for device files, verbatim otherwise."""
    if kind in {"serial", "usb"}:
        return f"{kind}:{Path(identity).resolve()}"
    return f"{kind}:{identity.strip().lower()}"


class DeviceClaim:
    """Exclusive advisory claim over one or more devices, released on `release()`.

    Args:
        devices: ``(kind, identity)`` pairs such as ``("http", "10.0.0.5:31950")``.

    Raises:
        DeviceBusy: Another open claim holds one of the devices.
    """

    def __init__(self, devices: tuple[tuple[str, str], ...]) -> None:
        self._handles: list[int] = []
        if devices:
            self._acquire(_lock_directory(), {normalize(*device) for device in devices}, 32)

    @classmethod
    def for_inspect_robots(cls, devices: tuple[tuple[str, str], ...]) -> DeviceClaim:
        """Claim native robot identities using the pinned Inspect Robots lock protocol.

        Compatible with commit 3c832c34b6c11fa5205ff80ab4947247fedd5eea, not a
        public upstream API. Serial/video paths resolve; CAN names stay verbatim.
        Unlike the native CLI helper, every locking failure refuses admission.
        See THIRD_PARTY_NOTICES.md for protocol attribution and its MIT notice.

        Args:
            devices: Explicit native (serial, v4l2 or can) device identities.

        Returns:
            Claim held until release. Never deletes native lock files.

        Raises:
            ValueError: No identities, invalid kinds, or blank identities.
            OSError: A claim cannot safely be acquired.
            DeviceBusy: A native or Labs evaluation already holds a device.
        """
        if not devices:
            raise ValueError("Native robot claims require explicit device identities")
        keys = set()
        for kind, identity in devices:
            if kind not in {"serial", "v4l2", "can"} or not identity.strip():
                raise ValueError("Invalid native robot device identity")
            keys.add(str(Path(identity).resolve()) if kind in {"serial", "v4l2"} else identity)
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        base = (
            Path(runtime)
            if runtime
            else Path(tempfile.gettempdir()) / f"inspect-robots-{os.getuid()}"
        )
        directory = base / "inspect-robots" / "locks"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        _validate_directory(directory)
        claim = cls(())
        claim._acquire(directory, keys, 16)
        return claim

    def _acquire(self, directory: Path, keys: set[str], digest_length: int) -> None:
        try:
            for key in sorted(keys):
                name = hashlib.sha256(key.encode()).hexdigest()[:digest_length] + ".lock"
                handle = os.open(directory / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                self._handles.append(handle)
                info = os.fstat(handle)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                    raise PermissionError(f"Refusing an unowned or non-file device lock: {name}")
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise DeviceBusy(f"Device {key} is claimed by another evaluation") from exc
                os.ftruncate(handle, 0)
                os.write(handle, f"{os.getpid()} {key}\n".encode())
        except BaseException:
            self.release()
            raise

    def release(self) -> None:
        """Release every held device."""
        while self._handles:
            handle = self._handles.pop()
            fcntl.flock(handle, fcntl.LOCK_UN)
            os.close(handle)
