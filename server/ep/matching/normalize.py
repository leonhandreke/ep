"""Normalising names and titles for comparison.

``normalize`` lowercases, removes accents and punctuation and collapses whitespace.
``normalize_title`` also drops featured artists and edition suffixes ("(Remastered
2011)", " - 2009 Remaster", "[Deluxe Edition]"); ``normalize_artist`` drops featured
artists and a leading or trailing "The".

``normalize`` has a twin in the database, the SQL function ``ep_normalize`` (migration
0004), which the trigram indexes are built on. Both do the same steps with the same
Unicode operations (Postgres 18 ``casefold`` and ``normalize``), and
``tests/test_normalize_sql.py`` checks that they agree character by character, except
for combining marks that are not "Alphabetic" (viramas, ...), which Postgres turns into
spaces and Python keeps (queries are normalised by ``ep_normalize`` too, see
``ep.matching.sql``, so this does not affect matching). Change them together. The
title and artist rules (featured artists, editions, "The") exist only here: they apply
to the query and to the rapidfuzz scores, while the database compares the plainly
normalised stored text by word similarity, which finds a query in a longer value anyway
("here comes the sun" in "Here Comes the Sun (Remastered 2009)").
"""

import re
import unicodedata

# Letters that do not decompose into a base letter plus accents.
_LETTERS = str.maketrans(
    {
        "ø": "o",
        "đ": "d",
        "ð": "d",
        "ł": "l",
        "ħ": "h",
        "\u0131": "i",
        "æ": "ae",
        "œ": "oe",
        "þ": "th",
    }
)
# The accents: the block of combining diacritical marks, after decomposition. Other
# combining marks (Japanese dakuten, Indic vowel signs) are part of the letter.
_ACCENTS = re.compile("[\u0300-\u036f]+")

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


def unaccent(text: str) -> str:
    """Case-folded, compatibility-decomposed, accents removed, recomposed."""
    decomposed = unicodedata.normalize("NFKD", text.casefold().translate(_LETTERS))
    return unicodedata.normalize("NFC", _ACCENTS.sub("", decomposed))


def _is_word_char(char: str) -> bool:
    # Close to Postgres regular expressions' \w under the builtin collation (Unicode
    # "Alphabetic" and digits), which Python cannot query: letters, marks, decimal
    # digits and letter numbers.
    return unicodedata.category(char)[0] in "LM" or unicodedata.category(char) in ("Nd", "Nl")


def normalize(text: str) -> str:
    """Lowercase, without accents, "&" as "and", punctuation as spaces, single spaces."""
    text = unaccent(text).replace("&", " and ")
    return " ".join("".join(char if _is_word_char(char) else " " for char in text).split())


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
