"""Cover images, stored in the managed library (never next to files in watched dirs)."""

import os
import tempfile
from pathlib import Path

from ep.models import ReleaseGroup

# Image types accepted as covers: magic bytes -> file extension.
_SIGNATURES = {b"\xff\xd8\xff": ".jpg", b"\x89PNG\r\n\x1a\n": ".png"}


def image_extension(data: bytes) -> str | None:
    """File extension for JPEG, PNG or WebP image data; None for anything else."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return next((ext for magic, ext in _SIGNATURES.items() if data.startswith(magic)), None)


def covers_dir(library_dir: Path) -> Path:
    return library_dir / "_covers"


def set_release_group_cover(release_group: ReleaseGroup, data: bytes, library_dir: Path) -> None:
    """Write ``data`` as the release group's cover and point ``cover_path`` at it.

    Raises ValueError if the data is not a JPEG, PNG or WebP image.
    """
    if (ext := image_extension(data)) is None:
        raise ValueError("cover must be a JPEG, PNG or WebP image")
    directory = covers_dir(library_dir) / "release-group"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{release_group.id}{ext}"
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as tmp:
        tmp.write(data)
    os.chmod(tmp.name, 0o644)  # NamedTemporaryFile creates it 0600
    os.replace(tmp.name, path)
    if release_group.cover_path and Path(release_group.cover_path) != path:
        remove_cover_file(release_group.cover_path, library_dir)
    release_group.cover_path = str(path)


def remove_cover_file(cover_path: str, library_dir: Path) -> None:
    """Delete a cover file, but only one EP wrote (under the library's covers dir)."""
    path = Path(cover_path)
    if path.is_relative_to(covers_dir(library_dir)):
        path.unlink(missing_ok=True)
