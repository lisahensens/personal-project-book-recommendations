"""Where each book club's files live, vs the genre cache they all share.

Exports, votes and preferences belong to one club. Open Library lookups are
keyed by book, not by member, so they sit in one cache every club can reuse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

CLUBS_DIR = Path("data/clubs")
ACTIVE_CLUB_PATH = Path("data/active_club.txt")
LEGACY_UPLOADS = Path("data/uploads")
LEGACY_CLUB_STATE = Path("data/club.json")
LEGACY_PREFERENCES = Path("preferences")

# Used only when moving the original single-club files into the new layout.
LEGACY_CLUB_NAME = "eline-laurens-lisa"

_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ClubPaths:
    """Folders and files for one club. The genre cache is not among them."""

    name: str
    root: Path

    @property
    def uploads(self) -> Path:
        return self.root / "uploads"

    @property
    def club_state(self) -> Path:
        return self.root / "club.json"

    @property
    def preferences(self) -> Path:
        return self.root / "preferences"

    def ensure(self) -> "ClubPaths":
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.preferences.mkdir(parents=True, exist_ok=True)
        return self


@dataclass(frozen=True)
class Workspace:
    """On-disk layout. Tests pass a temp root; the app uses the defaults."""

    clubs_dir: Path = CLUBS_DIR
    active_path: Path = ACTIVE_CLUB_PATH
    legacy_uploads: Path = LEGACY_UPLOADS
    legacy_club_state: Path = LEGACY_CLUB_STATE
    legacy_preferences: Path = LEGACY_PREFERENCES
    legacy_club_name: str = LEGACY_CLUB_NAME


DEFAULT = Workspace()


def slugify_club(name: str) -> str:
    """A folder-safe club name. Empty if nothing usable remains."""
    slug = _SLUG.sub("-", name.lower()).strip("-")
    return slug[:60]


def club_paths(name: str, workspace: Workspace = DEFAULT) -> ClubPaths:
    slug = slugify_club(name)
    if not slug:
        raise ValueError("Club name needs at least one letter or number.")
    return ClubPaths(name=slug, root=workspace.clubs_dir / slug)


def list_clubs(workspace: Workspace = DEFAULT) -> list[str]:
    if not workspace.clubs_dir.exists():
        return []
    return sorted(
        path.name
        for path in workspace.clubs_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )


def create_club(name: str, workspace: Workspace = DEFAULT) -> ClubPaths:
    paths = club_paths(name, workspace)
    return paths.ensure()


def load_active_club(workspace: Workspace = DEFAULT) -> str | None:
    path = workspace.active_path
    if not path.exists():
        return None
    try:
        name = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return name or None


def save_active_club(name: str, workspace: Workspace = DEFAULT) -> None:
    workspace.active_path.parent.mkdir(parents=True, exist_ok=True)
    workspace.active_path.write_text(name, encoding="utf-8")


def _has_legacy_files(workspace: Workspace) -> bool:
    uploads = workspace.legacy_uploads.exists() and any(
        workspace.legacy_uploads.glob("*.csv")
    )
    state = workspace.legacy_club_state.exists()
    prefs = workspace.legacy_preferences.exists() and any(
        workspace.legacy_preferences.glob("*.json")
    )
    return bool(uploads or state or prefs)


def _move_if_absent(source: Path, dest: Path) -> None:
    if not source.exists():
        return
    if dest.exists():
        source.unlink()
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    source.replace(dest)


def migrate_legacy_club(workspace: Workspace = DEFAULT) -> str | None:
    """Move the original single-club files into data/clubs/<name>/, once.

    Returns the club that received them, or None if there was nothing to move.
    """
    if not _has_legacy_files(workspace):
        return None
    paths = create_club(workspace.legacy_club_name, workspace)
    if workspace.legacy_uploads.exists():
        for csv in workspace.legacy_uploads.glob("*.csv"):
            _move_if_absent(csv, paths.uploads / csv.name)
    _move_if_absent(workspace.legacy_club_state, paths.club_state)
    if workspace.legacy_preferences.exists():
        for pref in workspace.legacy_preferences.glob("*.json"):
            _move_if_absent(pref, paths.preferences / pref.name)
    return paths.name
