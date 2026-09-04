"""English-only suggestions, series entry points, and genre-lookup progress."""

import json

import pytest

from bookclub.enrichment import (
    EnrichmentProgress,
    OpenLibraryClient,
    SubjectCandidate,
    _subject_work,
    series_tag,
)
from bookclub.ingest import TO_READ, BookRecord
from bookclub.matching import build_library, series_identity
from bookclub.preferences import MemberPreferences
from bookclub.recommend import build_group_profile, build_profiles, suggest_discoveries


def record(person, title, author, shelf=TO_READ, rating=None, genres=()):
    return BookRecord(
        person=person,
        title=title,
        author=author,
        shelf=shelf,
        rating=rating,
        source_genres=list(genres),
    )


def candidate(title, author="Some Author", key="/works/OL1W", **kwargs):
    return SubjectCandidate(
        title=title,
        author=author,
        subject="Fantasy",
        first_publish_year=2015,
        edition_count=10,
        open_library_key=key,
        readers=5000,
        rating=4.4,
        rating_count=900,
        **kwargs,
    )


class FakeClient:
    """Open Library stand-in, so no test touches the network."""

    def __init__(self, by_subject, mid_series=()):
        self.by_subject = by_subject
        self.mid_series = set(mid_series)

    def books_in_subject(self, subject, limit=40):
        return self.by_subject.get(subject, [])

    def starts_its_series(self, work_key):
        return work_key not in self.mid_series


def fantasy_club():
    """A two-person club that reads fantasy, so fantasy candidates score."""
    library = build_library(
        [
            record("Lisa", "Some Fantasy", "A Writer", genres=["Fantasy"]),
            record("Eline", "More Fantasy", "B Writer", genres=["Fantasy"]),
        ]
    )
    preferences = {
        person: MemberPreferences(person=person, liked_genres=["Fantasy"])
        for person in library.people
    }
    profiles = build_profiles(library, preferences)
    return library, profiles, build_group_profile(profiles)


# ---------------------------------------------------------- base-title search


def test_search_fallback_strips_the_series_suffix_from_the_query(monkeypatch, tmp_path):
    """Open Library finds nothing for a title carrying "(Series, #5)"."""
    library = build_library(
        [record("Lisa", "Order of the Phoenix (Harry Potter, #5, Part 1)", "J.K. Rowling")]
    )
    client = OpenLibraryClient(cache_path=tmp_path / "cache.json")
    queries = []

    def fake_get(path, params=None):
        queries.append((params or {}).get("title"))
        return {"docs": [{"key": "/works/OL1W", "subject": ["Fantasy fiction"]}]}

    monkeypatch.setattr(client, "_get", fake_get)
    subjects, work_key, _ = client._subjects_by_search(library.books[0])

    assert "#" not in queries[0] and "(" not in queries[0]
    assert queries[0] == "order of phoenix"
    assert subjects == ["Fantasy fiction"]
    assert work_key == "/works/OL1W"


# ------------------------------------------------------------- English-only


def test_english_edition_title_replaces_a_translated_work_title():
    """The work's default title is sometimes a translation, the edition is not."""
    work = _subject_work(
        {
            "key": "/works/OL1W",
            "title": "Una corte de niebla y furia",
            "author_name": ["Sarah J. Maas"],
            "cover_i": 111,
            "editions": {
                "docs": [
                    {
                        "title": "A Court of Mist and Fury",
                        "language": ["eng"],
                        "cover_i": 222,
                    }
                ]
            },
        }
    )

    assert work["title"] == "A Court of Mist and Fury"
    assert work["cover_id"] == 222
    assert work["in_english"] is True


def test_work_without_an_english_edition_is_flagged_not_english():
    work = _subject_work(
        {
            "key": "/works/OL2W",
            "title": "Het Diner",
            "cover_i": 333,
            "editions": {"docs": [{"title": "Het Diner", "language": ["dut"]}]},
        }
    )

    assert work["in_english"] is False
    assert work["title"] == "Het Diner"


def test_candidates_with_no_english_edition_are_not_suggested():
    library, profiles, group = fantasy_club()
    client = FakeClient(
        {
            "Fantasy": [
                candidate("A Court of Mist and Fury", key="/works/OL10W"),
                candidate("Het Diner", key="/works/OL11W", in_english=False),
            ]
        }
    )

    titles = [
        s.title
        for s in suggest_discoveries(library, profiles, group, client=client, limit=10)
    ]
    assert "A Court of Mist and Fury" in titles
    assert "Het Diner" not in titles


