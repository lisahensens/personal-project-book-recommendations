"""Taste profiles and book suggestions for the club.

Two pools feed the suggestion list:

1. The collective library - books at least one member already has, offered to the
   members who do not, so nobody needs everything on their own shelves.
2. New discoveries - books nobody has listed, pulled from Open Library subjects
   that match the club's blended taste.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .enrichment import OpenLibraryClient, SubjectCandidate, canonical_genre
from .ingest import READ, TO_READ
from .matching import (
    CanonicalBook,
    CollectiveLibrary,
    author_surname,
    series_identity,
)
from .preferences import MemberPreferences

# Stated preferences outrank inferred ones: a member who says "I love horror"
# should shape suggestions even with an almost empty shelf.
WEIGHT_STATED_GENRE = 3.0
WEIGHT_RATED_HIGH = 2.0
WEIGHT_READ_UNRATED = 1.0
WEIGHT_RATED_MIDDLING = 0.5
WEIGHT_RATED_LOW = -1.5
WEIGHT_TO_READ = 0.6
DISLIKE_PENALTY = 1.5

# A member sitting out no longer steers the genre profile, but their rating is
# still evidence about the book itself, so it keeps a reduced say. Without this
# their whole shelf would score zero and drop out of the pool.
OUTSIDE_ENDORSEMENT = 0.3

COLLECTIVE = "collective"
DISCOVERY = "discovery"

# What to do about books a member has already read. They can be the strongest
# recommendation ("she gave it five stars") or the worst pick for a shared read,
# so the club chooses which.
READ_FILTER_INCLUDE = "include"
READ_FILTER_PARTICIPANTS = "participants"
READ_FILTER_ANYONE = "anyone"
READ_FILTER_LABELS = {
    READ_FILTER_INCLUDE: "Include books someone has already read",
    READ_FILTER_PARTICIPANTS: "Skip books read by anyone joining the next book",
    READ_FILTER_ANYONE: "Skip books read by any member",
}


@dataclass
class FilterStats:
    """What the filters removed, so the club can see it was deliberate."""

    blocked_genres: int = 0
    already_read: int = 0
    mid_series: int = 0
    examples: list[str] = field(default_factory=list)

    def note(self, title: str) -> None:
        if len(self.examples) < 5:
            self.examples.append(title)


@dataclass
class TasteProfile:
    """Normalised genre and author affinities for one member or the whole club."""

    person: str
    genres: dict[str, float] = field(default_factory=dict)
    authors: dict[str, float] = field(default_factory=dict)
    disliked_genres: set[str] = field(default_factory=set)
    blocked_genres: set[str] = field(default_factory=set)
    book_count: int = 0
    stated_genres: list[str] = field(default_factory=list)

    def top_genres(self, limit: int = 10) -> list[tuple[str, float]]:
        ranked = sorted(self.genres.items(), key=lambda kv: -kv[1])
        return [(genre, weight) for genre, weight in ranked if weight > 0][:limit]

    def top_authors(self, limit: int = 10) -> list[tuple[str, float]]:
        ranked = sorted(self.authors.items(), key=lambda kv: -kv[1])
        return [(author, weight) for author, weight in ranked if weight > 0][:limit]


@dataclass
class Suggestion:
    """One recommended book, with the reasoning shown to the club."""

    title: str
    author: str
    source: str
    score: float
    genres: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    already_with: list[str] = field(default_factory=list)
    missing_for: list[str] = field(default_factory=list)
    average_rating: float | None = None
    first_publish_year: int | None = None
    cover_id: int | None = None
    isbn: str = ""
    alternate_titles: list[str] = field(default_factory=list)

    @property
    def source_label(self) -> str:
        return "In someone's library" if self.source == COLLECTIVE else "New discovery"

    @property
    def book_key(self) -> str:
        """Stable identifier for votes and shortlists."""
        return f"{self.title.strip().lower()}|{self.author.strip().lower()}"


def _rating_weight(rating: float | None, shelf: str) -> float:
    if shelf == TO_READ:
        return WEIGHT_TO_READ
    if rating is None:
        return WEIGHT_READ_UNRATED if shelf == READ else WEIGHT_TO_READ
    if rating >= 4:
        return WEIGHT_RATED_HIGH
    if rating >= 3:
        return WEIGHT_RATED_MIDDLING
    return WEIGHT_RATED_LOW


def _normalise(weights: dict[str, float]) -> dict[str, float]:
    """Scale weights so every member's profile carries the same total pull."""
    total = sum(w for w in weights.values() if w > 0)
    if total <= 0:
        return {key: 0.0 for key in weights}
    return {key: value / total for key, value in weights.items()}


