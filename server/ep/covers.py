"""Cover images, stored in the managed library (never next to files in watched dirs).

An image's file name carries a hash of its content, so a new image is a new path; the
version in an image's URL (``image_url``) is derived from the path, so the URL changes
whenever the image does and clients can cache it for good.
"""

import hashlib
import os
import tempfile
import uuid
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


def image_version(path: str) -> str:
    """The version of an entity's stored image (from its path: see the module doc)."""
    return hashlib.sha256(path.encode()).hexdigest()[:12]


def image_url(kind: str, entity_id: uuid.UUID, path: str | None) -> str | None:
    """``/covers/<kind>/<id>?v=<version>`` for an entity's image; None without one."""
    return f"/covers/{kind}/{entity_id}?v={image_version(path)}" if path else None


def write_image(kind: str, entity_id: uuid.UUID, data: bytes, library_dir: Path) -> Path:
    """Write ``data`` as the image of an entity (``kind``: ``release-group``,
    ``radio-show``, ``radio-episode``) under the covers dir and return its path.

    Raises ValueError if the data is not a JPEG, PNG or WebP image.
    """
    if (ext := image_extension(data)) is None:
        raise ValueError("cover must be a JPEG, PNG or WebP image")
    directory = covers_dir(library_dir) / kind
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()[:16]
    path = directory / f"{entity_id}-{digest}{ext}"
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as tmp:
        tmp.write(data)
    os.chmod(tmp.name, 0o644)  # NamedTemporaryFile creates it 0600
    os.replace(tmp.name, path)
    return path


def replace_image(
    kind: str, entity_id: uuid.UUID, data: bytes, old_path: str | None, library_dir: Path
) -> str:
    """``write_image``, removing the entity's previous image if it had another path."""
    path = write_image(kind, entity_id, data, library_dir)
    if old_path and Path(old_path) != path:
        remove_cover_file(old_path, library_dir)
    return str(path)


def set_release_group_cover(release_group: ReleaseGroup, data: bytes, library_dir: Path) -> None:
    """Write ``data`` as the release group's cover and point ``cover_path`` at it.

    Raises ValueError if the data is not a JPEG, PNG or WebP image.
    """
    release_group.cover_path = replace_image(
        "release-group", release_group.id, data, release_group.cover_path, library_dir
    )


def remove_cover_file(cover_path: str, library_dir: Path) -> None:
    """Delete a cover file, but only one EP wrote (under the library's covers dir)."""
    path = Path(cover_path)
    if path.is_relative_to(covers_dir(library_dir)):
        path.unlink(missing_ok=True)
