"""HTTP for web pages and images other than MusicBrainz (Bandcamp pages, cover images).

Requests carry an app-name User-Agent without contact details (those are for
MusicBrainz only, ``ep.metadata.musicbrainz``).
"""

from importlib.metadata import version

import httpx2

USER_AGENT = f"EP/{version('ep')}"
TIMEOUT = 30.0
# Larger responses are refused (a cover image, a web page).
MAX_BYTES = 30 * 1024 * 1024


class WebError(Exception):
    """A page or image could not be fetched."""


class WebNotFound(WebError):
    """404 or 410: the page is gone."""


def client(transport: httpx2.BaseTransport | None = None) -> httpx2.Client:
    return httpx2.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
        follow_redirects=True,
        transport=transport,
    )


def get_bytes(http: httpx2.Client, url: str) -> bytes:
    """The body of a 200 response; WebNotFound on 404/410, WebError otherwise."""
    try:
        with http.stream("GET", url) as response:
            if response.status_code in (404, 410):
                raise WebNotFound(f"{url}: not found ({response.status_code})")
            if response.status_code != 200:
                raise WebError(f"{url}: HTTP {response.status_code}")
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise WebError(f"{url}: larger than {MAX_BYTES} bytes")
                chunks.append(chunk)
            return b"".join(chunks)
    except httpx2.HTTPError as e:
        raise WebError(f"{url}: {e}") from e


def fetch(url: str, transport: httpx2.BaseTransport | None = None) -> bytes:
    with client(transport) as http:
        return get_bytes(http, url)