def build_member_profile(
    library: CollectiveLibrary,
    person: str,
    preferences: MemberPreferences | None = None,
) -> TasteProfile:
    """Blend a member's stated genres with the signal in their shelves."""
    genres: dict[str, float] = defaultdict(float)
    authors: dict[str, float] = defaultdict(float)
    book_count = 0

    stated = list(preferences.liked_genres) if preferences else []
    for genre in stated:
        genres[canonical_genre(genre)] += WEIGHT_STATED_GENRE

    for book in library.books:
        copy = book.copies.get(person)
        if copy is None:
            continue
        book_count += 1
        weight = _rating_weight(copy.rating, copy.shelf)
        for genre in book.all_genres:
            genres[canonical_genre(genre)] += weight
        if book.author:
            authors[book.author.strip()] += weight

    disliked = {
        canonical_genre(g) for g in (preferences.disliked_genres if preferences else [])
    }
    blocked = {
        canonical_genre(g) for g in (preferences.blocked_genres if preferences else [])
    }
    return TasteProfile(
        person=person,
        genres=_normalise(dict(genres)),
        authors=_normalise(dict(authors)),
        disliked_genres=disliked,
        blocked_genres=blocked,
        book_count=book_count,
        stated_genres=stated,
    )


def build_profiles(
    library: CollectiveLibrary, preferences: dict[str, MemberPreferences] | None = None
) -> dict[str, TasteProfile]:
    preferences = preferences or {}
    return {
        person: build_member_profile(library, person, preferences.get(person))
        for person in library.people
    }


def build_group_profile(profiles: dict[str, TasteProfile]) -> TasteProfile:
    """Average the per-member profiles so small libraries count as much as big ones."""
    genres: dict[str, float] = defaultdict(float)
    authors: dict[str, float] = defaultdict(float)
    disliked: set[str] = set()
    blocked: set[str] = set()
    members = len(profiles) or 1

    for profile in profiles.values():
        for genre, weight in profile.genres.items():
            genres[genre] += weight / members
        for author, weight in profile.authors.items():
            authors[author] += weight / members
        disliked |= profile.disliked_genres
        blocked |= profile.blocked_genres

    return TasteProfile(
        person="Club",
        genres=dict(genres),
        authors=dict(authors),
        disliked_genres=disliked,
        blocked_genres=blocked,
        book_count=sum(p.book_count for p in profiles.values()),
    )


def genre_affinity(genres: list[str], profile: TasteProfile) -> float:
    """How well a set of genres matches a profile, damped by genre count."""
    canonical = [canonical_genre(g) for g in genres]
    if not canonical:
        return 0.0
    matched = sum(profile.genres.get(genre, 0.0) for genre in canonical)
    return matched / math.sqrt(len(canonical))


_NON_LATIN = re.compile(r"[\u0400-\u04ff\u0590-\u08ff\u3000-\u9fff\uac00-\ud7af]")


def _is_latin_script(title: str) -> bool:
    """Keep suggestions readable for a club that reads English and Dutch."""
    return not _NON_LATIN.search(title)


def _dislike_penalty(genres: list[str], profiles: dict[str, TasteProfile]) -> float:
    if not profiles:
        return 0.0
    canonical = {canonical_genre(g) for g in genres}
    hits = sum(
        1 for profile in profiles.values() if canonical & profile.disliked_genres
    )
    return DISLIKE_PENALTY * hits / len(profiles)


def blocked_genre_set(profiles: dict[str, TasteProfile]) -> set[str]:
    """Genres somebody flatly refuses. One veto is enough for a shared read."""
    blocked: set[str] = set()
    for profile in profiles.values():
        blocked |= profile.blocked_genres
    return blocked


def _is_blocked(genres: list[str], blocked: set[str]) -> bool:
    if not blocked:
        return False
    return bool({canonical_genre(g) for g in genres} & blocked)


def _series_position(book: CanonicalBook) -> float:
    """Which volume of its series this is, or 1 for a standalone."""
    identity = series_identity(book.title, book.author)
    return identity[1] if identity else 1.0


