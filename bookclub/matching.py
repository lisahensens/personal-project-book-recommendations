"""Resolve the same book across members' exports into one canonical entry."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .ingest import READ, TO_READ, BookRecord

try:  # rapidfuzz is fast, but the fallback keeps the module importable without it.
    from rapidfuzz import fuzz

    def _ratio(left: str, right: str) -> float:
        return float(fuzz.token_sort_ratio(left, right))

except ImportError:  # pragma: no cover - exercised only without rapidfuzz installed
    from difflib import SequenceMatcher

    def _ratio(left: str, right: str) -> float:
        return SequenceMatcher(None, left, right).ratio() * 100

TITLE_THRESHOLD = 92.0
AUTHOR_THRESHOLD = 85.0

_PARENTHETICAL = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_NOISE_WORDS = re.compile(
    r"\b(a|an|the|edition|unabridged|abridged|illustrated|reprint|vol|volume|"
    r"book|novel|paperback|hardcover|boxed set|deluxe|anniversary)\b"
)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")

# Volume and series numbers are part of a book's identity: "Vol. 2" and "Vol. 27"
# are different books, so they must survive normalisation.
_VOLUME_PATTERNS = (
    re.compile(r"#\s*(\d+(?:\.\d+)?)"),
    re.compile(r"\bvol(?:ume)?\.?\s*(\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"\bbook\s*(\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"\bpart\s*(\d+(?:\.\d+)?)", re.IGNORECASE),
    # Trailing bare number, as used by many translated manga titles.
    re.compile(r"(\d+(?:\.\d+)?)\s*$"),
)


# Goodreads keeps the original series label on translated editions, so
# "Zeis (Arc of a Scythe, #1)" and "Scythe (Arc of a Scythe, #1)" are the same
# book even though the titles have nothing in common. A number range such as
# "(Harry Potter, #1-7)" fails this pattern on purpose: box sets are not one book.
_SERIES = re.compile(r"\(([^)]*?),?\s*#\s*(\d+(?:\.\d+)?)\s*\)")


def series_key(title: str, author: str) -> str | None:
    """Identify a book by its series slot, which survives translation."""
    match = _SERIES.search(title)
    if not match:
        return None
    name = _NON_ALNUM.sub(" ", match.group(1).lower())
    name = _SPACES.sub(" ", name).strip()
    surname = author_surname(author)
    if not name or not surname:
        return None
    return f"{name}|{match.group(2)}|{surname}"


def series_identity(title: str, author: str) -> tuple[str, float] | None:
    """The series a book belongs to, and its place in it.

    Unlike `series_key`, the number is kept separate so volumes of one series can
    be compared: that is what tells "book 1" apart from "book 4" and answers
    whether somebody has the earlier books already.
    """
    match = _SERIES.search(title)
    if not match:
        return None
    name = _NON_ALNUM.sub(" ", match.group(1).lower())
    name = _SPACES.sub(" ", name).strip()
    surname = author_surname(author)
    if not name or not surname:
        return None
    return f"{name}|{surname}", float(match.group(2))


def title_number(title: str) -> str:
    """Extract the volume/series number from a title, if it has one."""
    for pattern in _VOLUME_PATTERNS:
        match = pattern.search(title)
        if match:
            number = match.group(1)
            return number[:-2] if number.endswith(".0") else number
    return ""


def normalise_title_base(title: str) -> str:
    """Comparable title core with any volume/series number left off."""
    text = title.lower()
    text = _PARENTHETICAL.sub(" ", text)
    text = text.split(":")[0] if ":" in text else text
    text = _NON_ALNUM.sub(" ", text)
    text = _NOISE_WORDS.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def normalise_title(title: str) -> str:
    """Reduce a title to a comparable core, dropping series and edition noise."""
    number = title_number(title)
    text = normalise_title_base(title)
    return f"{text} #{number}".strip() if number else text


def normalise_author(author: str) -> str:
    """Normalise an author string, tolerating "Last, First" and multi-author fields."""
    primary = re.split(r"[,;&]| and ", author, maxsplit=1)
    text = author.lower()
    if "," in author:
        # "Yarros, Rebecca" -> compare on the surname, which is the stable part.
        text = primary[0].lower()
    text = _NON_ALNUM.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def author_surname(author: str) -> str:
    normalised = normalise_author(author)
    parts = normalised.split()
    return parts[-1] if parts else ""


def _isbn_keys(record: BookRecord) -> list[str]:
    keys = []
    for value in (record.isbn13, record.isbn):
        digits = re.sub(r"[^0-9Xx]", "", value or "").upper()
        if len(digits) in (10, 13):
            keys.append(digits)
    return keys


@dataclass
class CanonicalBook:
    """One book, plus every member's copy of it."""

    title: str
    author: str
    isbns: set[str] = field(default_factory=set)
    source_genres: set[str] = field(default_factory=set)
    genres: list[str] = field(default_factory=list)
    copies: dict[str, BookRecord] = field(default_factory=dict)
    work_key: str = ""  # Open Library work id, filled in by enrichment
    cover_id: int | None = None  # Open Library cover id, filled in by enrichment
    alternate_titles: set[str] = field(default_factory=set)

    @property
    def key(self) -> str:
        return f"{normalise_title(self.title)}|{author_surname(self.author)}"

    @property
    def series_key(self) -> str | None:
        return series_key(self.title, self.author)

    @property
    def base_key(self) -> str:
        """Key ignoring volume numbers, for comparing against sources that omit them."""
        return f"{normalise_title_base(self.title)}|{author_surname(self.author)}"

    @property
    def holders(self) -> set[str]:
        return set(self.copies)

    def people_on_shelf(self, shelf: str) -> set[str]:
        return {person for person, copy in self.copies.items() if copy.shelf == shelf}

    @property
    def to_read_by(self) -> set[str]:
        return self.people_on_shelf(TO_READ)

    @property
    def read_by(self) -> set[str]:
        return self.people_on_shelf(READ)

    @property
    def liked_by(self) -> set[str]:
        return {person for person, copy in self.copies.items() if copy.liked}

    @property
    def ratings(self) -> list[float]:
        return [c.rating for c in self.copies.values() if c.rating is not None]

    @property
    def average_rating(self) -> float | None:
        ratings = self.ratings
        return sum(ratings) / len(ratings) if ratings else None

    @property
    def all_genres(self) -> list[str]:
        merged = list(self.genres)
        for genre in sorted(self.source_genres):
            if genre not in merged:
                merged.append(genre)
        return merged

    def absorb(self, record: BookRecord) -> None:
        """Add a member's copy, keeping the richest metadata seen so far."""
        self.isbns.update(_isbn_keys(record))
        self.source_genres.update(g.strip() for g in record.source_genres if g.strip())

        # A translated edition matched on its series slot: remember the other
        # title so the club can see why two different names are one book.
        if normalise_title_base(record.title) != normalise_title_base(self.title):
            self.alternate_titles.add(record.title)

        if len(record.title) > len(self.title):
            self.alternate_titles.discard(record.title)
            if normalise_title_base(record.title) != normalise_title_base(self.title):
                self.alternate_titles.add(self.title)
            self.title = record.title
        if not self.author and record.author:
            self.author = record.author

        existing = self.copies.get(record.person)
        if existing is None or _shelf_rank(record.shelf) > _shelf_rank(existing.shelf):
            self.copies[record.person] = record


