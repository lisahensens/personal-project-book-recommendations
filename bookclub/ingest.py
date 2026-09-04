"""Read Goodreads and StoryGraph CSV exports into one shared schema."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

TO_READ = "to_read"
READING = "reading"
READ = "read"
DID_NOT_FINISH = "did_not_finish"

GOODREADS = "goodreads"
STORYGRAPH = "storygraph"

# Goodreads writes ISBNs as Excel formulas, e.g. ="9781682818527" or ="".
_EXCEL_FORMULA = re.compile(r'^\s*=?"*(?P<value>[^"]*)"*\s*$')

_SHELF_ALIASES = {
    "read": READ,
    "to-read": TO_READ,
    "to read": TO_READ,
    "want to read": TO_READ,
    "currently-reading": READING,
    "currently reading": READING,
    "did-not-finish": DID_NOT_FINISH,
    "did not finish": DID_NOT_FINISH,
    "dnf": DID_NOT_FINISH,
}

# Shelf names members invent that really mean "I want to read this".
_TO_READ_HINTS = ("to-read", "to read", "tbr", "wishlist", "want-to-read", "want to read")


@dataclass
class BookRecord:
    """One row of one member's export, normalised."""

    person: str
    title: str
    author: str
    isbn: str = ""
    isbn13: str = ""
    shelf: str = TO_READ
    rating: float | None = None
    source_genres: list[str] = field(default_factory=list)
    date_added: str = ""
    source: str = GOODREADS
    extra_shelves: list[str] = field(default_factory=list)

    @property
    def liked(self) -> bool:
        """True when the member clearly enjoyed the book (4+ stars)."""
        return self.rating is not None and self.rating >= 4.0


class UnknownExportFormat(ValueError):
    """Raised when a CSV's headers match neither supported platform."""


def _clean(value: str | None) -> str:
    if value is None:
        return ""
    match = _EXCEL_FORMULA.match(str(value))
    text = match.group("value") if match else str(value)
    return text.strip()


def _parse_rating(value: str | None) -> float | None:
    text = _clean(value)
    if not text:
        return None
    try:
        rating = float(text)
    except ValueError:
        return None
    # Both platforms use 0 to mean "not rated".
    return rating if rating > 0 else None


def _split_list(value: str | None) -> list[str]:
    text = _clean(value)
    if not text:
        return []
    parts = re.split(r"[,;|]", text)
    return [part.strip() for part in parts if part.strip()]


def _normalise_shelf(value: str | None) -> str:
    text = _clean(value).lower()
    if not text:
        return TO_READ
    if text in _SHELF_ALIASES:
        return _SHELF_ALIASES[text]
    if any(hint in text for hint in _TO_READ_HINTS):
        return TO_READ
    if "read" in text:
        return READ
    return TO_READ


def detect_format(fieldnames: Iterable[str]) -> str:
    """Identify the exporting platform from a CSV's header row."""
    headers = {name.strip().lower() for name in fieldnames if name}
    if "exclusive shelf" in headers or "bookshelves" in headers:
        return GOODREADS
    if "read status" in headers or "star rating" in headers:
        return STORYGRAPH
    raise UnknownExportFormat(
        "CSV headers match neither a Goodreads nor a StoryGraph export. "
        f"Found columns: {sorted(headers)}"
    )


def _row_getter(row: dict[str, str]):
    lookup = {(key or "").strip().lower(): value for key, value in row.items()}

    def get(*names: str) -> str:
        for name in names:
            if name in lookup:
                value = _clean(lookup[name])
                if value:
                    return value
        return ""

    return get


def _goodreads_record(row: dict[str, str], person: str) -> BookRecord | None:
    get = _row_getter(row)
    title = get("title")
    if not title:
        return None

    shelves = _split_list(get("bookshelves"))
    exclusive = get("exclusive shelf")
    return BookRecord(
        person=person,
        title=title,
        author=get("author", "author l-f"),
        isbn=get("isbn"),
        isbn13=get("isbn13"),
        shelf=_normalise_shelf(exclusive or (shelves[0] if shelves else "")),
        rating=_parse_rating(row.get("My Rating")),
        source_genres=[],  # Goodreads exports carry no genre data.
        date_added=get("date added"),
        source=GOODREADS,
        extra_shelves=shelves,
    )


def _storygraph_record(row: dict[str, str], person: str) -> BookRecord | None:
    get = _row_getter(row)
    title = get("title")
    if not title:
        return None

    identifier = get("isbn/uid", "isbn", "isbn13")
    digits = re.sub(r"[^0-9Xx]", "", identifier)
    isbn13 = identifier if len(digits) == 13 else ""
    isbn = identifier if len(digits) == 10 else ""

    # StoryGraph exports vary by vintage: some have a Genres column, some only Tags.
    genres = _split_list(get("genres")) or _split_list(get("tags"))
    return BookRecord(
        person=person,
        title=title,
        author=get("authors", "author", "contributors"),
        isbn=isbn,
        isbn13=isbn13,
        shelf=_normalise_shelf(get("read status")),
        rating=_parse_rating(row.get("Star Rating")),
        source_genres=genres,
        date_added=get("date added"),
        source=STORYGRAPH,
        extra_shelves=_split_list(get("tags")),
    )


def load_export(text: str, person: str) -> list[BookRecord]:
    """Parse one member's CSV export text into normalised records."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []

    platform = detect_format(reader.fieldnames)
    builder = _goodreads_record if platform == GOODREADS else _storygraph_record

    records = []
    for row in reader:
        record = builder(row, person)
        if record is not None:
            records.append(record)
    return records


def person_name_from_filename(filename: str) -> str:
    """Guess a member's name from an export filename."""
    stem = Path(filename).stem
    stem = re.sub(
        r"(goodreads|storygraph|library|export|books|shelf|shelves)",
        " ",
        stem,
        flags=re.IGNORECASE,
    )
    stem = re.sub(r"[_\-.]+", " ", stem)
    stem = re.sub(r"\d{4}[-/]\d{2}[-/]\d{2}|\d+", " ", stem)
    name = " ".join(part for part in stem.split() if part)
    return name.title() if name else Path(filename).stem


def load_export_file(path: str | Path, person: str | None = None) -> list[BookRecord]:
    """Parse an export from disk, inferring the member's name if not given."""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return load_export(text, person or person_name_from_filename(path.name))