def _shelved_series(library: CollectiveLibrary) -> dict[str, set[float]]:
    """Which volumes of which series each member has, for judging series order."""
    shelved: dict[str, set[float]] = defaultdict(set)
    for book in library.books:
        identity = series_identity(book.title, book.author)
        if identity is None:
            continue
        series, number = identity
        for person in book.copies:
            shelved[f"{person}|{series}"].add(number)
    return shelved


def _has_earlier_volumes(
    book: CanonicalBook, person: str, shelved: dict[str, set[float]]
) -> bool:
    """Whether this person is far enough into a series to be offered this book.

    Standalones and first volumes are always fine. Later volumes need every
    earlier number to be on that person's shelves, so a reader who has books one
    and two gets offered three, but not five.
    """
    identity = series_identity(book.title, book.author)
    if identity is None:
        return True
    series, number = identity
    if number <= 1:
        return True
    owned = shelved.get(f"{person}|{series}", set())
    return all(any(abs(n - wanted) < 0.5 for n in owned) for wanted in range(1, int(number)))


def _endorsement(book: CanonicalBook, joining: list[str]) -> float:
    """How strongly the club vouches for a book, weighted towards participants."""
    joining_set = set(joining)
    score = 0.0
    for person in book.liked_by:
        score += 1.0 if person in joining_set else OUTSIDE_ENDORSEMENT
    for person in book.to_read_by:
        score += 0.5 if person in joining_set else OUTSIDE_ENDORSEMENT / 2
    return score / max(len(joining), 1)


def _fails_read_filter(
    book: CanonicalBook, read_filter: str, participants: list[str]
) -> bool:
    if read_filter == READ_FILTER_ANYONE:
        return bool(book.read_by)
    if read_filter == READ_FILTER_PARTICIPANTS:
        return bool(book.read_by & set(participants))
    return False


def _genre_reason(genres: list[str], profile: TasteProfile, limit: int = 3) -> str:
    ranked = sorted(
        ({canonical_genre(g) for g in genres}),
        key=lambda genre: -profile.genres.get(genre, 0.0),
    )
    liked = [genre for genre in ranked if profile.genres.get(genre, 0.0) > 0][:limit]
    if not liked:
        return ""
    return "Matches genres the club likes: " + ", ".join(liked)


def suggest_from_library(
    library: CollectiveLibrary,
    profiles: dict[str, TasteProfile],
    group: TasteProfile,
    limit: int = 30,
    max_per_author: int = 2,
    participants: list[str] | None = None,
    read_filter: str = READ_FILTER_INCLUDE,
    stats: FilterStats | None = None,
) -> list[Suggestion]:
    """Recommend books someone in the club already has, to the members who don't."""
    joining = list(participants) if participants else list(library.people)
    stats = stats if stats is not None else FilterStats()
    blocked = blocked_genre_set(profiles)
    shelved_series = _shelved_series(library)
    suggestions: list[Suggestion] = []

    for book in library.books:
        missing = [person for person in joining if person not in book.copies]
        if not missing:
            continue  # everyone taking part already has it

        if _is_blocked(book.all_genres, blocked):
            stats.blocked_genres += 1
            stats.note(book.title)
            continue

        if _fails_read_filter(book, read_filter, joining):
            stats.already_read += 1
            continue

        # Book four of a series is no use to someone who has not got the first
        # three, so it is only offered to members who are already that far in.
        ready = [
            person for person in missing if _has_earlier_volumes(book, person, shelved_series)
        ]
        if not ready:
            if _series_position(book) > 1:
                stats.mid_series += 1
                stats.note(book.title)
            continue
        missing = ready

        reasons: list[str] = []
        liked_by = sorted(book.liked_by)
        to_read_by = sorted(book.to_read_by)
        read_by = sorted(book.read_by)
        endorsement = _endorsement(book, joining)

        if liked_by:
            ratings = ", ".join(
                f"{person} rated it {book.copies[person].rating:.0f}/5"
                for person in liked_by
                if book.copies[person].rating is not None
            )
            reasons.append(ratings or f"Loved by {', '.join(liked_by)}")
        elif read_by:
            reasons.append(f"Already read by {', '.join(read_by)}")
        if to_read_by:
            reasons.append(f"On the to-read list of {', '.join(to_read_by)}")

        affinity = genre_affinity(book.all_genres, group)
        author_bonus = group.authors.get(book.author.strip(), 0.0) * 2
        if author_bonus > 0:
            reasons.append(f"The club reads {book.author}")

        score = (
            affinity
            + 0.8 * endorsement
            + author_bonus
            - _dislike_penalty(book.all_genres, profiles)
        )
        if score <= 0:
            continue

        genre_reason = _genre_reason(book.all_genres, group)
        if genre_reason:
            reasons.append(genre_reason)

        suggestions.append(
            Suggestion(
                title=book.title,
                author=book.author or "Unknown author",
                source=COLLECTIVE,
                score=score,
                genres=book.all_genres,
                reasons=reasons,
                already_with=sorted(book.holders),
                missing_for=missing,
                average_rating=book.average_rating,
                cover_id=book.cover_id,
                isbn=next(iter(sorted(book.isbns)), ""),
                alternate_titles=sorted(book.alternate_titles),
            )
        )

    suggestions.sort(key=lambda s: -s.score)
    return _cap_per_author(suggestions, max_per_author)[:limit]


