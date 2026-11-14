"""Which files EP may read: only those under the configured media roots.

The API has no auth, so a ``file`` source must not be able to point at an arbitrary
path (``/etc/passwd``) that ``/sources/{id}/stream`` would then serve.
"""

from collections.abc import Iterable
from pathlib import Path


def is_within_media_roots(path: Path, roots: Iterable[Path]) -> bool:
    """True if ``path``, with symlinks and ``..`` resolved, lies under one of ``roots``."""
    real = path.resolve()
    return any(real.is_relative_to(root.resolve()) for root in roots)