# --------------------------------------------------------- series entry points


@pytest.mark.parametrize(
    "subjects,expected",
    [
        (["[series:Throne_of_Glass]"], "Throne_of_Glass"),
        (["series:A_Court_of_Thorns_and_Roses"], "A_Court_of_Thorns_and_Roses"),
        (["Fantasy", "series:Harry_Potter"], "Harry_Potter"),
        (["Fantasy", "Magic"], None),
    ],
)
def test_series_tag_is_read_from_the_subject_list(subjects, expected):
    assert series_tag(subjects) == expected


def test_later_volumes_and_box_sets_are_not_entry_points(tmp_path):
    """Open Library's series position answers this directly when it is filled in."""
    client = OpenLibraryClient(cache_path=tmp_path / "cache.json", offline=True)

    first = {"subjects": ["Fantasy"], "series": [{"position": "1"}]}
    fifth = {"subjects": ["Fantasy"], "series": [{"position": "5"}]}
    bundle = {"subjects": ["book_set"], "series": [{"position": "1-4"}]}
    standalone = {"subjects": ["Fantasy"]}

    assert client._judge_series_start(first, "/works/OL1W") is True
    assert client._judge_series_start(fifth, "/works/OL2W") is False
    assert client._judge_series_start(bundle, "/works/OL3W") is False
    assert client._judge_series_start(standalone, "/works/OL4W") is True


def test_untagged_work_falls_back_to_the_earliest_published_check(monkeypatch, tmp_path):
    """With no position on file, the oldest work under the series tag is book one."""
    client = OpenLibraryClient(cache_path=tmp_path / "cache.json")
    monkeypatch.setattr(
        client,
        "_get",
        lambda path, params=None: {"docs": [{"key": "/works/FIRST"}]},
    )
    work = {"subjects": ["series:Throne_of_Glass"]}

    assert client._judge_series_start(work, "/works/FIRST") is True
    assert client._judge_series_start(work, "/works/LATER") is False


def test_mid_series_discoveries_are_dropped_and_counted():
    library, profiles, group = fantasy_club()
    from bookclub.recommend import FilterStats

    client = FakeClient(
        {
            "Fantasy": [
                candidate("Throne of Glass", key="/works/OL20W"),
                candidate("Empire of Storms", key="/works/OL21W"),
            ]
        },
        mid_series={"/works/OL21W"},
    )
    stats = FilterStats()

    titles = [
        s.title
        for s in suggest_discoveries(
            library, profiles, group, client=client, limit=10, stats=stats
        )
    ]
    assert titles == ["Throne of Glass"]
    assert stats.mid_series == 1


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Empire of Storms (Throne of Glass, #5)", ("throne of glass|maas", 5.0)),
        ("Throne of Glass (Throne of Glass, #1)", ("throne of glass|maas", 1.0)),
        ("Piranesi", None),
        ("Harry Potter Box Set (Harry Potter, #1-7)", None),
    ],
)
def test_series_identity_separates_the_series_from_the_volume(title, expected):
    assert series_identity(title, "Sarah J. Maas") == expected


def test_later_volume_is_offered_only_to_members_holding_the_earlier_ones():
    from bookclub.recommend import FilterStats, suggest_from_library

    library = build_library(
        [
            # Eline has book one, so book two is a reasonable next read for her.
            record("Eline", "The Cruel Prince (The Folk of the Air, #1)", "Holly Black"),
            record("Lisa", "The Cruel Prince (The Folk of the Air, #1)", "Holly Black"),
            record("Lisa", "The Wicked King (The Folk of the Air, #2)", "Holly Black"),
            # Nobody but Lisa is as far as book three, so it should vanish.
            record("Lisa", "The Queen of Nothing (The Folk of the Air, #3)", "Holly Black"),
            # Noor has never touched the series, so book two is not for her even
            # though she is missing it just as much as Eline is.
            record("Noor", "Piranesi", "Susanna Clarke"),
        ]
    )
    preferences = {
        person: MemberPreferences(person=person) for person in library.people
    }
    profiles = build_profiles(library, preferences)
    stats = FilterStats()
    suggestions = suggest_from_library(
        library, profiles, build_group_profile(profiles), limit=20, stats=stats
    )

    offered = {s.title: s.missing_for for s in suggestions}
    assert offered["The Wicked King (The Folk of the Air, #2)"] == ["Eline"]
    assert "The Queen of Nothing (The Folk of the Air, #3)" not in offered
    assert stats.mid_series == 1


