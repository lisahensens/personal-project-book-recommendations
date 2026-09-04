"""Fetch genre/subject data from Open Library, cached on disk.

Open Library needs no API key. Every lookup is cached so repeat runs cost
nothing and the club stays a polite API citizen.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

import requests

from .matching import (
    CanonicalBook,
    normalise_author,
    normalise_title,
    normalise_title_base,
)

CACHE_PATH = Path("data/cache/open_library_cache.json")
USER_AGENT = "BookClubRecommender/1.0 (personal book club project)"
BASE_URL = "https://openlibrary.org"
REQUEST_TIMEOUT = 12
POLITE_DELAY = 0.25
MAX_GENRES_PER_BOOK = 12
# How many books to look up between cache writes during a long batch.
SAVE_EVERY = 10
# Bumped whenever a genre lookup gets better at finding data, so books it
# previously found nothing for are tried once more instead of staying blank.
LOOKUP_GENERATION = 2

# Open Library subjects mix genres with plot topics and catalogue tags: one book
# can be filed under "Fantasy", "Sisters", "Contests" and "Accessible book" alike.
# Only subjects that map into this vocabulary are treated as genres, which keeps
# taste profiles about genre rather than plot trivia. Order matters, because the
# first pattern to match wins ("science fiction" before the broader "science").
_GENRE_VOCABULARY: tuple[tuple[str, str], ...] = (
    ("Romantasy", r"romantasy"),
    ("Manga", r"\bmanga\b|light novel"),
    ("Graphic Novels", r"graphic novel|comic book|comics|comic strip"),
    ("True Crime", r"true crime"),
    ("Historical Fiction", r"historical fiction|historical novel"),
    ("Magical Realism", r"magic(al)? realism"),
    ("Dystopian", r"dystopi|utopia"),
    ("Science Fiction", r"science fiction|sci-?fi|space opera|cyberpunk|"
                        r"\bspace\b|robot|extraterrestrial|alien|time travel|"
                        r"apocalyp|end of the world|steampunk"),
    ("Horror", r"horror|ghost|haunt|zombie|gothic|vampire|werewolf|monster|"
               r"occult|demon"),
    ("Fantasy", r"fantasy|magic|wizard|sorcer|dragon|fairi|fairy tale|"
                r"mytholog|witch|elves|quest|imaginary|epic"),
    ("Thriller", r"thriller|suspense|espionage|spy stor"),
    ("Mystery", r"mystery|detective|whodunit|noir|sleuth"),
    ("Crime", r"crime|murder|police|gangster|mafia"),
    ("Romance", r"romance|romantic|love stor|chick lit"),
    ("Erotica", r"erotic"),
    # Open Library catalogues young adult series as "Juvenile fiction", so that
    # term belongs here rather than with genuine children's books.
    ("Young Adult", r"young adult|teenage|teen fiction|coming of age|"
                    r"juvenile fiction"),
    ("Children's Fiction", r"children|juvenile|picture book|middle grade|"
                           r"school stor"),
    ("Adventure", r"adventure|survival|treasure|pirat"),
    ("Western", r"\bwestern|cowboy"),
    ("War Fiction", r"war stor|world war|military fiction"),
    ("Short Stories", r"short stor|antholog"),
    ("Poetry", r"poetr|poems"),
    ("Classics", r"classic"),
    ("Humour", r"humor|humour|satire|comic fiction|parody"),
    ("LGBTQ", r"\bgay\b|lesbian|lgbt|queer|transgender"),
    ("Literary Fiction", r"literary|literature"),
    ("Memoir", r"memoir|autobiograph|personal narrative"),
    ("Biography", r"biograph"),
    ("Popular Science", r"science|physics|biology|astronom|mathematic|evolution"),
    ("History", r"history|historic|antiquit|civilization"),
    ("Philosophy", r"philosoph|ethics|logic"),
    ("Psychology", r"psycholog|mental health"),
    ("Self Help", r"self-?help|personal development|productivity|habits"),
    ("Business", r"business|economic|management|finance|entrepreneur"),
    ("Politics", r"politic|government|democracy"),
    ("Religion", r"religio|spiritual|christian|buddhis|islam|theolog|bible"),
    ("Travel", r"travel|voyages|guidebook"),
    ("Nature", r"nature|environment|ecolog|animals|gardening"),
    ("Cookbooks", r"cook|recipe|cuisine|baking"),
    ("Art", r"\bart\b|photograph|painting|design|architecture"),
    ("Music", r"music|jazz|rock music|opera"),
    ("Sports", r"sport|football|soccer|baseball|climbing"),
    ("Health", r"health|fitness|nutrition|medicine"),
    ("Nonfiction", r"non-?fiction|essays|reportage|journalism"),
)

_GENRE_MATCHERS: tuple[tuple[str, re.Pattern], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _GENRE_VOCABULARY
)

# Open Library tags series membership as a subject, sometimes bracketed:
# "[series:Throne_of_Glass]" or "series:A_Court_of_Thorns_and_Roses".
_SERIES_SUBJECT = re.compile(r"^\[?series:([^\]]+)\]?$", re.IGNORECASE)

# Omnibus editions: several novels in one listing, which is not a book to read
# next. Open Library files these under "book_set" or a boxed-set subject.
_BOX_SET_SUBJECT = re.compile(r"\b(book_set|boxed?[ _-]?set|omnibus)\b", re.IGNORECASE)

# Catalogue and library-system tags, checked before genre matching so that
# something like "collectionID:fantasy-picks" cannot sneak in as a genre.
_SUBJECT_BLOCKLIST = re.compile(
    r"(accessible book|protected daisy|in library|internet archive|overdrive|"
    r"large type|reading level|lending library|open library|nyt:|new york times|"
    r"award|translations into|specimens|catalog|\w+id:|collection|bestseller|"
    r"book club|staff pick|reading level|^\d+$)",
    re.IGNORECASE,
)


@dataclass
class EnrichmentProgress:
    """How a genre-lookup batch is going, for the progress bar and the summary.

    `found` and `without_data` are tracked separately because "no genres on Open
    Library" is a normal outcome, and lumping it in with the total would make a
    working batch look like a failure.
    """

    index: int
    total: int
    title: str = ""
    found: int = 0
    without_data: int = 0
    elapsed: float = 0.0

    @property
    def fraction(self) -> float:
        return self.index / self.total if self.total else 1.0

    @property
    def seconds_left(self) -> float | None:
        """Rough estimate from the average so far, or None until it means anything."""
        if self.index < 2 or self.index >= self.total:
            return None
        return (self.elapsed / self.index) * (self.total - self.index)

    def eta_text(self) -> str:
        seconds = self.seconds_left
        if seconds is None:
            return ""
        if seconds < 60:
            return f"about {round(seconds)}s left"
        return f"about {round(seconds / 60)} min left"

    def summary(self) -> str:
        """One line for after the run, honest about books with no data."""
        if not self.total:
            return "Nothing left to look up."
        parts = [f"Looked up {self.index} book(s): {self.found} got genres"]
        if self.without_data:
            parts.append(f"{self.without_data} had no genre data on Open Library")
        return ", ".join(parts) + "."


def _is_stale_empty_entry(cached: dict) -> bool:
    """Detect a "found nothing" cache entry written by an older, worse lookup.

    Only empty entries are retried. An older entry that already holds subjects is
    still good data and refetching it would waste a request, which is why the
    generation marker is not checked on its own.

    Two rounds of bug produced false negatives here: lookups predating the
    work-key and cover fallbacks, and lookups whose title search still carried
    the series suffix, which made Open Library return nothing for most of a
    series-heavy shelf. Rather than fingerprint each one, entries carry the
    generation of the code that wrote them, so an empty result is retried once
    after a lookup improves and then trusted.
    """
    if cached.get("subjects"):
        return False
    return int(cached.get("lookup", 0)) < LOOKUP_GENERATION


@dataclass
class SubjectCandidate:
    """A book discovered by browsing an Open Library subject."""

    title: str
    author: str
    subject: str
    first_publish_year: int | None = None
    edition_count: int = 0
    open_library_key: str = ""
    readers: int = 0
    rating: float | None = None
    rating_count: int = 0
    cover_id: int | None = None
    # False when Open Library has no English edition on file, in which case the
    # title above is a translation and the club should not be offered it.
    in_english: bool = True

    @property
    def _surname(self) -> str:
        parts = normalise_author(self.author).split()
        return parts[-1] if parts else ""

    @property
    def match_key(self) -> str:
        return f"{normalise_title(self.title)}|{self._surname}"

    @property
    def base_key(self) -> str:
        return f"{normalise_title_base(self.title)}|{self._surname}"


class OpenLibraryClient:
    """Cached read-only client for the handful of endpoints we need."""

    def __init__(
        self,
        cache_path: str | Path = CACHE_PATH,
        session: requests.Session | None = None,
        offline: bool = False,
    ) -> None:
        self.cache_path = Path(cache_path)
        self.offline = offline
        self._session = session
        self._cache: dict[str, dict] = self._load_cache()
        self._dirty = False

    # -- cache ---------------------------------------------------------------

    def _load_cache(self) -> dict[str, dict]:
        if self.cache_path.exists():
            try:
                return json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def save(self) -> None:
        if not self._dirty:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, indent=2, sort_keys=True), encoding="utf-8"
        )
        self._dirty = False

    def _cached(self, key: str) -> dict | None:
        return self._cache.get(key)

    def _store(self, key: str, payload: dict) -> None:
        self._cache[key] = payload
        self._dirty = True

    # -- http ----------------------------------------------------------------

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": USER_AGENT})
        return self._session

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        if self.offline:
            return None
        try:
            response = self.session.get(
                f"{BASE_URL}{path}", params=params, timeout=REQUEST_TIMEOUT
            )
            if response.status_code != 200:
                return None
            time.sleep(POLITE_DELAY)
            return response.json()
        except (requests.RequestException, ValueError):
            return None

    # -- genres for known books ---------------------------------------------

    def cached_genres(self, book: CanonicalBook) -> list[str] | None:
        """Genres already on disk for this book, or None if never looked up."""
        cached = self._cached(self._book_cache_key(book))
        if cached is None:
            return None
        if _is_stale_empty_entry(cached):
            # Written before the work-key fallback and cover capture existed, and
            # it found nothing. The old code gave up too early (it neither tried
            # the work behind the edition nor moved on to the book's other
            # ISBNs), so treat it as never looked up rather than trusting it
            # forever - a fresh attempt now succeeds far more often.
            return None
        book.work_key = book.work_key or cached.get("work_key", "")
        if book.cover_id is None:
            cover = cached.get("cover_id")
            book.cover_id = cover if isinstance(cover, int) else None
        # Raw subjects are cached and filtered on read, so tightening the genre
        # filters takes effect without refetching everything.
        return clean_subjects(cached.get("subjects", cached.get("genres", [])))

    def apply_cached_genres(self, books: Sequence[CanonicalBook]) -> int:
        """Fill in genres from the cache alone, without touching the network."""
        applied = 0
        for book in books:
            genres = self.cached_genres(book)
            if genres and not book.all_genres:
                book.genres = genres
                applied += 1
        return applied

    def genres_for_book(self, book: CanonicalBook) -> list[str]:
        """Look up genre-ish subjects for one book, by ISBN then by title search."""
        key = self._book_cache_key(book)
        cached = self.cached_genres(book)
        if cached is not None:
            return cached

        subjects, work_key, cover_id = self._subjects_by_isbn(book)
        if not subjects:
            searched, search_work, search_cover = self._subjects_by_search(book)
            subjects = searched
            work_key = work_key or search_work
            cover_id = cover_id if cover_id is not None else search_cover
        book.work_key = work_key
        book.cover_id = cover_id
        self._store(
            key,
            {
                "subjects": subjects,
                "work_key": work_key,
                "cover_id": cover_id,
                "title": book.title,
                "author": book.author,
                "lookup": LOOKUP_GENERATION,
            },
        )
        return clean_subjects(subjects)

    @staticmethod
    def _book_cache_key(book: CanonicalBook) -> str:
        for isbn in sorted(book.isbns):
            return f"isbn:{isbn}"
        return f"book:{book.key}"

    def _subjects_by_isbn(self, book: CanonicalBook) -> tuple[list[str], str, int | None]:
        fallback: tuple[list[str], str, int | None] = ([], "", None)
        for isbn in sorted(book.isbns):
            data = self._get(f"/isbn/{isbn}.json")
            if not data:
                continue
            subjects = list(data.get("subjects") or [])
            works = data.get("works") or []
            work_key = ""
            if works and isinstance(works[0], dict):
                work_key = works[0].get("key") or ""
            covers = [c for c in (data.get("covers") or []) if isinstance(c, int)]
            cover_id = covers[0] if covers else None
            if not subjects and work_key:
                # Editions often carry no subjects; the work behind them usually does.
                work = self._get(f"{work_key}.json")
                subjects = list((work or {}).get("subjects") or [])
            if subjects:
                return [str(s) for s in subjects], work_key, cover_id
            # Keep the work id and cover even when no genres were found: they
            # still de-duplicate translated editions and illustrate the book.
            if work_key or cover_id:
                fallback = ([], work_key, cover_id)
        return fallback

    def _subjects_by_search(
        self, book: CanonicalBook
    ) -> tuple[list[str], str, int | None]:
        # Open Library matches titles strictly, so the series suffix Goodreads
        # adds has to go: searching "Harry Potter and the Order of the Phoenix
        # (Harry Potter, #5, Part 1)" finds nothing, while the bare title finds
        # the book. Most of the club's shelves are series entries, so this is
        # the difference between a batch finding genres and finding none.
        data = self._get(
            "/search.json",
            {
                "title": normalise_title_base(book.title),
                "author": book.author,
                "fields": "key,subject,title,author_name,cover_i",
                "limit": 1,
            },
        )
        docs = (data or {}).get("docs") or []
        if not docs:
            return [], "", None
        cover = docs[0].get("cover_i")
        return (
            [str(s) for s in (docs[0].get("subject") or [])],
            docs[0].get("key", ""),
            cover if isinstance(cover, int) else None,
        )

    def enrich_library(
        self,
        books: Sequence[CanonicalBook],
        limit: int | None = None,
        progress: Callable[["EnrichmentProgress"], None] | None = None,
    ) -> "EnrichmentProgress":
        """Fill in `book.genres` for books lacking genre data.

        Returns the final progress report, so callers can tell "found nothing"
        apart from "looked at nothing".
        """
        targets = [
            book
            for book in prioritise_for_enrichment(books)
            if self.cached_genres(book) is None
        ]
        if limit is not None:
            targets = targets[:limit]

        report = EnrichmentProgress(index=0, total=len(targets))
        started = time.monotonic()
        saved_at = 0
        for index, book in enumerate(targets, start=1):
            genres = self.genres_for_book(book)
            if genres:
                book.genres = genres
                report.found += 1
            else:
                report.without_data += 1
            report.index = index
            report.title = book.title
            report.elapsed = time.monotonic() - started

            # A long batch is easy to interrupt - a closed tab, a restart, an
            # impatient Stop - so results reach disk as we go instead of only
            # at the end, where a cancelled run would have thrown them all away.
            if index - saved_at >= SAVE_EVERY:
                self.save()
                saved_at = index

            if progress is not None:
                progress(report)

        self.save()
        return report

    # -- discovery ----------------------------------------------------------

    def starts_its_series(self, work_key: str) -> bool:
        """Whether this work is a sensible place to start reading.

        Standalones and first volumes qualify; later volumes and box sets do
        not, since suggesting book four to a club that has not read book one is
        no use. Results are cached, and the check only runs on candidates that
        already survived scoring, so the request cost stays small.
        """
        if not work_key:
            return True  # nothing to check against, so do not stand in the way

        key = f"series-start:v1:{work_key}"
        cached = self._cached(key)
        if cached is None:
            work = self._get(f"{work_key}.json") or {}
            cached = {"starts": self._judge_series_start(work, work_key)}
            self._store(key, cached)
            self.save()
        return bool(cached.get("starts", True))

    def _judge_series_start(self, work: dict, work_key: str) -> bool:
        subjects = [str(s) for s in (work.get("subjects") or [])]

        # A bundle of several novels is a poor book-club pick whatever its
        # position, and these are what surfaced as odd foreign-language results.
        if any(_BOX_SET_SUBJECT.search(s) for s in subjects):
            return False

        # Open Library's own series position is the most direct answer when it
        # is filled in, and it covers books whose subject tags are missing.
        position = _series_position(work)
        if position is not None:
            return position <= 1

        # No position on file, so fall back to the series tag and ask which of
        # its members came out first.
        return self._is_earliest_in_series(series_tag(subjects), work_key)

    def _is_earliest_in_series(self, tag: str | None, work_key: str) -> bool:
        if not tag:
            return True  # no series tag at all, so treat it as a standalone
        data = self._get(
            "/search.json",
            {
                "subject": f"series:{tag}",
                "sort": "old",
                "limit": 3,
                "fields": "key,first_publish_year",
            },
        )
        docs = (data or {}).get("docs") or []
        if not docs:
            return True  # the tag told us nothing useful, so do not block it
        return docs[0].get("key") == work_key

    def books_in_subject(self, subject: str, limit: int = 40) -> list[SubjectCandidate]:
        """Find widely-read books filed under a subject.

        Uses the search endpoint sorted by reading-log popularity rather than the
        subjects endpoint, which ranks by edition count and therefore returns
        mostly out-of-copyright classics.
        """
        slug = subject_slug(subject)
        if not slug:
            return []
        # The version marker retires cached responses that were stored before the
        # fields we ask for changed, rather than leaving them stale forever.
        key = f"search-subject:v3:{slug}:{limit}"
        cached = self._cached(key)
        if cached is None:
            params = {
                "subject": subject,
                "sort": "readinglog",
                "limit": limit,
                # A work's own title is whichever edition Open Library treats as
                # canonical, which is sometimes a translation, so the English
                # edition is requested alongside it and preferred below.
                "editions.language": "eng",
                "fields": (
                    "key,title,author_name,first_publish_year,edition_count,"
                    "ratings_average,ratings_count,readinglog_count,cover_i,"
                    "language,editions,editions.title,editions.language,"
                    "editions.cover_i"
                ),
            }
            data = self._get("/search.json", params) or {}
            works = [_subject_work(doc) for doc in (data.get("docs") or [])]
            cached = {"works": works}
            self._store(key, cached)
            self.save()

        return [
            SubjectCandidate(
                title=work["title"],
                author=work.get("author", ""),
                subject=subject,
                first_publish_year=work.get("first_publish_year"),
                edition_count=work.get("edition_count", 0),
                open_library_key=work.get("key", ""),
                readers=work.get("readers", 0),
                rating=work.get("rating"),
                rating_count=work.get("rating_count", 0),
                cover_id=work.get("cover_id"),
                in_english=bool(work.get("in_english")),
            )
            for work in cached.get("works", [])
            if work.get("title")
        ]


def _series_position(work: dict) -> float | None:
    """Where a work sits in its series, per Open Library's `series` field.

    Positions are free text: "2", "1-4" for a bundle, sometimes "Book 3". Only a
    leading number is trusted, and None means the field said nothing usable.
    """
    entries = work.get("series")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        raw = entry.get("position") if isinstance(entry, dict) else entry
        match = re.search(r"\d+(?:\.\d+)?", str(raw or ""))
        if match:
            return float(match.group(0))
    return None


def series_tag(subjects: Iterable[str]) -> str | None:
    """The series a work belongs to, from Open Library's `series:` subject tag.

    Open Library has no reliable ordinal for a book's place in its series, but it
    does tag membership, e.g. "[series:Throne_of_Glass]". Knowing the series is
    enough: the earliest-published work carrying the same tag is book one.
    """
    for raw in subjects:
        match = _SERIES_SUBJECT.match(str(raw).strip())
        if match:
            return match.group(1).strip("_ ")
    return None


def _subject_work(doc: dict) -> dict:
    """Flatten one search result, preferring its English edition.

    The work-level title is whichever edition Open Library considers canonical,
    which is why a Sarah J. Maas book could turn up as "Una corte de niebla y
    furia". The nested edition comes back English when one exists, so its title
    and cover are used instead, and `in_english` records whether that worked.
    """
    editions = ((doc.get("editions") or {}).get("docs") or [{}])[0]
    edition_languages = [str(code) for code in (editions.get("language") or [])]
    english_edition = "eng" in edition_languages

    title = doc.get("title") or ""
    cover_id = doc.get("cover_i")
    if english_edition and editions.get("title"):
        title = editions["title"]
        cover_id = editions.get("cover_i", cover_id)

    return {
        "title": title,
        "author": (doc.get("author_name") or [""])[0],
        "first_publish_year": doc.get("first_publish_year"),
        "edition_count": doc.get("edition_count") or 0,
        "key": doc.get("key") or "",
        "readers": doc.get("readinglog_count") or 0,
        "rating": doc.get("ratings_average"),
        "rating_count": doc.get("ratings_count") or 0,
        "cover_id": cover_id,
        "in_english": english_edition,
    }


def prioritise_for_enrichment(
    books: Sequence[CanonicalBook],
    client: OpenLibraryClient | None = None,
) -> list[CanonicalBook]:
    """Order genre lookups so a limited API budget buys the most useful genres.

    Big libraries make full enrichment a long job, so books that actually move the
    recommendations go first: ones several members share, ones members rated, and
    ones from members whose shelves are too sparse to infer taste from.

    A book already looked up is left out even when Open Library had no genres:
    fetching it again would spend the batch on the same misses. Stale cache
    entries still come through, because those are worth one more try.
    """
    targets = []
    for book in books:
        if book.all_genres:
            continue
        if client is not None and client.cached_genres(book) is not None:
            continue
        targets.append(book)

    def rank(book: CanonicalBook) -> tuple:
        return (
            -len(book.holders),  # shared books shape the club's overlap most
            -len(book.liked_by),
            -len(book.read_by),
            0 if book.isbns else 1,  # an ISBN makes the lookup likely to succeed
            book.title.lower(),
        )

    return sorted(targets, key=rank)


def subject_slug(subject: str) -> str:
    """Turn a genre name into an Open Library subject slug."""
    slug = re.sub(r"[^a-z0-9]+", "_", subject.lower()).strip("_")
    return slug


def known_genre_names() -> list[str]:
    """Every genre the tool recognises, for offering as choices in the UI."""
    return sorted({name for name, _ in _GENRE_VOCABULARY})


def match_genre(subject: str) -> str | None:
    """Map a free-text subject onto the genre vocabulary, or None if it is not one."""
    if _SUBJECT_BLOCKLIST.search(subject):
        return None
    for name, pattern in _GENRE_MATCHERS:
        if pattern.search(subject):
            return name
    return None


def clean_subjects(subjects: Iterable[str]) -> list[str]:
    """Turn Open Library subjects into a deduped list of recognised genres."""
    seen: dict[str, str] = {}
    for raw in subjects:
        subject = re.sub(r"\s+", " ", str(raw)).strip(" .,;")
        if not subject or len(subject) < 3:
            continue
        genre = match_genre(subject)
        if genre is None:
            continue  # a plot topic or catalogue tag, not a genre
        seen.setdefault(genre.lower(), genre)
        if len(seen) >= MAX_GENRES_PER_BOOK:
            break
    return list(seen.values())


def canonical_genre(genre: str) -> str:
    """Normalise genre spelling so genres from all sources line up.

    Anything recognisable becomes the vocabulary's name for it, so a member who
    types "sci-fi" lines up with a book filed as "Science fiction". Unrecognised
    entries are kept as typed, so niche tastes are not silently discarded.
    """
    text = re.sub(r"\s+", " ", genre).strip().strip(" .,;")
    if not text:
        return text
    text = re.sub(r"^(fiction|juvenile fiction)\s*[/,-]\s*", "", text, flags=re.I)
    matched = match_genre(text)
    if matched is not None:
        return matched
    return text.title() if text.islower() or text.isupper() else text
