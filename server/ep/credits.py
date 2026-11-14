"""Artist credits: rendering, and finding or creating a shared credit.

An artist credit is an ordered list of (artist, credited name, join phrase), e.g.
``[(Simon, "Simon", " & "), (Garfunkel, "Garfunkel", "")]`` renders as
"Simon & Garfunkel". Entities with the same list share one ``artist_credit`` row,
which is never edited in place: changing an entity's credit points it at another one.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import exists
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.interfaces import ORMOption
from sqlmodel import Session, col, select

from ep.models import Artist, ArtistCredit, ArtistCreditName, Recording, ReleaseGroup, rel


@dataclass(frozen=True)
class CreditPart:
    artist: Artist
    credited_name: str
    join_phrase: str = ""


def render_credit(parts: Sequence[CreditPart]) -> str:
    return "".join(part.credited_name + part.join_phrase for part in parts)


def get_or_create_artist_credit(session: Session, parts: Sequence[CreditPart]) -> ArtistCredit:
    """Return the credit for exactly these parts, adding a new one if none exists."""
    if not parts:
        raise ValueError("an artist credit needs at least one artist")
    name = render_credit(parts)
    wanted = [(part.artist.id, part.credited_name, part.join_phrase) for part in parts]
    for credit in session.exec(select(ArtistCredit).where(ArtistCredit.name == name)):
        if [(n.artist_id, n.credited_name, n.join_phrase) for n in credit.names] == wanted:
            return credit
    credit = ArtistCredit(name=name)
    credit.names = [
        ArtistCreditName(
            artist_credit_id=credit.id,
            artist_id=part.artist.id,
            position=position,
            credited_name=part.credited_name,
            join_phrase=part.join_phrase,
        )
        for position, part in enumerate(parts)
    ]
    session.add(credit)
    return credit


def with_credit(*path: object) -> ORMOption:
    """Load option: the many-to-one relationships ``path`` (ending in an artist credit)
    joined in, and the credit's names, for listing entities with their credits."""
    first, *rest = path
    option = joinedload(rel(first))
    for attribute in rest:
        option = option.joinedload(rel(attribute))
    return option.selectinload(rel(ArtistCredit.names))


def delete_artist_credit_if_unused(session: Session, credit: ArtistCredit) -> None:
    """Delete a credit that no release group or recording uses any more."""
    session.flush()
    used = exists().where(col(ReleaseGroup.artist_credit_id) == credit.id) | exists().where(
        col(Recording.artist_credit_id) == credit.id
    )
    if not session.scalar(select(used)):
        session.delete(credit)
