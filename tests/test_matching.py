from bookclub.ingest import READ, TO_READ, BookRecord
from bookclub.matching import (
    build_library,
    normalise_title,
    normalise_title_base,
    series_key,
    title_number,
)


def record(person, title, author="Rebecca Yarros", **kwargs):
    return BookRecord(person=person, title=title, author=author, **kwargs)


def test_same_isbn_merges_across_members_and_platforms():
    library = build_library(
        [
            record("Lisa", "Fourth Wing", isbn13="9781649374042", shelf=TO_READ),
            # Same book from a StoryGraph export: different title styling, same ISBN.
            record(
                "Laurens",
                "Fourth Wing (The Empyrean, #1)",
                isbn13="9781649374042",
                shelf=READ,
                rating=5.0,
            ),
        ]
    )

    assert len(library.books) == 1
    book = library.books[0]
    assert book.holders == {"Lisa", "Laurens"}
    assert book.to_read_by == {"Lisa"}
    assert book.liked_by == {"Laurens"}


def test_titles_merge_without_isbns_via_fuzzy_match():
    library = build_library(
        [
            record("Lisa", "Iron Flame (The Empyrean, #2)"),
            record("Laurens", "Iron Flame"),
        ]
    )
    assert len(library.books) == 1
    assert library.books[0].holders == {"Lisa", "Laurens"}


def test_different_volumes_stay_separate():
    """Volume numbers are identity: Vol. 2 and Vol. 27 are not the same book."""
    library = build_library(
        [
            record("Lisa", "Fullmetal Alchemist, Vol. 2", author="Hiromu Arakawa"),
            record("Lisa", "Fullmetal Alchemist, Vol. 27", author="Hiromu Arakawa"),
            record("Lisa", "MASHLE 4", author="Hajime Komoto"),
            record("Lisa", "MASHLE 14", author="Hajime Komoto"),
        ]
    )
    assert len(library.books) == 4


def test_different_series_entries_stay_separate():
    library = build_library(
        [
            record("Lisa", "Alchemy of Secrets (Alchemy of Secrets, #1)"),
            record("Lisa", "Alchemy of Secrets #2 (Alchemy of Secrets, #2)"),
        ]
    )
    assert len(library.books) == 2


def test_same_title_by_different_authors_stays_separate():
    library = build_library(
        [
            record("Lisa", "Beloved", author="Toni Morrison"),
            record("Laurens", "Beloved", author="Someone Else"),
        ]
    )
    assert len(library.books) == 2


def test_read_copy_wins_over_duplicate_to_read_row():
    library = build_library(
        [
            record("Lisa", "Dune", author="Frank Herbert", shelf=TO_READ),
            record("Lisa", "Dune", author="Frank Herbert", shelf=READ, rating=4.0),
        ]
    )
    assert len(library.books) == 1
    assert library.books[0].copies["Lisa"].shelf == READ


def test_shared_to_read_lists_books_wanted_by_several_members():
    library = build_library(
        [
            record("Lisa", "Dune", author="Frank Herbert", shelf=TO_READ),
            record("Laurens", "Dune", author="Frank Herbert", shelf=TO_READ),
            record("Anna", "Dune", author="Frank Herbert", shelf=READ),
            record("Lisa", "Solo Read", author="Nobody Else", shelf=TO_READ),
        ]
    )

    shared = library.shared_to_read()
    assert [book.title for book in shared] == ["Dune"]
    assert shared[0].to_read_by == {"Lisa", "Laurens"}
    assert library.people == ["Lisa", "Laurens", "Anna"]


def test_genres_merge_from_whichever_platform_supplied_them():
    library = build_library(
        [
            record("Lisa", "Piranesi", author="Susanna Clarke", isbn13="9781526622426"),
            record(
                "Laurens",
                "Piranesi",
                author="Susanna Clarke",
                isbn13="9781526622426",
                source_genres=["Fantasy", "Mystery"],
            ),
        ]
    )
    assert library.books[0].all_genres == ["Fantasy", "Mystery"]


def test_translated_edition_matches_on_its_series_slot():
    """Goodreads keeps the English series label on translations.

    Lisa's copy has no ISBN at all, so nothing but the series slot can join these.
    """
    library = build_library(
        [
            record("Lisa", "Scythe (Arc of a Scythe, #1)", author="Neal Shusterman"),
            record(
                "Laurens",
                "Zeis (Arc of a Scythe, #1)",
                author="Neal Shusterman",
                isbn13="9789059248427",
            ),
        ]
    )

    assert len(library.books) == 1
    book = library.books[0]
    assert book.to_read_by == {"Lisa", "Laurens"}
    # The other title is kept so the club can see why these are one book.
    assert book.alternate_titles == {"Zeis (Arc of a Scythe, #1)"}


def test_series_matching_keeps_different_entries_apart():
    library = build_library(
        [
            record("Lisa", "Scythe (Arc of a Scythe, #1)", author="Neal Shusterman"),
            record("Laurens", "Thunderhead (Arc of a Scythe, #2)", author="Neal Shusterman"),
        ]
    )
    assert len(library.books) == 2


def test_same_series_slot_by_another_author_stays_separate():
    library = build_library(
        [
            record("Lisa", "Scythe (Arc of a Scythe, #1)", author="Neal Shusterman"),
            record("Laurens", "Copycat (Arc of a Scythe, #1)", author="Someone Else"),
        ]
    )
    assert len(library.books) == 2


def test_box_sets_are_not_treated_as_a_series_slot():
    """A range like #1-7 is a collection, not one entry in the series."""
    assert series_key("Harry Potter Boxed Set (Harry Potter, #1-7)", "J.K. Rowling") is None
    assert series_key("Scythe (Arc of a Scythe, #1)", "Neal Shusterman") == (
        "arc of a scythe|1|shusterman"
    )
    assert series_key("Piranesi", "Susanna Clarke") is None


def test_matching_titles_do_not_record_a_pointless_alternate():
    library = build_library(
        [
            record("Lisa", "Dune", author="Frank Herbert"),
            record("Laurens", "Dune", author="Frank Herbert"),
        ]
    )
    assert library.books[0].alternate_titles == set()


def test_title_normalisation_details():
    assert title_number("Fullmetal Alchemist, Vol. 27") == "27"
    assert title_number("Threshing Day (The Empyrean, #3.5)") == "3.5"
    assert title_number("Piranesi") == ""
    assert normalise_title_base("The Hunger Games (Hunger Games, #1)") == "hunger games"
    assert normalise_title("Iron Flame (The Empyrean, #2)") == "iron flame #2"