# ------------------------------------------------------------- progress report


def test_progress_report_counts_found_and_missing_separately():
    report = EnrichmentProgress(index=3, total=10, found=1, without_data=2)

    assert report.fraction == pytest.approx(0.3)
    assert "1 got genres" in report.summary()
    assert "2 had no genre data" in report.summary()


def test_progress_eta_waits_until_it_has_enough_data():
    assert EnrichmentProgress(index=1, total=10, elapsed=1.0).seconds_left is None
    assert EnrichmentProgress(index=10, total=10, elapsed=5.0).seconds_left is None

    halfway = EnrichmentProgress(index=5, total=10, elapsed=10.0)
    assert halfway.seconds_left == pytest.approx(10.0)
    assert halfway.eta_text() == "about 10s left"


def test_enrichment_reports_each_book_and_saves_as_it_goes(monkeypatch, tmp_path):
    """An interrupted batch should keep whatever it already looked up."""
    library = build_library(
        [record("Lisa", f"Book {n}", f"Author {n}") for n in range(25)]
    )
    client = OpenLibraryClient(cache_path=tmp_path / "cache.json", offline=True)
    monkeypatch.setattr(client, "genres_for_book", lambda book: ["Fantasy"])

    saves = []
    original_save = client.save
    monkeypatch.setattr(
        client, "save", lambda: (saves.append(len(saves)), original_save())[1]
    )

    seen = []
    report = client.enrich_library(library.books, progress=lambda s: seen.append(
        (s.index, s.title, s.found)
    ))

    assert report.total == 25
    assert report.found == 25
    assert [entry[0] for entry in seen] == list(range(1, 26))
    assert seen[0][1] == library.books[0].title
    # Saves every ten books plus a final one, rather than only at the end.
    assert len(saves) == 3


def test_enrichment_summary_names_books_with_no_data(tmp_path):
    library = build_library([record("Lisa", "Unknown Book", "Nobody")])
    client = OpenLibraryClient(cache_path=tmp_path / "cache.json", offline=True)

    report = client.enrich_library(library.books)
    assert report.found == 0
    assert "no genre data" in report.summary()


# ------------------------------------------------------- cache invalidation


def test_empty_results_from_an_older_lookup_are_tried_again(tmp_path):
    """The broken search cached "no genres" for most of a series-heavy shelf."""
    library = build_library(
        [record("Lisa", "Empire of Storms (Throne of Glass, #5)", "Sarah J. Maas")]
    )
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps(
            {
                f"book:{library.books[0].key}": {
                    "subjects": [],
                    "work_key": "/works/OL1W",
                    "cover_id": 42,
                }
            }
        ),
        encoding="utf-8",
    )
    client = OpenLibraryClient(cache_path=cache, offline=True)

    assert client.cached_genres(library.books[0]) is None


def test_an_empty_result_from_the_current_lookup_is_trusted(tmp_path):
    """Some books really have no subjects, and should not be retried forever."""
    from bookclub.enrichment import LOOKUP_GENERATION

    library = build_library([record("Lisa", "Obscure Zine", "Nobody")])
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps(
            {
                f"book:{library.books[0].key}": {
                    "subjects": [],
                    "work_key": "",
                    "cover_id": None,
                    "lookup": LOOKUP_GENERATION,
                }
            }
        ),
        encoding="utf-8",
    )
    client = OpenLibraryClient(cache_path=cache, offline=True)

    assert client.cached_genres(library.books[0]) == []


def test_older_entries_that_found_genres_are_kept(tmp_path):
    """A stale entry holding real data is still good; refetching it wastes a call."""
    library = build_library([record("Lisa", "Hex", "Thomas Olde Heuvelt")])
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps({f"book:{library.books[0].key}": {"subjects": ["Horror tales"]}}),
        encoding="utf-8",
    )
    client = OpenLibraryClient(cache_path=cache, offline=True)

    assert client.cached_genres(library.books[0]) == ["Horror"]


def test_cached_series_verdicts_are_reused_without_new_requests(monkeypatch, tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps({"series-start:v1:/works/OL9W": {"starts": False}}), encoding="utf-8"
    )
    client = OpenLibraryClient(cache_path=cache, offline=True)

    def explode(*args, **kwargs):
        raise AssertionError("cached verdicts must not trigger a request")

    monkeypatch.setattr(client, "_get", explode)
    assert client.starts_its_series("/works/OL9W") is False
