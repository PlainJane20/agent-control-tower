"""
Tiny advisory file lock (fcntl.flock) used by ledger, audit and approval.
On platforms without fcntl (e.g. Windows) it degrades to a no-op: the
read-modify-write paths are then NOT safe against concurrent writers.
flock is advisory and per-host; it does not protect against other
processes that ignore it, nor reliably on network filesystems.
"""

import contextlib
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None

LOCKING_AVAILABLE = fcntl is not None


@contextlib.contextmanager
def file_lock(lock_path: Path):
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as f:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(f, fcntl.LOCK_UN)
