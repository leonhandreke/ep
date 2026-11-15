"""Paths on the host running the server, for clients on that host.

In Docker the server sees the managed library as ``/library`` and watched directories
under ``/watch``; a client on the host needs the host's paths (to show a file in the
Finder) and gives host paths (a folder dropped on it). ``EP_HOST_PATHS`` maps the
server's directories to the host's; without it the server runs on the host and the
paths are the same.
"""

from pathlib import Path, PurePath

from ep.settings import Settings


def _relative(path: PurePath, root: PurePath) -> PurePath | None:
    return path.relative_to(root) if path.is_relative_to(root) else None


def to_host(path: str | PurePath | None, settings: Settings) -> str | None:
    """The host path of a server path; None if it is in no mapped directory."""
    if path is None:
        return None
    path = PurePath(path)
    if not settings.host_paths:
        return str(path)
    for server, host in sorted(settings.host_paths.items(), key=lambda m: -len(m[0].parts)):
        if (rest := _relative(path, server)) is not None:
            return str(host / rest) if rest.parts else str(host)
    return None


def from_host(path_text: str, settings: Settings) -> str:
    """The server path of a host path in a mapped directory; any other path unchanged
    (it may be a server path already)."""
    path = PurePath(path_text)
    mappings = sorted(settings.host_paths.items(), key=lambda m: -len(m[1].parts))
    for server, host in mappings:
        if (rest := _relative(path, host)) is not None and _relative(path, server) is None:
            return str(Path(server) / rest) if rest.parts else str(server)
    return path_text
