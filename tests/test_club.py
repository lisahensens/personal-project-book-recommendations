import pytest

from bookclub.club import (
    MAYBE,
    NO,
    YES,
    ClubState,
    load_club_state,
    save_club_state,
)


def shortlisted() -> ClubState:
    state = ClubState()
    state.add_to_shortlist("dune|herbert", "Dune", "Frank Herbert")
    state.add_to_shortlist("hex|heuvelt", "Hex", "Thomas Olde Heuvelt")
    return state


def test_participants_default_to_everyone():
    state = ClubState()
    assert state.participants(["Lisa", "Laurens"]) == ["Lisa", "Laurens"]


def test_a_member_can_sit_a_round_out():
    state = ClubState()
    state.set_joining("Laurens", False)
    assert state.participants(["Lisa", "Laurens"]) == ["Lisa"]
    assert state.is_joining("Laurens") is False

    state.set_joining("Laurens", True)
    assert state.participants(["Lisa", "Laurens"]) == ["Lisa", "Laurens"]


def test_everyone_sitting_out_falls_back_to_everyone():
    """Otherwise there would be no taste to work from at all."""
    state = ClubState()
    state.set_joining("Lisa", False)
    state.set_joining("Laurens", False)
    assert state.participants(["Lisa", "Laurens"]) == ["Lisa", "Laurens"]


def test_shortlist_does_not_take_duplicates():
    state = shortlisted()
    state.add_to_shortlist("dune|herbert", "Dune", "Frank Herbert")
    assert len(state.shortlist) == 2


def test_removing_a_book_also_drops_its_votes():
    state = shortlisted()
    state.record_vote("dune|herbert", "Lisa", YES)
    state.remove_from_shortlist("dune|herbert")
    assert state.shortlist_keys() == {"hex|heuvelt"}
    assert "dune|herbert" not in state.votes


def test_a_single_no_outweighs_a_yes():
    state = shortlisted()
    state.record_vote("dune|herbert", "Lisa", YES)
    state.record_vote("dune|herbert", "Laurens", NO)
    tally = next(t for t in state.tallies() if t.book_key == "dune|herbert")
    assert tally.score == pytest.approx(0.0)
    assert tally.blocked_by == ["Laurens"]


def test_winner_is_the_highest_positive_score():
    state = shortlisted()
    state.record_vote("dune|herbert", "Lisa", YES)
    state.record_vote("dune|herbert", "Laurens", MAYBE)
    state.record_vote("hex|heuvelt", "Lisa", MAYBE)

    winner = state.winner()
    assert winner is not None
    assert winner.title == "Dune"
    assert winner.summary() == "1 yes, 1 maybe"


def test_no_winner_when_the_best_book_is_not_wanted():
    state = shortlisted()
    state.record_vote("dune|herbert", "Lisa", NO)
    state.record_vote("hex|heuvelt", "Laurens", NO)
    assert state.winner() is None


def test_votes_from_members_sitting_out_do_not_count():
    state = shortlisted()
    state.set_joining("Laurens", False)
    state.record_vote("dune|herbert", "Laurens", NO)
    state.record_vote("dune|herbert", "Lisa", YES)

    participants = state.participants(["Lisa", "Laurens"])
    tally = next(t for t in state.tallies(participants) if t.book_key == "dune|herbert")
    assert tally.blocked_by == []
    assert tally.score == pytest.approx(2.0)


def test_unknown_vote_is_rejected():
    state = shortlisted()
    with pytest.raises(ValueError):
        state.record_vote("dune|herbert", "Lisa", "sure why not")


def test_club_state_survives_a_save_and_load(tmp_path):
    path = tmp_path / "club.json"
    state = shortlisted()
    state.set_joining("Laurens", False)
    state.record_vote("dune|herbert", "Lisa", YES)
    save_club_state(state, path)

    loaded = load_club_state(path)
    assert loaded.sitting_out == ["Laurens"]
    assert loaded.shortlist_keys() == {"dune|herbert", "hex|heuvelt"}
    assert loaded.vote_of("dune|herbert", "Lisa") == YES


def test_missing_and_corrupt_state_files_are_tolerated(tmp_path):
    assert load_club_state(tmp_path / "absent.json").shortlist == []
    broken = tmp_path / "club.json"
    broken.write_text("{oops", encoding="utf-8")
    assert load_club_state(broken).shortlist == []
