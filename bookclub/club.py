"""Club state that is about the next meeting rather than a member's taste.

Two things live here: who is joining the next book, and how everyone voted on the
shortlist. Both are kept in one small JSON file so they survive a restart.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CLUB_STATE_PATH = Path("data/club.json")

YES = "yes"
MAYBE = "maybe"
NO = "no"

# A "no" outweighs a "yes" on purpose: a book one member refuses is a bad choice
# for a club, even when the others are keen.
VOTE_WEIGHTS = {YES: 2.0, MAYBE: 1.0, NO: -2.0}
VOTE_LABELS = {YES: "Yes", MAYBE: "Maybe", NO: "No"}


@dataclass
class VoteTally:
    """How one book fared in the vote."""

    book_key: str
    title: str
    author: str
    votes: dict[str, str] = field(default_factory=dict)

    @property
    def score(self) -> float:
        return sum(VOTE_WEIGHTS.get(vote, 0.0) for vote in self.votes.values())

    def count(self, choice: str) -> int:
        return sum(1 for vote in self.votes.values() if vote == choice)

    @property
    def blocked_by(self) -> list[str]:
        return sorted(p for p, vote in self.votes.items() if vote == NO)

    def summary(self) -> str:
        parts = [
            f"{self.count(choice)} {VOTE_LABELS[choice].lower()}"
            for choice in (YES, MAYBE, NO)
            if self.count(choice)
        ]
        return ", ".join(parts) if parts else "no votes yet"


@dataclass
class ClubState:
    """Everything the club has decided, as opposed to what members like."""

    sitting_out: list[str] = field(default_factory=list)
    shortlist: list[dict] = field(default_factory=list)
    votes: dict[str, dict[str, str]] = field(default_factory=dict)

    def participants(self, people: list[str]) -> list[str]:
        """Members joining the next book, in the order they were loaded."""
        joining = [person for person in people if person not in self.sitting_out]
        # Everyone sitting out would leave nothing to work with.
        return joining or list(people)

    def is_joining(self, person: str) -> bool:
        return person not in self.sitting_out

    def set_joining(self, person: str, joining: bool) -> None:
        if joining:
            self.sitting_out = [p for p in self.sitting_out if p != person]
        elif person not in self.sitting_out:
            self.sitting_out.append(person)

    # -- shortlist and votes -------------------------------------------------

    def shortlist_keys(self) -> set[str]:
        return {entry["book_key"] for entry in self.shortlist}

    def add_to_shortlist(self, book_key: str, title: str, author: str) -> None:
        if book_key not in self.shortlist_keys():
            self.shortlist.append(
                {"book_key": book_key, "title": title, "author": author}
            )

    def remove_from_shortlist(self, book_key: str) -> None:
        self.shortlist = [e for e in self.shortlist if e["book_key"] != book_key]
        self.votes.pop(book_key, None)

    def clear_shortlist(self) -> None:
        self.shortlist = []
        self.votes = {}

    def record_vote(self, book_key: str, person: str, choice: str) -> None:
        if choice not in VOTE_WEIGHTS:
            raise ValueError(f"Unknown vote: {choice!r}")
        self.votes.setdefault(book_key, {})[person] = choice

    def clear_vote(self, book_key: str, person: str) -> None:
        self.votes.get(book_key, {}).pop(person, None)

    def vote_of(self, book_key: str, person: str) -> str | None:
        return self.votes.get(book_key, {}).get(person)

    def tallies(self, participants: list[str] | None = None) -> list[VoteTally]:
        """Vote results, best first. Only participants' votes count."""
        results = []
        for entry in self.shortlist:
            votes = dict(self.votes.get(entry["book_key"], {}))
            if participants is not None:
                votes = {p: v for p, v in votes.items() if p in participants}
            results.append(
                VoteTally(
                    book_key=entry["book_key"],
                    title=entry["title"],
                    author=entry["author"],
                    votes=votes,
                )
            )
        return sorted(results, key=lambda t: (-t.score, t.title.lower()))

    def winner(self, participants: list[str] | None = None) -> VoteTally | None:
        """The club's next read, if anyone has voted and nobody vetoed it."""
        tallies = [t for t in self.tallies(participants) if t.votes]
        if not tallies:
            return None
        best = tallies[0]
        return best if best.score > 0 else None


def load_club_state(path: str | Path = CLUB_STATE_PATH) -> ClubState:
    path = Path(path)
    if not path.exists():
        return ClubState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ClubState()
    return ClubState(
        sitting_out=list(data.get("sitting_out") or []),
        shortlist=[
            entry
            for entry in (data.get("shortlist") or [])
            if isinstance(entry, dict) and entry.get("book_key")
        ],
        votes={
            key: dict(value)
            for key, value in (data.get("votes") or {}).items()
            if isinstance(value, dict)
        },
    )


def save_club_state(state: ClubState, path: str | Path = CLUB_STATE_PATH) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "sitting_out": state.sitting_out,
                "shortlist": state.shortlist,
                "votes": state.votes,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path