def _shelf_rank(shelf: str) -> int:
    """A member's "read" copy is more informative than a duplicate "to read"."""
    return {READ: 3, "reading": 2, TO_READ: 1}.get(shelf, 0)


class CollectiveLibrary:
    """Every book anyone in the club has listed, deduplicated across platforms."""

    def __init__(self) -> None:
        self.books: list[CanonicalBook] = []
        self._by_isbn: dict[str, CanonicalBook] = {}
        self._by_key: dict[str, CanonicalBook] = {}
        self._by_series: dict[str, CanonicalBook] = {}
        self._by_block: dict[str, list[CanonicalBook]] = {}
        self.people: list[str] = []

    def add(self, record: BookRecord) -> CanonicalBook:
        book = self._find(record)
        if book is None:
            book = CanonicalBook(title=record.title, author=record.author)
            self.books.append(book)
        book.absorb(record)
        self._index(book)
        if record.person not in self.people:
            self.people.append(record.person)
        return book

    def add_all(self, records: Iterable[BookRecord]) -> None:
        for record in records:
            self.add(record)

    def _index(self, book: CanonicalBook) -> None:
        for isbn in book.isbns:
            self._by_isbn[isbn] = book
        self._by_key[book.key] = book
        series = book.series_key
        if series:
            self._by_series.setdefault(series, book)
        block = self._block_key(book.title)
        bucket = self._by_block.setdefault(block, [])
        if book not in bucket:
            bucket.append(book)

    @staticmethod
    def _block_key(title: str) -> str:
        """Cheap blocking key so fuzzy comparison stays local, not quadratic."""
        return normalise_title(title)[:6]

    def _find(self, record: BookRecord) -> CanonicalBook | None:
        for isbn in _isbn_keys(record):
            match = self._by_isbn.get(isbn)
            if match is not None:
                return match

        # The series slot catches translated editions, whose titles are unrelated.
        series = series_key(record.title, record.author)
        if series:
            match = self._by_series.get(series)
            if match is not None:
                return match

        title = normalise_title(record.title)
        surname = author_surname(record.author)
        exact = self._by_key.get(f"{title}|{surname}")
        if exact is not None:
            return exact

        if not title:
            return None

        best: CanonicalBook | None = None
        best_score = 0.0
        number = title_number(record.title)
        base = normalise_title_base(record.title)
        for candidate in self._by_block.get(self._block_key(record.title), ()):
            candidate_number = title_number(candidate.title)
            if number and candidate_number:
                # Different volumes of one series read as near-identical strings,
                # so matching numbers are required rather than a high score alone.
                if number != candidate_number:
                    continue
            elif number or candidate_number:
                # One side omits the series number, as StoryGraph exports often do.
                # Only merge when the rest of the title is an exact match.
                if base != normalise_title_base(candidate.title):
                    continue
            # Numbers are handled above, so score the rest of the title.
            title_score = _ratio(base, normalise_title_base(candidate.title))
            if title_score < TITLE_THRESHOLD:
                continue
            author_score = _ratio(
                normalise_author(record.author), normalise_author(candidate.author)
            )
            same_surname = surname and surname == author_surname(candidate.author)
            if not same_surname and author_score < AUTHOR_THRESHOLD:
                continue
            score = title_score + author_score
            if score > best_score:
                best, best_score = candidate, score
        return best

    def shared_to_read(self, minimum_people: int = 2) -> list[CanonicalBook]:
        """Books on at least `minimum_people` members' to-read shelves."""
        shared = [b for b in self.books if len(b.to_read_by) >= minimum_people]
        return sorted(shared, key=lambda b: (-len(b.to_read_by), b.title.lower()))

    def books_missing_genres(self) -> list[CanonicalBook]:
        return [book for book in self.books if not book.all_genres]


def build_library(records: Iterable[BookRecord]) -> CollectiveLibrary:
    library = CollectiveLibrary()
    library.add_all(records)
    return library
