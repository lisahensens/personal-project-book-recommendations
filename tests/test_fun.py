import random

from bookclub.fun import (
    club_awards,
    cover_url,
    genre_chip,
    genre_icon,
    spin_roulette,
    taste_match,
)
from bookclub.ingest import READ, TO_READ, BookRecord
from bookclub.matching import build_library


def record(person, title, author="An Author", shelf=TO_READ, rating=None, genres=()):
    return BookRecord(
        person=person,
        title=title,
        author=author,
        shelf=shelf,
        rating=rating,
        source_genres=list(genres),
    )


def test_genre_icons_cover_the_vocabulary_and_fall_back():
    assert genre_icon("Fantasy") == "🐉"
    assert genre_icon("sci-fi") == "🚀"  # normalised first
    assert genre_icon("Bookbinding") == "📚"  # unknown genre still renders
    assert genre_chip("Horror").endswith("Horror")


def test_cover_url_prefers_a_cover_id_then_isbn_then_nothing():
    assert cover_url(12345) == "https://covers.openlibrary.org/b/id/12345-M.jpg"
    assert cover_url(None, "9780441013593") == (
        "https://covers.openlibrary.org/b/isbn/9780441013593-M.jpg"
    )
    assert cover_url(None, "") is None
    assert cover_url(12345, size="L").endswith("-L.jpg")


def award_library():
    records = [
        record("Lisa", f"Fantasy {i}", f"Writer {i}", shelf=READ, rating=5.0, genres=["Fantasy"])
        for i in range(6)
    ]
    records += [
        record("Lisa", f"Wanted {i}", f"Author {i}", shelf=TO_READ, genres=["Romance"])
        for i in range(4)
    ]
    records += [
        record("Laurens", "Shared Fantasy", "Writer 0", shelf=READ, rating=3.0, genres=["Fantasy"]),
        record("Laurens", "Horror One", "Grim", shelf=READ, rating=3.0, genres=["Horror"]),
        record("Laurens", "Mystery One", "Sleuth", shelf=READ, rating=3.0, genres=["Mystery"]),
        record("Laurens", "Poetry One", "Bard", shelf=READ, rating=3.0, genres=["Poetry"]),
        record("Laurens", "Travel One", "Rover", shelf=READ, rating=3.0, genres=["Travel"]),
    ]
    return build_library(records)


def test_club_awards_are_handed_out_with_a_winner_and_a_reason():
    awards = {award.title: award for award in club_awards(award_library())}

    assert "Genre twins" in awards
    assert "Deepest to-read pile" in awards
    assert awards["Deepest to-read pile"].winner == "Lisa"
    assert "4 books" in awards["Deepest to-read pile"].detail
    # Lisa rates everything 5, Laurens rates everything 3.
    assert awards["Kindest critic"].winner == "Lisa"
    # Laurens reads across more genres despite the far smaller shelf.
    assert awards["Most eclectic reader"].winner == "Laurens"
    assert all(award.icon for award in club_awards(award_library()))


def test_awards_on_an_empty_library_are_simply_empty():
    assert club_awards(build_library([])) == []


def test_series_devotee_needs_a_real_run_of_a_series():
    library = build_library(
        [
            record("Lisa", f"Volume (Some Series, #{i})", "One Writer", shelf=READ)
            for i in range(1, 5)
        ]
    )
    awards = {award.title: award for award in club_awards(library)}
    assert awards["Series devotee"].winner == "Lisa"
    assert "Some Series" in awards["Series devotee"].detail


def test_taste_match_is_a_percentage_best_first():
    matches = taste_match(award_library())
    assert matches
    assert all(0 <= percentage <= 100 for _, _, percentage in matches)


def test_roulette_prefers_books_more_than_one_member_wants():
    library = build_library(
        [
            record("Lisa", "Shared Want", "A", shelf=TO_READ),
            record("Laurens", "Shared Want", "A", shelf=TO_READ),
            record("Lisa", "Only Lisa Wants", "B", shelf=TO_READ),
        ]
    )
    random.seed(0)
    picks = {spin_roulette(library).title for _ in range(15)}
    assert picks == {"Shared Want"}


def test_roulette_falls_back_when_nothing_is_shared():
    library = build_library([record("Lisa", "Only Lisa Wants", "B", shelf=TO_READ)])
    assert spin_roulette(library).title == "Only Lisa Wants"


def test_roulette_returns_nothing_when_there_is_nothing_to_read():
    library = build_library([record("Lisa", "Finished", "B", shelf=READ)])
    assert spin_roulette(library) is None