def suggest_discoveries(
    library: CollectiveLibrary,
    profiles: dict[str, TasteProfile],
    group: TasteProfile,
    client: OpenLibraryClient,
    limit: int = 20,
    subjects_to_browse: int = 6,
    per_subject: int = 40,
    max_per_author: int = 2,
    min_year: int | None = 1990,
    participants: list[str] | None = None,
    stats: FilterStats | None = None,
) -> list[Suggestion]:
    """Recommend books nobody has yet, browsing the club's favourite subjects."""
    joining = list(participants) if participants else list(library.people)
    stats = stats if stats is not None else FilterStats()
    blocked = blocked_genre_set(profiles)
    # Open Library results rarely carry series numbers, so compare on both keys to
    # avoid re-suggesting a book a member already shelved as "Title (Series, #1)".
    known_keys = {book.key for book in library.books}
    known_keys |= {book.base_key for book in library.books}
    known_works = {book.work_key for book in library.books if book.work_key}
    # A blocked genre is not worth browsing at all.
    top_genres = [
        genre
        for genre, _ in group.top_genres(subjects_to_browse + len(blocked))
        if canonical_genre(genre) not in blocked
    ][:subjects_to_browse]
    if not top_genres:
        return []

    # A discovery is more valuable when it opens up an author the club has not
    # read yet, and this also damps translated editions of books already shelved.
    owned_per_author: Counter[str] = Counter()
    for book in library.books:
        surname = author_surname(book.author)
        if surname:
            owned_per_author[surname] += 1

    weights = dict(group.top_genres(subjects_to_browse))
    best: dict[str, tuple[float, SubjectCandidate, list[str]]] = {}

    for genre in top_genres:
        for candidate in client.books_in_subject(genre, limit=per_subject):
            key = candidate.match_key
            if key in known_keys or candidate.base_key in known_keys:
                continue  # someone already has it, so it belongs to the other pool
            if candidate.open_library_key and candidate.open_library_key in known_works:
                continue  # same work, usually a translated edition of an owned book
            # The club reads English, and Open Library's canonical title for a
            # work is sometimes a translation. The script check stays as a cheap
            # backstop for candidates with no edition data at all.
            if not candidate.in_english or not _is_latin_script(candidate.title):
                continue
            if _is_blocked([candidate.subject], blocked):
                stats.blocked_genres += 1
                stats.note(candidate.title)
                continue
            if (
                min_year
                and candidate.first_publish_year
                and candidate.first_publish_year < min_year
            ):
                continue
            popularity = math.log1p(max(candidate.readers, 0)) / 40
            rating_bonus = 0.0
            if candidate.rating is not None and candidate.rating_count >= 20:
                rating_bonus = max(candidate.rating - 3.5, 0) * 0.05
            already_owned = owned_per_author.get(
                author_surname(candidate.author), 0
            )
            score = weights.get(genre, 0.0) + popularity + rating_bonus
            score -= 0.03 * min(already_owned, 6)
            score -= _dislike_penalty([genre], profiles)
            if score <= 0:
                continue
            existing = best.get(key)
            if existing is None:
                best[key] = (score, candidate, [genre])
            else:
                # Turning up under several club genres is a strong signal.
                previous_score, previous_candidate, genres = existing
                if genre not in genres:
                    genres.append(genre)
                best[key] = (previous_score + score * 0.5, previous_candidate, genres)

    suggestions = []
    # The series check costs a request or two per book, so it waits until the
    # candidates have been scored and only runs on the shortlist that is left.
    ranked = sorted(best.values(), key=lambda entry: -entry[0])
    for score, candidate, genres in ranked:
        if len(suggestions) >= limit * 2:
            break  # enough survivors to fill the list after the author cap
        if not client.starts_its_series(candidate.open_library_key):
            stats.mid_series += 1
            stats.note(candidate.title)
            continue
        reasons = [f"Popular in {', '.join(genres)}, which the club reads a lot"]
        if not owned_per_author.get(author_surname(candidate.author), 0):
            reasons.append(f"{candidate.author} is a new author for the club")
        if candidate.rating is not None and candidate.rating_count >= 20:
            reasons.append(
                f"Rated {candidate.rating:.1f}/5 by {candidate.rating_count} "
                "Open Library readers"
            )
        if candidate.first_publish_year:
            reasons.append(f"First published {candidate.first_publish_year}")
        reasons.append("Nobody in the club has this on their shelves yet")
        suggestions.append(
            Suggestion(
                title=candidate.title,
                author=candidate.author or "Unknown author",
                source=DISCOVERY,
                score=score,
                genres=genres,
                reasons=reasons,
                already_with=[],
                missing_for=list(joining),
                cover_id=candidate.cover_id,
                average_rating=candidate.rating,
                first_publish_year=candidate.first_publish_year,
            )
        )

    suggestions.sort(key=lambda s: -s.score)
    return _cap_per_author(suggestions, max_per_author)[:limit]


