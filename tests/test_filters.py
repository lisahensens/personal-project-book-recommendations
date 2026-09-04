"""Absolute-no genres, sitting a round out, and the already-read filter."""

from bookclub.enrichment import SubjectCandidate
from bookclub.ingest import READ, TO_READ, BookRecord
from bookclub.matching import build_library
from bookclub.preferences import MemberPreferences
from bookclub.recommend import (
    COLLECTIVE,
    DISCOVERY,
    READ_FILTER_ANYONE,
    READ_FILTER_INCLUDE,
    READ_FILTER_PARTICIPANTS,
    blocked_genre_set,
    build_group_profile,
    build_profiles,
    recommend,
    suggest_discoveries,
)


def record(person, title, author, shelf=TO_READ, rating=None, genres=()):
    return BookRecord(
        person=person,
        title=title,
        author=author,
        shelf=shelf,
        rating=rating,
        source_genres=list(genres),
    )


class FakeClient:
    def __init__(self, by_subject):
        self.by_subject = by_subject
        self.asked = []

    def books_in_subject(self, subject, limit=40):
        self.asked.append(subject)
        return self.by_subject.get(subject, [])

    def starts_its_series(self, work_key):
        return True


def mixed_library():
    """Lisa reads horror and fantasy; Laurens has a fantasy book of his own."""
    return build_library(
        [
            record("Lisa", "Gore Fest", "Grim Writer", shelf=READ, rating=5.0, genres=["Horror"]),
            record("Lisa", "Cosy Tale", "Kind Writer", shelf=READ, rating=5.0, genres=["Fantasy"]),
            record("Laurens", "His Own Fantasy", "Other Writer", shelf=READ, rating=5.0, genres=["Fantasy"]),
        ]
    )


def test_absolute_no_genres_are_collected_from_participants():
    library = mixed_library()
    profiles = build_profiles(
        library,
        {"Laurens": MemberPreferences("Laurens", blocked_genres=["horror"])},
    )
    assert blocked_genre_set(profiles) == {"Horror"}


def test_a_blocked_genre_removes_the_book_from_suggestions():
    library = mixed_library()
    preferences = {"Laurens": MemberPreferences("Laurens", blocked_genres=["Horror"])}

    result = recommend(library, preferences)
    titles = [s.title for s in result.suggestions]

    assert "Gore Fest" not in titles
    assert "Cosy Tale" in titles
    assert result.filtered.blocked_genres == 1
    assert "Gore Fest" in result.filtered.examples


def test_a_blocked_genre_is_not_even_browsed_for_discoveries():
    library = build_library(
        [record("Lisa", "Gore Fest", "Grim Writer", shelf=READ, rating=5.0, genres=["Horror"])]
    )
    library.people.append("Laurens")
    client = FakeClient(
        {"Horror": [SubjectCandidate("Some Horror", "Author", "Horror", 2020, readers=500)]}
    )
    preferences = {"Laurens": MemberPreferences("Laurens", blocked_genres=["Horror"])}

    profiles = build_profiles(library, preferences)
    group = build_group_profile(profiles)
    suggestions = suggest_discoveries(library, profiles, group, client)

    assert "Horror" not in client.asked
    assert suggestions == []


def test_blocking_differs_from_merely_disliking():
    """A dislike lowers the score; a block removes the book."""
    library = mixed_library()

    disliked = recommend(
        library, {"Laurens": MemberPreferences("Laurens", disliked_genres=["Horror"])}
    )
    blocked = recommend(
        library, {"Laurens": MemberPreferences("Laurens", blocked_genres=["Horror"])}
    )

    assert "Gore Fest" in [s.title for s in disliked.suggestions]
    assert "Gore Fest" not in [s.title for s in blocked.suggestions]


def read_filter_library():
    return build_library(
        [
            record("Lisa", "Lisa Read It", "A", shelf=READ, rating=5.0, genres=["Fantasy"]),
            record("Laurens", "Laurens Read It", "B", shelf=READ, rating=5.0, genres=["Fantasy"]),
            record("Lisa", "Nobody Read It", "C", shelf=TO_READ, genres=["Fantasy"]),
        ]
    )


def test_read_filter_include_keeps_books_others_have_read():
    result = recommend(read_filter_library(), read_filter=READ_FILTER_INCLUDE)
    titles = [s.title for s in result.suggestions]
    assert "Lisa Read It" in titles
    assert "Laurens Read It" in titles
    assert result.filtered.already_read == 0


def test_read_filter_anyone_drops_every_book_a_member_read():
    result = recommend(read_filter_library(), read_filter=READ_FILTER_ANYONE)
    titles = [s.title for s in result.suggestions]
    assert titles == ["Nobody Read It"]
    assert result.filtered.already_read == 2


def test_read_filter_participants_only_looks_at_who_is_joining():
    result = recommend(
        read_filter_library(),
        participants=["Lisa"],
        read_filter=READ_FILTER_PARTICIPANTS,
    )
    titles = [s.title for s in result.suggestions]

    # Laurens is sitting out, so his having read a book does not disqualify it,
    # but Lisa is joining and will not be sent a book she has read.
    assert "Laurens Read It" in titles
    assert "Lisa Read It" not in titles


def test_sitting_out_keeps_their_books_but_drops_their_taste():
    library = build_library(
        [
            record("Lisa", "Fantasy Read", "A", shelf=READ, rating=5.0, genres=["Fantasy"]),
            record("Laurens", "Horror Gem", "B", shelf=READ, rating=5.0, genres=["Horror"]),
        ]
    )

    everyone = recommend(library)
    without_laurens = recommend(library, participants=["Lisa"])

    # His book is still on offer to Lisa...
    assert "Horror Gem" in [s.title for s in without_laurens.suggestions]
    # ...but horror no longer counts as a club taste.
    assert "Horror" in everyone.group.genres
    assert without_laurens.group.genres.get("Horror", 0) == 0
    assert without_laurens.participants == ["Lisa"]


def test_sitting_out_also_drops_that_members_vetoes():
    library = build_library(
        [
            # The horror book is on Laurens's shelf, so it can be offered to Lisa.
            record("Laurens", "Horror Gem", "B", shelf=READ, rating=5.0, genres=["Horror"]),
            record("Lisa", "Cosy Tale", "A", shelf=READ, rating=5.0, genres=["Fantasy"]),
        ]
    )
    preferences = {"Laurens": MemberPreferences("Laurens", blocked_genres=["Horror"])}

    with_laurens = recommend(library, preferences)
    without_laurens = recommend(library, preferences, participants=["Lisa"])

    assert "Horror Gem" not in [s.title for s in with_laurens.suggestions]
    assert with_laurens.filtered.blocked_genres == 1
    # He is not coming, so his veto no longer applies.
    assert "Horror Gem" in [s.title for s in without_laurens.suggestions]
    assert without_laurens.filtered.blocked_genres == 0


def test_suggestions_expose_a_stable_key_for_voting():
    result = recommend(read_filter_library())
    keys = [s.book_key for s in result.suggestions]
    assert len(keys) == len(set(keys))
    assert all("|" in key for key in keys)


def test_of_source_splits_the_two_pools():
    library = read_filter_library()
    client = FakeClient(
        {"Fantasy": [SubjectCandidate("Fresh Find", "New Author", "Fantasy", 2020, readers=500)]}
    )
    result = recommend(library, client=client)

    assert [s.title for s in result.of_source(DISCOVERY)] == ["Fresh Find"]
    assert all(s.source == COLLECTIVE for s in result.of_source(COLLECTIVE))
