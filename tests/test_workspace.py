from pathlib import Path

import pytest

from bookclub.workspace import (
    Workspace,
    club_paths,
    create_club,
    list_clubs,
    load_active_club,
    migrate_legacy_club,
    save_active_club,
    slugify_club,
)


def workspace(tmp_path: Path) -> Workspace:
    return Workspace(
        clubs_dir=tmp_path / "clubs",
        active_path=tmp_path / "active.txt",
        legacy_uploads=tmp_path / "uploads",
        legacy_club_state=tmp_path / "club.json",
        legacy_preferences=tmp_path / "prefs",
        legacy_club_name="eline-laurens-lisa",
    )


def test_slugify_club_is_folder_safe():
    assert slugify_club("Thursday Club") == "thursday-club"
    assert slugify_club("  Lisa & friends!! ") == "lisa-friends"
    assert slugify_club("...") == ""


def test_create_club_makes_uploads_and_preferences(tmp_path):
    ws = workspace(tmp_path)
    paths = create_club("Thursday Club", ws)
    assert paths.name == "thursday-club"
    assert paths.uploads.is_dir()
    assert paths.preferences.is_dir()
    assert list_clubs(ws) == ["thursday-club"]


def test_blank_club_name_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        club_paths("!!!", workspace(tmp_path))


def test_active_club_survives_a_restart(tmp_path):
    ws = workspace(tmp_path)
    assert load_active_club(ws) is None
    save_active_club("thursday-club", ws)
    assert load_active_club(ws) == "thursday-club"


def test_legacy_files_move_into_the_first_club(tmp_path):
    ws = workspace(tmp_path)
    ws.legacy_uploads.mkdir()
    (ws.legacy_uploads / "goodreads_library_export_lisa.csv").write_text("x")
    ws.legacy_club_state.write_text('{"sitting_out": []}', encoding="utf-8")
    ws.legacy_preferences.mkdir()
    (ws.legacy_preferences / "lisa.json").write_text("{}", encoding="utf-8")

    name = migrate_legacy_club(ws)
    assert name == "eline-laurens-lisa"
    paths = club_paths(name, ws)
    assert (paths.uploads / "goodreads_library_export_lisa.csv").exists()
    assert paths.club_state.exists()
    assert (paths.preferences / "lisa.json").exists()
    assert not (ws.legacy_uploads / "goodreads_library_export_lisa.csv").exists()
    assert not ws.legacy_club_state.exists()


def test_migration_is_idempotent_and_does_not_overwrite(tmp_path):
    ws = workspace(tmp_path)
    first = create_club(ws.legacy_club_name, ws)
    (first.uploads / "already.csv").write_text("kept")
    ws.legacy_uploads.mkdir()
    (ws.legacy_uploads / "already.csv").write_text("new")
    (ws.legacy_uploads / "extra.csv").write_text("extra")

    migrate_legacy_club(ws)
    assert (first.uploads / "already.csv").read_text() == "kept"
    assert (first.uploads / "extra.csv").read_text() == "extra"
    assert migrate_legacy_club(ws) is None
    assert not (ws.legacy_uploads / "already.csv").exists()


def test_nothing_to_migrate_when_the_old_folders_are_empty(tmp_path):
    assert migrate_legacy_club(workspace(tmp_path)) is None
