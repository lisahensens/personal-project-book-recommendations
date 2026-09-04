from bookclub.preferences import (
    MemberPreferences,
    genre_options,
    load_all_preferences,
    load_preferences,
    preference_path,
    save_preferences,
)


def test_preferences_survive_a_save_and_load(tmp_path):
    save_preferences(
        MemberPreferences(
            person="Lisa",
            liked_genres=["Fantasy", "romantasy"],
            disliked_genres=["westerns"],
            notes="No cliffhangers please",
        ),
        directory=tmp_path,
    )

    loaded = load_preferences("Lisa", directory=tmp_path)
    assert loaded.person == "Lisa"
    assert loaded.liked_genres == ["Fantasy", "Romantasy"]  # spelling is normalised
    assert loaded.disliked_genres == ["Western"]
    assert loaded.notes == "No cliffhangers please"


def test_duplicate_genres_are_collapsed(tmp_path):
    save_preferences(
        MemberPreferences("Laurens", liked_genres=["Sci-Fi", "science fiction", "Horror"]),
        directory=tmp_path,
    )
    assert load_preferences("Laurens", directory=tmp_path).liked_genres == [
        "Science Fiction",
        "Horror",
    ]


def test_missing_file_yields_empty_preferences(tmp_path):
    preferences = load_preferences("Nobody", directory=tmp_path)
    assert preferences.person == "Nobody"
    assert preferences.liked_genres == []


def test_corrupt_file_does_not_crash(tmp_path):
    path = preference_path("Anna", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert load_preferences("Anna", directory=tmp_path).liked_genres == []


def test_names_map_to_tidy_filenames(tmp_path):
    save_preferences(MemberPreferences("Jan Willem"), directory=tmp_path)
    assert (tmp_path / "jan_willem.json").exists()


def test_load_all_preferences_covers_every_member(tmp_path):
    save_preferences(MemberPreferences("Lisa", liked_genres=["Fantasy"]), tmp_path)
    loaded = load_all_preferences(["Lisa", "Laurens"], directory=tmp_path)
    assert set(loaded) == {"Lisa", "Laurens"}
    assert loaded["Laurens"].liked_genres == []


def test_genre_options_include_data_genres_without_duplicates():
    options = genre_options(["Fantasy", "Nordic Noir"])
    assert "Nordic Noir" in options
    assert options.count("Fantasy") == 1
