"""Club statistics: shelf overlap, shared genres and shared authors."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import combinations

from .enrichment import canonical_genre
from .ingest import READ, TO_READ
from .matching import CanonicalBook, CollectiveLibrary, author_surname


@dataclass
class MemberSummary:
    person: str
    total_books: int
    to_read: int
    read: int
    rated: int
    average_rating: float | None
    top_genres: list[tuple[str, int]]
    top_authors: list[tuple[str, int]]


@dataclass
class OverlapPair:
    left: str
    right: str
    shared_to_read: int
    shared_any_shelf: int
    genre_similarity: float
    shared_authors: int


def _display_author(book: CanonicalBook) -> str:
    return book.author.strip() or "Unknown author"


def member_summaries(library: CollectiveLibrary, top_n: int = 8) -> list[MemberSummary]:
    """Headline numbers per member, including their most common genres/authors."""
    summaries = []
    for person in library.people:
        copies = [b.copies[person] for b in library.books if person in b.copies]
        ratings = [c.rating for c in copies if c.rating is not None]
        genres: Counter[str] = Counter()
        authors: Counter[str] = Counter()
        for book in library.books:
            if person not in book.copies:
                continue
            for genre in book.all_genres:
                genres[canonical_genre(genre)] += 1
            authors[_display_author(book)] += 1

        summaries.append(
            MemberSummary(
                person=person,
                total_books=len(copies),
                to_read=sum(1 for c in copies if c.shelf == TO_READ),
                read=sum(1 for c in copies if c.shelf == READ),
                rated=len(ratings),
                average_rating=sum(ratings) / len(ratings) if ratings else None,
                top_genres=genres.most_common(top_n),
                top_authors=authors.most_common(top_n),
            )
        )
    return summaries


def genre_counts_by_person(library: CollectiveLibrary) -> dict[str, Counter[str]]:
    counts: dict[str, Counter[str]] = {p: Counter() for p in library.people}
    for book in library.books:
        genres = [canonical_genre(g) for g in book.all_genres]
        for person in book.copies:
            counts[person].update(genres)
    return counts


def author_counts_by_person(library: CollectiveLibrary) -> dict[str, Counter[str]]:
    counts: dict[str, Counter[str]] = {p: Counter() for p in library.people}
    for book in library.books:
        author = _display_author(book)
        for person in book.copies:
            counts[person][author] += 1
    return counts


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def overlap_pairs(library: CollectiveLibrary, top_genres: int = 20) -> list[OverlapPair]:
    """Pairwise similarity between members, on shelves, genres and authors."""
    genre_counts = genre_counts_by_person(library)
    author_counts = author_counts_by_person(library)
    top_genre_sets = {
        person: {genre for genre, _ in counts.most_common(top_genres)}
        for person, counts in genre_counts.items()
    }

    pairs = []
    for left, right in combinations(library.people, 2):
        shared_to_read = sum(
            1
            for book in library.books
            if {left, right} <= book.to_read_by
        )
        shared_any = sum(
            1 for book in library.books if {left, right} <= book.holders
        )
        shared_authors = len(set(author_counts[left]) & set(author_counts[right]))
        pairs.append(
            OverlapPair(
                left=left,
                right=right,
                shared_to_read=shared_to_read,
                shared_any_shelf=shared_any,
                genre_similarity=round(
                    _jaccard(top_genre_sets[left], top_genre_sets[right]), 3
                ),
                shared_authors=shared_authors,
            )
        )
    return sorted(pairs, key=lambda p: -p.shared_to_read)


def shared_genres(
    library: CollectiveLibrary, minimum_people: int = 2, top_n: int = 25
) -> list[tuple[str, int, list[str]]]:
    """Genres that show up in several members' libraries, most shared first."""
    counts = genre_counts_by_person(library)
    people_by_genre: dict[str, list[str]] = {}
    for person, genre_counter in counts.items():
        for genre in genre_counter:
            people_by_genre.setdefault(genre, []).append(person)

    rows = [
        (genre, sum(counts[p][genre] for p in people), sorted(people))
        for genre, people in people_by_genre.items()
        if len(people) >= minimum_people
    ]
    rows.sort(key=lambda row: (-len(row[2]), -row[1], row[0].lower()))
    return rows[:top_n]


def shared_authors(
    library: CollectiveLibrary, minimum_people: int = 2, top_n: int = 25
) -> list[tuple[str, int, list[str]]]:
    """Authors appearing on multiple members' shelves."""
    counts = author_counts_by_person(library)
    people_by_author: dict[str, list[str]] = {}
    for person, author_counter in counts.items():
        for author in author_counter:
            if author == "Unknown author":
                continue
            people_by_author.setdefault(author, []).append(person)

    rows = [
        (author, sum(counts[p][author] for p in people), sorted(people))
        for author, people in people_by_author.items()
        if len(people) >= minimum_people
    ]
    rows.sort(key=lambda row: (-len(row[2]), -row[1], row[0].lower()))
    return rows[:top_n]


def club_totals(library: CollectiveLibrary) -> dict[str, int]:
    to_read_books = [b for b in library.books if b.to_read_by]
    return {
        "members": len(library.people),
        "unique_books": len(library.books),
        "unique_to_read": len(to_read_books),
        "shared_to_read": len(library.shared_to_read()),
        "books_with_genres": sum(1 for b in library.books if b.all_genres),
        "distinct_authors": len(
            {author_surname(b.author) for b in library.books if b.author}
        ),
    }
