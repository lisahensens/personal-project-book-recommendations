"""Per-member genre preferences, stored as one JSON file per person."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .enrichment import canonical_genre, known_genre_names

PREFERENCES_DIR = Path("preferences")

# The same vocabulary the genre lookup uses, so a member's choice lines up with
# how books are labelled rather than sitting in a category of its own.
COMMON_GENRES = known_genre_names()


@dataclass
class MemberPreferences:
    """What a member says they like, independent of what their shelves show.

    `disliked_genres` means "rather not", and only lowers a book's score.
    `blocked_genres` means "never": those books are filtered out entirely.
    """

    person: str
    liked_genres: list[str] = field(default_factory=list)
    disliked_genres: list[str] = field(default_factory=list)
    blocked_genres: list[str] = field(default_factory=list)
    notes: str = ""

    def normalised(self) -> "MemberPreferences":
        return MemberPreferences(
            person=self.person,
            liked_genres=_dedupe([canonical_genre(g) for g in self.liked_genres]),
            disliked_genres=_dedupe([canonical_genre(g) for g in self.disliked_genres]),
            blocked_genres=_dedupe([canonical_genre(g) for g in self.blocked_genres]),
            notes=self.notes,
        )


def _dedupe(genres: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for genre in genres:
        cleaned = genre.strip()
        if cleaned:
            seen.setdefault(cleaned.lower(), cleaned)
    return list(seen.values())


def slugify(person: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", person.lower()).strip("_")
    return slug or "member"


def preference_path(person: str, directory: str | Path = PREFERENCES_DIR) -> Path:
    return Path(directory) / f"{slugify(person)}.json"


def load_preferences(
    person: str, directory: str | Path = PREFERENCES_DIR
) -> MemberPreferences:
    """Load a member's preferences, returning empty ones if none are saved yet."""
    path = preference_path(person, directory)
    if not path.exists():
        return MemberPreferences(person=person)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return MemberPreferences(person=person)
    return MemberPreferences(
        person=data.get("person", person),
        liked_genres=list(data.get("liked_genres") or []),
        disliked_genres=list(data.get("disliked_genres") or []),
        # Absent in files written before absolute-no genres existed.
        blocked_genres=list(data.get("blocked_genres") or []),
        notes=data.get("notes", ""),
    ).normalised()


def save_preferences(
    preferences: MemberPreferences, directory: str | Path = PREFERENCES_DIR
) -> Path:
    path = preference_path(preferences.person, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(preferences.normalised()), indent=2), encoding="utf-8"
    )
    return path


def load_all_preferences(
    people: list[str], directory: str | Path = PREFERENCES_DIR
) -> dict[str, MemberPreferences]:
    return {person: load_preferences(person, directory) for person in people}


def genre_options(
    known_genres: list[str] | None = None, extra: list[str] | None = None
) -> list[str]:
    """Genre choices for the UI: the common list plus anything seen in the data."""
    return _dedupe(COMMON_GENRES + list(known_genres or []) + list(extra or []))
