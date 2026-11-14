"""Normalising names and titles for comparison.

``normalize`` lowercases, removes accents and punctuation and collapses whitespace.
``normalize_title`` also drops featured artists and edition suffixes ("(Remastered
2011)", " - 2009 Remaster", "[Deluxe Edition]"); ``normalize_artist`` drops featured
artists and a leading or trailing "The".
"""

import re
import unicodedata

# Letters that do not decompose into a base letter plus accents (as Postgres unaccent).
_LETTERS = str.maketrans(
    {"ø": "o", "đ": "d", "ł": "l", "ħ": "h", "\u0131": "i", "æ": "ae", "œ": "oe", "þ": "th"}
)

_FEAT = r"(?:feat|ft|featuring)\b\.?"
# "(feat. X)", "[ft. X]"
_FEAT_BRACKETED = re.compile(rf"[(\[]\s*{_FEAT}[^)\]]*[)\]]", re.IGNORECASE)
# " feat. X" up to the end, or up to an opening bracket.
_FEAT_TRAILING = re.compile(rf"\s{_FEAT}\s[^(\[]*", re.IGNORECASE)

# Words that mark an edition rather than a different recording or release group.
_EDITION = (
    r"\b(?:\d{4}\s+)?(?:re-?master(?:ed)?|deluxe|expanded|anniversary|edition|bonus\s+track"
    r"|explicit|clean|mono|stereo)\b"
)
# Words that mark a different recording: a part containing one is never stripped, even
# with an edition word in it ("(Live, 2011 Remaster)", " - Radio Edit / Mono").
_DIFFERENT = (
    r"\b(?:live|remix(?:ed)?|mix|edit|demo|acoustic|instrumental|unplugged|session|take"
    r"|rehearsal|karaoke|a\s?cappella|reprise|dub)\b"
)
_KEEP = rf"(?![^)\]]*{_DIFFERENT})"
# "(Remastered 2011)", "[Deluxe Edition]"
_EDITION_BRACKETED = re.compile(rf"[(\[]{_KEEP}[^)\]]*{_EDITION}[^)\]]*[)\]]", re.IGNORECASE)
# " - Remastered 2011", " - 2009 Remaster", " - Mono" up to the end (hyphen, en or em dash).
_EDITION_DASHED = re.compile(
    rf"\s[-\u2013\u2014]\s(?!.*{_DIFFERENT})[^-\u2013\u2014]*{_EDITION}.*$", re.IGNORECASE
)

# After normalize: "The Beatles" -> "the beatles", "Beatles, The" -> "beatles the".
_THE = re.compile(r"^the | the$")
_NON_WORD = re.compile(r"[\W_]+")


def unaccent(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold().translate(_LETTERS))
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def normalize(text: str) -> str:
    """Lowercase, without accents, "&" as "and", punctuation as spaces, single spaces."""
    text = unaccent(text).replace("&", " and ")
    return _NON_WORD.sub(" ", text).strip()


def strip_featuring(text: str) -> str:
    return _FEAT_TRAILING.sub(" ", _FEAT_BRACKETED.sub(" ", text))


def strip_edition(title: str) -> str:
    return _EDITION_DASHED.sub("", _EDITION_BRACKETED.sub(" ", title))


def normalize_title(title: str) -> str:
    """A recording or release group title for comparison.

    Falls back to the plain normalised title if stripping would leave nothing (a song
    called "Mono").
    """
    return normalize(strip_edition(strip_featuring(title))) or normalize(title)


def normalize_artist(name: str) -> str:
    """An artist name or credit for comparison: no featured artists, no "The"."""
    normalized = normalize(strip_featuring(name)) or normalize(name)
    return _THE.sub("", normalized) or normalized
