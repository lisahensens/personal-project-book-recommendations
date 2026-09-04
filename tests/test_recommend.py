from bookclub.enrichment import SubjectCandidate
from bookclub.ingest import READ, TO_READ, BookRecord
from bookclub.matching import build_library
from bookclub.preferences import MemberPreferences
from bookclub.recommend import (
    COLLECTIVE,
    DISCOVERY,
    build_group_profile,
    build_profiles,
    recommend,
    suggest_discoveries,
    suggest_from_library,
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
    """Stands in for Open Library so tests never touch the network."""

    def __init__(self, by_subject, mid_series=()):
        self.by_subject = by_subject
        self.mid_series = set(mid_series)
        self.asked = []

    def books_in_subject(self, subject, limit=40):
        self.asked.append(subject)
        return self.by_subject.get(subject, [])

    def starts_its_series(self, work_key):
        return work_key not in self.mid_series


def big_and_small_library():
    """Lisa has a large fantasy shelf; Laurens has three horror books."""
    records = []
    for index in range(30):
        records.append(
            record("Lisa", f"Fantasy Book {index}", f"Author {index}", genres=["Fantasy"])
        )
    for index in range(3):
        records.append(
            record("Laurens", f"Horror Book {index}", f"Writer {index}", genres=["Horror"])
        )
    return build_library(records)


def test_small_library_counts_as_much_as_a_large_one():
    group = build_group_profile(build_profiles(big_and_small_library()))
    # 30 fantasy books versus 3 horror books, yet both members pull equally.
    assert group.genres["Fantasy"] == group.genres["Horror"]


def test_stated_genres_shape_taste_even_with_an_empty_shelf():
    library = build_library(
        [record("Lisa", "Fantasy Book", "An Author", genres=["Fantasy"])]
    )
    library.people.append("Newcomer")  # joined the club, nothing shelved yet

    profiles = build_profiles(
        library,
        {"Newcomer": MemberPreferences("Newcomer", liked_genres=["Horror"])},
    )
    group = build_group_profile(profiles)

    assert profiles["Newcomer"].book_count == 0
    assert group.genres["Horror"] > 0
    assert "Horror" in [genre for genre, _ in group.top_genres()]


def test_low_ratings_push_a_genre_down():
    library = build_library(
        [
            record("Lisa", "Loved It", "A", shelf=READ, rating=5.0, genres=["Fantasy"]),
            record("Lisa", "Hated It", "B", shelf=READ, rating=1.0, genres=["westerns"]),
        ]
    )
    profile = build_profiles(library)["Lisa"]
    assert profile.genres["Fantasy"] > 0
    # Genre spelling is normalised onto the shared vocabulary.
    assert profile.genres["Western"] < 0


def test_library_suggestions_target_members_who_lack_the_book():
    library = build_library(
        [
            record("Lisa", "Shared Read", "A", shelf=READ, rating=5.0, genres=["Fantasy"]),
            record("Laurens", "Shared Read", "A", shelf=READ, rating=4.0, genres=["Fantasy"]),
            record("Lisa", "Only Lisa Has It", "B", shelf=READ, rating=5.0, genres=["Fantasy"]),
        ]
    )
    profiles = build_profiles(library)
    group = build_group_profile(profiles)

    suggestions = suggest_from_library(library, profiles, group)
    titles = [s.title for s in suggestions]

    # Everyone already has "Shared Read", so it is not worth suggesting.
    assert "Shared Read" not in titles
    assert "Only Lisa Has It" in titles
    suggestion = next(s for s in suggestions if s.title == "Only Lisa Has It")
    assert suggestion.missing_for == ["Laurens"]
    assert suggestion.already_with == ["Lisa"]
    assert suggestion.source == COLLECTIVE
    assert any("Lisa rated it 5/5" in reason for reason in suggestion.reasons)


def test_disliked_genres_are_penalised():
    library = build_library(
        [
            record("Lisa", "Cosy Fantasy", "A", shelf=READ, rating=5.0, genres=["Fantasy"]),
            record("Lisa", "Gore Fest", "B", shelf=READ, rating=5.0, genres=["Horror"]),
        ]
    )
    preferences = {"Laurens": MemberPreferences("Laurens", disliked_genres=["Horror"])}
    library.people.append("Laurens")

    profiles = build_profiles(library, preferences)
    group = build_group_profile(profiles)
    suggestions = {s.title: s.score for s in suggest_from_library(library, profiles, group)}

    assert suggestions.get("Cosy Fantasy", 0) > suggestions.get("Gore Fest", 0)


def test_discoveries_skip_books_already_in_the_collective_library():
    library = build_library(
        [record("Lisa", "Fourth Wing (The Empyrean, #1)", "Rebecca Yarros", genres=["Fantasy"])]
    )
    client = FakeClient(
        {
            "Fantasy": [
                # Same book as Lisa's, but without the series number.
                SubjectCandidate("Fourth Wing", "Rebecca Yarros", "Fantasy", 2023, readers=900),
                SubjectCandidate("Brand New Book", "Fresh Author", "Fantasy", 2021, readers=400),
                SubjectCandidate("Too Old", "Ancient Author", "Fantasy", 1850, readers=900),
                SubjectCandidate("コーヒーが冷めないうちに", "Toshikazu", "Fantasy", 2015, readers=900),
            ]
        }
    )

    profiles = build_profiles(library)
    group = build_group_profile(profiles)
    suggestions = suggest_discoveries(library, profiles, group, client, min_year=1990)
    titles = [s.title for s in suggestions]

    assert titles == ["Brand New Book"]
    assert suggestions[0].source == DISCOVERY
    assert suggestions[0].missing_for == ["Lisa"]
    assert any("new author for the club" in reason for reason in suggestions[0].reasons)


def test_discoveries_prefer_books_matching_several_club_genres():
    library = build_library(
        [
            record("Lisa", "A Fantasy", "A", shelf=READ, rating=5.0, genres=["Fantasy"]),
            record("Laurens", "A Horror", "B", shelf=READ, rating=5.0, genres=["Horror"]),
        ]
    )
    both = SubjectCandidate("Fits Both", "Writer One", "Fantasy", 2015, readers=100)
    client = FakeClient(
        {
            "Fantasy": [
                both,
                SubjectCandidate("Fits One", "Writer Two", "Fantasy", 2015, readers=100),
            ],
            "Horror": [SubjectCandidate("Fits Both", "Writer One", "Horror", 2015, readers=100)],
        }
    )

    profiles = build_profiles(library)
    group = build_group_profile(profiles)
    suggestions = suggest_discoveries(library, profiles, group, client)

    assert suggestions[0].title == "Fits Both"
    assert set(suggestions[0].genres) == {"Fantasy", "Horror"}


def test_author_variety_is_capped():
    library = build_library(
        [
            record("Lisa", f"Book {index}", "Prolific Author", shelf=READ, rating=5.0, genres=["Fantasy"])
            for index in range(5)
        ]
    )
    library.people.append("Laurens")
    profiles = build_profiles(library)
    group = build_group_profile(profiles)

    suggestions = suggest_from_library(library, profiles, group, max_per_author=2)
    assert len(suggestions) == 2


def test_recommend_combines_both_pools_and_scales_scores():
    library = build_library(
        [
            record("Lisa", "Lisa Only", "A", shelf=READ, rating=5.0, genres=["Fantasy"]),
            record("Laurens", "Laurens Only", "B", shelf=READ, rating=5.0, genres=["Fantasy"]),
        ]
    )
    client = FakeClient(
        {"Fantasy": [SubjectCandidate("Unknown Gem", "C", "Fantasy", 2020, readers=500)]}
    )

    result = recommend(library, client=client)
    sources = {s.source for s in result.suggestions}

    assert sources == {COLLECTIVE, DISCOVERY}
    assert max(s.score for s in result.suggestions) == 100.0
    assert all(0 <= s.score <= 100 for s in result.suggestions)
    assert set(result.profiles) == {"Lisa", "Laurens"}
    assert result.group.person == "Club"
    assert result.participants == ["Lisa", "Laurens"]


def test_recommend_without_a_client_returns_only_library_suggestions():
    library = build_library(
        [
            record("Lisa", "Lisa Only", "A", shelf=READ, rating=5.0, genres=["Fantasy"]),
            record("Laurens", "Laurens Only", "B", shelf=READ, rating=5.0, genres=["Fantasy"]),
        ]
    )
    result = recommend(library, client=None)
    assert result.suggestions
    assert all(s.source == COLLECTIVE for s in result.suggestions)