def _cap_per_author(
    suggestions: list[Suggestion], max_per_author: int
) -> list[Suggestion]:
    """Keep the list varied instead of ten books by one author."""
    if max_per_author <= 0:
        return suggestions
    seen: Counter[str] = Counter()
    kept = []
    for suggestion in suggestions:
        author = suggestion.author.lower()
        if seen[author] >= max_per_author:
            continue
        seen[author] += 1
        kept.append(suggestion)
    return kept


@dataclass
class Recommendations:
    """The ranked suggestions, how they were reached, and what was filtered out."""

    suggestions: list[Suggestion]
    profiles: dict[str, TasteProfile]
    group: TasteProfile
    participants: list[str] = field(default_factory=list)
    filtered: FilterStats = field(default_factory=FilterStats)

    def of_source(self, source: str) -> list[Suggestion]:
        return [s for s in self.suggestions if s.source == source]


def recommend(
    library: CollectiveLibrary,
    preferences: dict[str, MemberPreferences] | None = None,
    client: OpenLibraryClient | None = None,
    library_limit: int = 30,
    discovery_limit: int = 20,
    min_year: int | None = 1990,
    participants: list[str] | None = None,
    read_filter: str = READ_FILTER_INCLUDE,
) -> Recommendations:
    """Build both suggestion pools plus the profiles used to rank them.

    Members sitting out keep their books in the pool but stop steering it: their
    taste, their vetoes and their shelves are left out of the scoring.
    """
    joining = list(participants) if participants else list(library.people)
    preferences = preferences or {}
    profiles = build_profiles(library, preferences)
    joining_profiles = {p: profiles[p] for p in joining if p in profiles}
    group = build_group_profile(joining_profiles or profiles)
    stats = FilterStats()

    from_library = suggest_from_library(
        library,
        joining_profiles,
        group,
        limit=library_limit,
        participants=joining,
        read_filter=read_filter,
        stats=stats,
    )
    discoveries = (
        suggest_discoveries(
            library,
            joining_profiles,
            group,
            client,
            limit=discovery_limit,
            min_year=min_year,
            participants=joining,
            stats=stats,
        )
        if client is not None
        else []
    )

    combined = _scale_scores(from_library) + _scale_scores(discoveries)
    combined.sort(key=lambda s: -s.score)
    return Recommendations(
        suggestions=combined,
        profiles=profiles,
        group=group,
        participants=joining,
        filtered=stats,
    )


def _scale_scores(suggestions: list[Suggestion]) -> list[Suggestion]:
    """Rescale each pool to 0-100 so the two pools can share one ranking."""
    if not suggestions:
        return []
    top = max(s.score for s in suggestions) or 1.0
    for suggestion in suggestions:
        suggestion.score = round(100 * suggestion.score / top, 1)
    return suggestions
