import json

import pytest

from bookclub.enrichment import (
    OpenLibraryClient,
    canonical_genre,
    clean_subjects,
    known_genre_names,
    match_genre,
    prioritise_for_enrichment,
    subject_slug,
)
from bookclub.ingest import READ, TO_READ, BookRecord
from bookclub.matching import build_library


@pytest.mark.parametrize(
    "subject,expected",
    [
        ("Science fiction", "Science Fiction"),
        ("sci-fi", "Science Fiction"),
        ("Fantasy fiction", "Fantasy"),
        ("Witches", "Fantasy"),
        ("Detective and mystery stories", "Mystery"),
        ("Haunted houses", "Horror"),
        ("Young adult fiction", "Young Adult"),
        ("Autobiography", "Memoir"),
    ],
)
def test_subjects_map_onto_the_genre_vocabulary(subject, expected):
    assert match_genre(subject) == expected


@pytest.mark.parametrize(
    "subject",
    [
        "Sisters",
        "Contests",
        "Insurgency",
        "Employees",
        "Accessible book",
        "collectionID:EanesChallenge",
        "NYT:series-books=2011-01-01",
        "Translations into Spanish",
    ],
)
def test_plot_topics_and_catalogue_tags_are_not_genres(subject):
    """Open Library files plot details and library tags as subjects too."""
    assert match_genre(subject) is None


def test_clean_subjects_dedupes_and_keeps_only_genres():
    subjects = [
        "Fantasy fiction",
        "Magic",  # also fantasy, so it collapses into one entry
        "Sisters",
        "Accessible book",
        "Horror tales",
    ]
    assert clean_subjects(subjects) == ["Fantasy", "Horror"]


def test_canonical_genre_keeps_niche_tastes_it_does_not_recognise():
    assert canonical_genre("nordic noir") == "Mystery"  # noir is recognised
    assert canonical_genre("bookbinding") == "Bookbinding"  # kept as typed
    assert canonical_genre("  cosy   fantasy ") == "Fantasy"


def test_known_genre_names_are_sorted_and_unique():
    names = known_genre_names()
    assert names == sorted(set(names))
    assert "Fantasy" in names


def test_subject_slug():
    assert subject_slug("Science Fiction") == "science_fiction"
    assert subject_slug("Children's Fiction") == "children_s_fiction"


def record(person, title, author="An Author", **kwargs):
    return BookRecord(person=person, title=title, author=author, **kwargs)


def test_enrichment_order_puts_shared_and_rated_books_first():
    library = build_library(
        [
            record("Lisa", "Solo Book", shelf=TO_READ),
            record("Lisa", "Shared Book", "Shared Author", shelf=READ, rating=5.0),
            record("Laurens", "Shared Book", "Shared Author", shelf=TO_READ),
            record("Lisa", "Rated Book", "Other Author", shelf=READ, rating=5.0),
        ]
    )
    order = [book.title for book in prioritise_for_enrichment(library.books)]
    assert order[0] == "Shared Book"
    assert order[1] == "Rated Book"


def test_books_that_already_have_genres_are_not_looked_up_again():
    library = build_library(
        [record("Lisa", "Piranesi", "Susanna Clarke", source_genres=["Fantasy"])]
    )
    assert prioritise_for_enrichment(library.books) == []


def test_books_already_looked_up_with_no_data_are_skipped(tmp_path):
    """A miss stays a miss; the next batch should move on to untried titles."""
    from bookclub.enrichment import LOOKUP_GENERATION

    library = build_library(
        [
            record("Lisa", "Unknown Book", "Nobody"),
            record("Lisa", "Still Waiting", "Somebody"),
        ]
    )
    unknown = next(book for book in library.books if book.title == "Unknown Book")
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps(
            {
                f"book:{unknown.key}": {
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

    titles = [book.title for book in prioritise_for_enrichment(library.books, client=client)]
    assert titles == ["Still Waiting"]


def test_stale_empty_lookups_stay_in_the_queue(tmp_path):
    library = build_library([record("Lisa", "Empire of Storms (Throne of Glass, #5)", "Sarah J. Maas")])
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

    assert [book.title for book in prioritise_for_enrichment(library.books, client=client)] == [
        "Empire of Storms (Throne of Glass, #5)"
    ]


def test_a_second_batch_does_not_revisit_known_misses(tmp_path):
    library = build_library([record("Lisa", "Unknown Book", "Nobody")])
    client = OpenLibraryClient(cache_path=tmp_path / "cache.json", offline=True)

    first = client.enrich_library(library.books)
    assert first.without_data == 1

    second = client.enrich_library(library.books)
    assert second.total == 0
    assert second.summary() == "Nothing left to look up."


def test_cached_genres_are_applied_without_network_access(tmp_path):
    library = build_library([record("Lisa", "Hex", "Thomas Olde Heuvelt", isbn13="9781250780935")])
    cache = tmp_path / "cache.json"
    cache.write_text(
        json.dumps(
            {
                "isbn:9781250780935": {
                    "subjects": ["Horror tales", "Witches", "Sisters"],
                    "work_key": "/works/OL123W",
                }
            }
        ),
        encoding="utf-8",
    )

    client = OpenLibraryClient(cache_path=cache, offline=True)
    assert client.apply_cached_genres(library.books) == 1

    book = library.books[0]
    assert book.genres == ["Horror", "Fantasy"]  # "Sisters" is a plot topic
    assert book.work_key == "/works/OL123W"


def test_offline_client_reports_no_genres_rather_than_failing(tmp_path):
    library = build_library([record("Lisa", "Unknown Book", "Nobody")])
    client = OpenLibraryClient(cache_path=tmp_path / "cache.json", offline=True)
    report = client.enrich_library(library.books)
    assert report.found == 0
    assert report.without_data == 1
    assert library.books[0].all_genres == []
