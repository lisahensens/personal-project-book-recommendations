"""The playful side: genre icons, cover images and club awards."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass

from .enrichment import canonical_genre
from .matching import CanonicalBook, CollectiveLibrary, series_key
from .stats import genre_counts_by_person, overlap_pairs

COVER_BASE = "https://covers.openlibrary.org/b"

GENRE_ICONS = {
    "Adventure": "🗺️",
    "Art": "🎨",
    "Biography": "👤",
    "Business": "💼",
    "Children's Fiction": "🧸",
    "Classics": "🏛️",
    "Cookbooks": "🍳",
    "Crime": "🚔",
    "Dystopian": "🏚️",
    "Erotica": "🔥",
    "Fantasy": "🐉",
    "Graphic Novels": "💥",
    "Health": "🥗",
    "Historical Fiction": "🏰",
    "History": "📜",
    "Horror": "👻",
    "Humour": "😂",
    "LGBTQ": "🏳️‍🌈",
    "Literary Fiction": "🖋️",
    "Magical Realism": "🦋",
    "Manga": "🇯🇵",
    "Memoir": "📔",
    "Music": "🎵",
    "Mystery": "🔍",
    "Nature": "🌿",
    "Nonfiction": "📗",
    "Philosophy": "🤔",
    "Poetry": "🌙",
    "Politics": "🏛️",
    "Popular Science": "🔬",
    "Psychology": "🧠",
    "Religion": "🕯️",
    "Romance": "💕",
    "Romantasy": "💞",
    "Science Fiction": "🚀",
    "Self Help": "🌱",
    "Short Stories": "✂️",
    "Sports": "⚽",
    "Thriller": "😱",
    "Travel": "✈️",
    "True Crime": "🔦",
    "War Fiction": "🎖️",
    "Western": "🤠",
    "Young Adult": "🌟",
}

DEFAULT_ICON = "📚"


def genre_icon(genre: str) -> str:
    return GENRE_ICONS.get(canonical_genre(genre), DEFAULT_ICON)


def genre_chip(genre: str) -> str:
    return f"{genre_icon(genre)} {genre}"


def cover_url(
    cover_id: int | None = None, isbn: str = "", size: str = "M"
) -> str | None:
    """Best available Open Library cover image, or None when there is nothing.

    A cover id is the reliable route. ISBN works for many editions but not all,
    and plenty of books simply have no cover on file, hence the None.
    """
    if cover_id:
        return f"{COVER_BASE}/id/{cover_id}-{size}.jpg"
    if isbn:
        return f"{COVER_BASE}/isbn/{isbn}-{size}.jpg"
    return None


def book_cover_url(book: CanonicalBook, size: str = "M") -> str | None:
    return cover_url(book.cover_id, next(iter(sorted(book.isbns)), ""), size)


# ------------------------------------------------------------------- awards


@dataclass
class Award:
    """A playful badge to hand out at the next meeting."""

    title: str
    icon: str
    winner: str
    detail: str


def _series_counts(library: CollectiveLibrary, person: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for book in library.books:
        if person not in book.copies:
            continue
        key = series_key(book.title, book.author)
        if key:
            counts[key.rsplit("|", 2)[0]] += 1
    return counts


def taste_match(library: CollectiveLibrary) -> list[tuple[str, str, int]]:
    """Genre overlap between each pair, as a friendly percentage."""
    return [
        (pair.left, pair.right, round(pair.genre_similarity * 100))
        for pair in sorted(overlap_pairs(library), key=lambda p: -p.genre_similarity)
    ]


def club_awards(library: CollectiveLibrary) -> list[Award]:
    """Badges derived from data already computed elsewhere."""
    awards: list[Award] = []
    people = library.people
    if not people:
        return awards

    genre_counts = genre_counts_by_person(library)

    matches = taste_match(library)
    if matches:
        left, right, percentage = matches[0]
        awards.append(
            Award(
                title="Genre twins",
                icon="👯",
                winner=f"{left} and {right}",
                detail=f"{percentage}% taste match on their top genres",
            )
        )

    # Most eclectic: distinct genres relative to how much has been labelled.
    eclectic = [
        (person, len(counts), sum(counts.values()))
        for person, counts in genre_counts.items()
        if sum(counts.values()) >= 5
    ]
    if eclectic:
        person, distinct, _ = max(eclectic, key=lambda row: row[1])
        awards.append(
            Award(
                title="Most eclectic reader",
                icon="🎭",
                winner=person,
                detail=f"reads across {distinct} different genres",
            )
        )

    tbr = {
        person: sum(1 for b in library.books if person in b.to_read_by)
        for person in people
    }
    deepest = max(tbr, key=lambda person: tbr[person])
    if tbr[deepest]:
        awards.append(
            Award(
                title="Deepest to-read pile",
                icon="🗼",
                winner=deepest,
                detail=f"{tbr[deepest]} books waiting patiently",
            )
        )

    finishers = []
    for person in people:
        owned = [b for b in library.books if person in b.copies]
        read = [b for b in owned if person in b.read_by]
        if len(owned) >= 5:
            finishers.append((person, len(read) / len(owned), len(read)))
    if finishers:
        person, share, read_count = max(finishers, key=lambda row: row[1])
        awards.append(
            Award(
                title="Actually finishes books",
                icon="✅",
                winner=person,
                detail=f"{round(share * 100)}% of their shelf read ({read_count} books)",
            )
        )

    devotees = []
    for person in people:
        counts = _series_counts(library, person)
        if counts:
            series, count = counts.most_common(1)[0]
            devotees.append((person, series, count))
    if devotees:
        person, series, count = max(devotees, key=lambda row: row[2])
        if count >= 3:
            awards.append(
                Award(
                    title="Series devotee",
                    icon="📖",
                    winner=person,
                    detail=f"{count} books of {series.title()}",
                )
            )

    generous = []
    for person in people:
        ratings = [
            b.copies[person].rating
            for b in library.books
            if person in b.copies and b.copies[person].rating is not None
        ]
        if len(ratings) >= 5:
            generous.append((person, sum(ratings) / len(ratings), len(ratings)))
    if generous:
        person, average, count = max(generous, key=lambda row: row[1])
        awards.append(
            Award(
                title="Kindest critic",
                icon="⭐",
                winner=person,
                detail=f"averages {average:.1f}/5 across {count} ratings",
            )
        )

    return awards


def spin_roulette(
    library: CollectiveLibrary, people: list[str] | None = None
) -> CanonicalBook | None:
    """Pick a book at random, preferring ones more than one member wants."""
    shared = library.shared_to_read()
    if people:
        joining = set(people)
        shared = [b for b in shared if len(b.to_read_by & joining) >= 2]
    pool = shared or [
        book
        for book in library.books
        if book.to_read_by & set(people or library.people)
    ]
    return random.choice(pool) if pool else None
