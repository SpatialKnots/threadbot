from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType
from typing import BinaryIO


class SingleInstanceLockError(RuntimeError):
    pass


_held_lock_paths: set[Path] = set()


class SingleInstanceLock:
    def __init__(self, lock_path: str | Path) -> None:
        self.lock_path = Path(lock_path).resolve()
        self._file: BinaryIO | None = None

    def __enter__(self) -> SingleInstanceLock:
        if self.lock_path in _held_lock_paths:
            raise SingleInstanceLockError(_message(self.lock_path))

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.lock_path.open("a+b")
        try:
            _lock_file(lock_file)
        except OSError as exc:
            lock_file.close()
            raise SingleInstanceLockError(_message(self.lock_path)) from exc

        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(str(os.getpid()).encode("ascii"))
        lock_file.flush()
        _held_lock_paths.add(self.lock_path)
        self._file = lock_file
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._file is None:
            return
        try:
            _unlock_file(self._file)
        finally:
            self._file.close()
            self._file = None
            _held_lock_paths.discard(self.lock_path)


def _message(lock_path: Path) -> str:
    return (
        "Another Thread Search Bot instance is already running on this machine. "
        f"Stop it before starting a new one. Lock file: {lock_path}"
    )


if os.name == "nt":
    import msvcrt

    def _lock_file(lock_file: BinaryIO) -> None:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_file(lock_file: BinaryIO) -> None:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_file(lock_file: BinaryIO) -> None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(lock_file: BinaryIO) -> None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
