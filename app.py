"""Streamlit app: shared to-reads, club statistics, suggestions and voting."""

from __future__ import annotations

from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from bookclub.club import (
    MAYBE,
    NO,
    VOTE_LABELS,
    YES,
    ClubState,
    load_club_state,
    save_club_state,
)
from bookclub.enrichment import (
    OpenLibraryClient,
    canonical_genre,
    prioritise_for_enrichment,
)
from bookclub.fun import (
    book_cover_url,
    club_awards,
    cover_url,
    genre_chip,
    genre_icon,
    spin_roulette,
    taste_match,
)
from bookclub.ingest import (
    READ,
    TO_READ,
    UnknownExportFormat,
    load_export,
    person_name_from_filename,
)
from bookclub.matching import build_library
from bookclub.preferences import (
    MemberPreferences,
    genre_options,
    load_all_preferences,
    load_preferences,
    save_preferences,
)
from bookclub.recommend import (
    COLLECTIVE,
    READ_FILTER_ANYONE,
    READ_FILTER_INCLUDE,
    READ_FILTER_LABELS,
    READ_FILTER_PARTICIPANTS,
    recommend,
)
from bookclub.stats import (
    club_totals,
    member_summaries,
    overlap_pairs,
    shared_authors,
    shared_genres,
)
from bookclub.workspace import (
    club_paths,
    create_club,
    list_clubs,
    load_active_club,
    migrate_legacy_club,
    save_active_club,
)

st.set_page_config(page_title="Book Club", page_icon="📚", layout="wide")


# ---------------------------------------------------------------- data loading


def widget_key(suffix: str) -> str:
    """Keep Streamlit widget state from leaking between clubs."""
    club = st.session_state.get("club_name") or "club"
    return f"{club}_{suffix}"


def forget_club_session() -> None:
    for key in (
        "signature",
        "library",
        "problems",
        "club",
        "club_loaded_for",
        "roulette",
        "roulette_spun",
        "celebrated",
        "last_enrichment",
    ):
        st.session_state.pop(key, None)


def activate_club(name: str) -> None:
    forget_club_session()
    st.session_state["club_name"] = name
    save_active_club(name)


def ensure_club_session() -> None:
    """Pick a club before anything else reads files. Migrates the old layout once."""
    migrated = migrate_legacy_club()
    clubs = list_clubs()
    chosen = st.session_state.get("club_name") or load_active_club() or migrated
    if chosen not in clubs:
        chosen = clubs[0] if clubs else None
    if chosen:
        st.session_state["club_name"] = chosen
        save_active_club(chosen)


def get_paths():
    name = st.session_state.get("club_name")
    if not name:
        return None
    return club_paths(name).ensure()


def stored_exports() -> list[Path]:
    """CSV exports kept on disk, so the club does not re-upload every session."""
    paths = get_paths()
    if paths is None:
        return []
    return sorted(paths.uploads.glob("*.csv"))


def load_records(paths: list[Path]) -> tuple[list, list[str]]:
    records, problems = [], []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            records.extend(load_export(text, person_name_from_filename(path.name)))
        except UnknownExportFormat as error:
            problems.append(f"{path.name}: {error}")
        except OSError as error:
            problems.append(f"{path.name}: could not be read ({error})")
    return records, problems


def library_signature(files: list[Path]) -> tuple:
    club = st.session_state.get("club_name")
    return (club, tuple((path.name, path.stat().st_mtime, path.stat().st_size) for path in files))


def get_library():
    """Build the collective library once per club and set of files, then keep it."""
    paths = get_paths()
    if paths is None:
        return build_library([]), []
    files = stored_exports()
    signature = library_signature(files)
    if st.session_state.get("signature") != signature:
        records, problems = load_records(files)
        library = build_library(records)
        # Genres looked up in earlier sessions live in the cache; apply them
        # straight away so restarting the app does not lose the genre data.
        # The cache is shared across clubs, so a book another club already
        # looked up does not need fetching again.
        get_client().apply_cached_genres(library.books)
        st.session_state["signature"] = signature
        st.session_state["library"] = library
        st.session_state["problems"] = problems
    return st.session_state.get("library"), st.session_state.get("problems", [])


def get_client() -> OpenLibraryClient:
    if "client" not in st.session_state:
        st.session_state["client"] = OpenLibraryClient()
    return st.session_state["client"]


def get_club():
    paths = get_paths()
    if paths is None:
        return ClubState()
    if st.session_state.get("club_loaded_for") != paths.name:
        st.session_state["club"] = load_club_state(paths.club_state)
        st.session_state["club_loaded_for"] = paths.name
    return st.session_state["club"]


def store_club() -> None:
    paths = get_paths()
    if paths is None:
        return
    save_club_state(get_club(), paths.club_state)


def all_known_genres(library) -> list[str]:
    genres = {canonical_genre(g) for book in library.books for g in book.all_genres}
    return sorted(genres)


def show_cover(url: str | None, title: str, width: int = 110) -> None:
    """Covers are often missing, so fall back to something readable."""
    if url:
        st.image(url, width=width)
    else:
        st.markdown(
            f"<div style='width:{width}px;height:{int(width * 1.5)}px;"
            "display:flex;align-items:center;justify-content:center;"
            "background:#EDE3D4;border-radius:6px;padding:6px;"
            "font-size:0.75rem;text-align:center;color:#5B5560;'>"
            f"{title[:60]}</div>",
            unsafe_allow_html=True,
        )


# ------------------------------------------------------------------ upload tab


def render_upload(library) -> None:
    st.subheader("Member exports")
    st.write(
        "Upload one CSV per member. Goodreads and StoryGraph exports are both "
        "recognised automatically, and the member's name is taken from the filename."
    )

    uploads = st.file_uploader(
        "Goodreads or StoryGraph CSV exports",
        type="csv",
        accept_multiple_files=True,
    )
    if uploads:
        dest = get_paths()
        if dest is None:
            st.error("Create a club in the sidebar before uploading exports.")
            return
        dest.uploads.mkdir(parents=True, exist_ok=True)
        for upload in uploads:
            (dest.uploads / upload.name).write_bytes(upload.getbuffer())
        st.session_state.pop("signature", None)
        st.success(f"Saved {len(uploads)} export(s).")
        st.rerun()

    paths = stored_exports()
    if not paths:
        st.info("No exports yet. Upload a CSV above to get started.")
        return

    rows = []
    for path in paths:
        person = person_name_from_filename(path.name)
        copies = [b.copies[person] for b in library.books if person in b.copies]
        rows.append(
            {
                "File": path.name,
                "Member": person,
                "Books": len(copies),
                "To read": sum(1 for c in copies if c.shelf == TO_READ),
                "Read": sum(1 for c in copies if c.shelf == READ),
                "Platform": copies[0].source if copies else "unknown",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    with st.expander("Remove an export"):
        target = st.selectbox(
            "File", [p.name for p in paths], key=widget_key("remove_target")
        )
        if st.button("Delete this export", type="secondary"):
            dest = get_paths()
            if dest is not None:
                (dest.uploads / target).unlink(missing_ok=True)
            st.session_state.pop("signature", None)
            st.rerun()

    st.divider()
    render_enrichment(library)


def render_enrichment(library) -> None:
    st.subheader("Genre lookup")

    # st.rerun() right after finishing would wipe a st.success() before the
    # browser ever renders it, so the result is parked in session_state and
    # shown here, on the far side of the rerun, where it actually sticks.
    last_run = st.session_state.pop("last_enrichment", None)
    if last_run is not None:
        if last_run["found"]:
            st.success(last_run["summary"])
        else:
            st.warning(last_run["summary"])

    client = get_client()
    # Filter here rather than only inside prioritise_for_enrichment: Streamlit
    # often reloads app.py while still holding an older enrichment module, and
    # a new keyword argument would crash this page.
    untried = [
        book
        for book in library.books
        if not book.all_genres and client.cached_genres(book) is None
    ]
    to_fetch = prioritise_for_enrichment(untried)
    without_genres = sum(1 for book in library.books if not book.all_genres)
    have_genres = len(library.books) - without_genres
    already_tried = without_genres - len(to_fetch)
    st.write(
        f"{have_genres} of {len(library.books)} books have genre data. "
        "Goodreads exports contain no genres, so they are fetched from Open Library "
        "and cached on disk. Books shared by several members are fetched first."
    )
    if already_tried:
        st.caption(
            f"{already_tried} already looked up with no genre data on Open Library "
            "are skipped until a later lookup improvement retries them."
        )
    if not to_fetch:
        if without_genres:
            st.success(
                "Nothing left to look up. "
                f"{already_tried} book(s) had no genre data on Open Library."
            )
        else:
            st.success("Every book has genre data.")
        return

    looked_at = have_genres + already_tried
    st.progress(looked_at / max(len(library.books), 1))
    batch = st.slider("Books to look up now", 10, 500, 100, step=10)
    st.caption(
        "Roughly one to two seconds per book. Results are cached, so you can stop "
        "and continue later; stated genre preferences work even without this step."
    )
    if st.button("Fetch genres", type="primary"):
        # The spinner is the one piece of feedback Streamlit guarantees shows up
        # immediately and stays visible for the whole call, unlike the tiny
        # "running" icon in the corner, which is easy to miss and says nothing
        # about progress.
        with st.spinner(f"Looking up genres for up to {batch} books..."):
            bar = st.progress(0.0, text="Contacting Open Library...")
            tally = st.empty()
            report = client.enrich_library(
                to_fetch,
                limit=batch,
                progress=lambda state: _show_enrichment_progress(bar, tally, state),
            )
            bar.empty()
            tally.empty()
        st.session_state["last_enrichment"] = {
            "found": report.found,
            "summary": report.summary(),
        }
        st.rerun()


def _show_enrichment_progress(bar, tally, state) -> None:
    """Name the book being looked up, so a slow batch still feels alive."""
    bar.progress(
        state.fraction,
        text=f"{state.index} of {state.total}: {state.title[:60]}",
    )
    line = f"{state.found} with genres, {state.without_data} with no data on file"
    eta = state.eta_text()
    tally.caption(f"{line} - {eta}" if eta else line)


# ------------------------------------------------------------- preferences tab


def render_preferences(library) -> None:
    st.subheader("What each member likes")
    st.write(
        "Stated preferences carry more weight than shelf history, so members with "
        "small or unrepresentative libraries still shape the suggestions."
    )
    if not library.people:
        st.info("Upload some exports first.")
        return

    options = genre_options(all_known_genres(library))
    prefs_dir = get_paths().preferences
    for person in library.people:
        existing = load_preferences(person, directory=prefs_dir)
        with st.form(widget_key(f"prefs_{person}")):
            st.markdown(f"### {person}")
            liked = st.multiselect(
                "Genres they like",
                options=genre_options(options, existing.liked_genres),
                default=existing.liked_genres,
                key=widget_key(f"liked_{person}"),
                help="Weighted more heavily than what is on their shelves.",
            )
            disliked = st.multiselect(
                "Rather not (lowers the score)",
                options=genre_options(options, existing.disliked_genres),
                default=existing.disliked_genres,
                key=widget_key(f"disliked_{person}"),
            )
            blocked = st.multiselect(
                "Absolute no - never suggest these",
                options=genre_options(options, existing.blocked_genres),
                default=existing.blocked_genres,
                key=widget_key(f"blocked_{person}"),
                help=(
                    "Removed from suggestions entirely. One member's absolute no "
                    "is enough to drop a book, since the club reads together."
                ),
            )
            extra = st.text_input(
                "Liked genres not in the list (comma separated)",
                key=widget_key(f"extra_{person}"),
                placeholder="cosy fantasy, nordic noir",
            )
            notes = st.text_area(
                "Notes for the club",
                value=existing.notes,
                key=widget_key(f"notes_{person}"),
            )
            if st.form_submit_button("Save", type="primary"):
                extras = [part.strip() for part in extra.split(",") if part.strip()]
                save_preferences(
                    MemberPreferences(
                        person=person,
                        liked_genres=liked + extras,
                        disliked_genres=disliked,
                        blocked_genres=blocked,
                        notes=notes,
                    ),
                    directory=prefs_dir,
                )
                st.success(f"Saved preferences for {person}.")
                st.rerun()


# --------------------------------------------------------------- overlap tab


def render_overlap(library) -> None:
    totals = club_totals(library)
    columns = st.columns(5)
    columns[0].metric("Members", totals["members"])
    columns[1].metric("Unique books", totals["unique_books"])
    columns[2].metric("On a to-read list", totals["unique_to_read"])
    columns[3].metric("Shared to-reads", totals["shared_to_read"])
    columns[4].metric("Authors", totals["distinct_authors"])

    render_awards(library)

    st.subheader("Books more than one member wants to read")
    shared = library.shared_to_read()
    if shared:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Title": book.title,
                        "Also shelved as": ", ".join(sorted(book.alternate_titles)),
                        "Author": book.author,
                        "Members": len(book.to_read_by),
                        "Who": ", ".join(sorted(book.to_read_by)),
                        "Genres": ", ".join(book.all_genres[:4]),
                    }
                    for book in shared
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        # A merged translation looks like a bug unless we say so out loud, and it
        # would otherwise be buried somewhere in a long scrolling table.
        translated = [book for book in shared if book.alternate_titles]
        if translated:
            st.success(
                "Matched across languages: "
                + "; ".join(
                    f"**{book.title}** is also shelved as "
                    + " / ".join(sorted(book.alternate_titles))
                    for book in translated
                )
            )
        st.caption(
            "The 'also shelved as' column shows translated editions that were "
            "matched to the same book."
        )
    else:
        st.info(
            "No book is on two members' to-read lists yet. The Suggestions tab still "
            "works: it draws on everyone's shelves plus new discoveries."
        )

    st.subheader("How similar are members' shelves")
    pairs = overlap_pairs(library)
    if pairs:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Member": pair.left,
                        "Member ": pair.right,
                        "Taste match": f"{round(pair.genre_similarity * 100)}%",
                        "Shared to-reads": pair.shared_to_read,
                        "Shared books (any shelf)": pair.shared_any_shelf,
                        "Shared authors": pair.shared_authors,
                    }
                    for pair in pairs
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("Add a second member's export to compare shelves.")

    left, right = st.columns(2)
    with left:
        st.subheader("Genres several members read")
        genre_rows = shared_genres(library)
        if genre_rows:
            frame = pd.DataFrame(
                [
                    {
                        "Genre": genre_chip(genre),
                        "Books": count,
                        "Members": ", ".join(people),
                    }
                    for genre, count, people in genre_rows
                ]
            )
            chart = (
                alt.Chart(frame.head(15))
                .mark_bar(cornerRadiusEnd=4)
                .encode(
                    x=alt.X("Books:Q"),
                    y=alt.Y("Genre:N", sort="-x", title=None),
                    tooltip=["Genre", "Books", "Members"],
                )
            )
            st.altair_chart(chart, width="stretch")
            st.dataframe(frame, hide_index=True, width="stretch")
        else:
            st.info("Fetch genres on the Exports tab to see shared genres.")

    with right:
        st.subheader("Authors several members shelve")
        author_rows = shared_authors(library)
        if author_rows:
            st.dataframe(
                pd.DataFrame(
                    [
                        {"Author": author, "Books": count, "Members": ", ".join(people)}
                        for author, count, people in author_rows
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        else:
            st.info("No author appears on more than one member's shelves yet.")

    st.subheader("Per member")
    for summary in member_summaries(library):
        average = (
            f"{summary.average_rating:.2f}" if summary.average_rating else "no ratings"
        )
        with st.expander(
            f"{summary.person} - {summary.total_books} books, "
            f"{summary.to_read} to read, average rating {average}"
        ):
            columns = st.columns(2)
            columns[0].write("**Most common genres**")
            columns[0].write(
                pd.DataFrame(
                    [(genre_chip(g), n) for g, n in summary.top_genres],
                    columns=["Genre", "Books"],
                )
                if summary.top_genres
                else "No genre data yet."
            )
            columns[1].write("**Most shelved authors**")
            columns[1].write(
                pd.DataFrame(summary.top_authors, columns=["Author", "Books"])
            )


def render_awards(library) -> None:
    awards = club_awards(library)
    if not awards:
        return
    st.subheader("Club awards")
    for row_start in range(0, len(awards), 3):
        for column, award in zip(
            st.columns(3), awards[row_start : row_start + 3]
        ):
            with column:
                with st.container(border=True):
                    st.markdown(f"### {award.icon}")
                    st.markdown(f"**{award.title}**")
                    st.markdown(award.winner)
                    st.caption(award.detail)

    matches = taste_match(library)
    if matches:
        st.caption(
            "Taste match: "
            + " | ".join(f"{left} + {right}: {pct}%" for left, right, pct in matches)
        )


# ------------------------------------------------------------ suggestions tab


def render_suggestions(library, settings: dict) -> None:
    st.subheader("Suggestions for the club")
    if not library.people:
        st.info("Upload some exports first.")
        return

    club = get_club()
    preferences = load_all_preferences(
        library.people, directory=get_paths().preferences
    )
    client = get_client() if settings["include_discoveries"] else None

    # The language and series checks add a few requests on a cold cache, so the
    # first load of this tab can take a moment.
    with st.spinner("Ranking suggestions..."):
        result = recommend(
            library,
            preferences,
            client=client,
            library_limit=settings["library_limit"],
            discovery_limit=settings["discovery_limit"],
            min_year=settings["min_year"],
            participants=settings["participants"],
            read_filter=settings["read_filter"],
        )

    top_genres = result.group.top_genres(8)
    if top_genres:
        st.caption(
            "The club's blended taste: "
            + ", ".join(genre_chip(genre) for genre, _ in top_genres)
            + ". Every member counts equally, however large their library."
        )
    else:
        st.warning(
            "No genre signal yet. Add liked genres on the Preferences tab, or fetch "
            "genres on the Exports tab."
        )

    sitting_out = [p for p in library.people if p not in result.participants]
    if sitting_out:
        st.info(
            f"{', '.join(sitting_out)} sitting this one out: their taste and vetoes "
            "are ignored, but their books stay in the pool."
        )

    filtered = result.filtered
    # An older Streamlit session can still be holding a FilterStats from before
    # this field existed; treat that as "nothing hidden for series".
    mid_series = getattr(filtered, "mid_series", 0)
    if filtered.blocked_genres or filtered.already_read or mid_series:
        notes = []
        if filtered.blocked_genres:
            notes.append(f"{filtered.blocked_genres} hidden by absolute-no genres")
        if filtered.already_read:
            notes.append(f"{filtered.already_read} hidden as already read")
        if mid_series:
            notes.append(f"{mid_series} hidden as mid-series")
        with st.expander(" and ".join(notes)):
            if mid_series:
                st.write(
                    "Books partway into a series are only offered to members who "
                    "already have the earlier volumes, and new series are only "
                    "suggested from book one."
                )
            blocked = sorted(result.group.blocked_genres)
            if blocked:
                st.write("Absolute no: " + ", ".join(genre_chip(g) for g in blocked))
            if filtered.examples:
                st.write("For example: " + ", ".join(filtered.examples))
            st.caption(f"Already-read rule: {READ_FILTER_LABELS[settings['read_filter']]}")

    if not result.suggestions:
        st.info("No suggestions yet. Try relaxing the filters in the sidebar.")
        return

    sources = st.multiselect(
        "Show",
        ["In someone's library", "New discovery"],
        default=["In someone's library", "New discovery"],
    )
    chosen = [s for s in result.suggestions if s.source_label in sources]

    for row_start in range(0, len(chosen), 3):
        for column, suggestion in zip(
            st.columns(3), chosen[row_start : row_start + 3]
        ):
            with column:
                render_suggestion_card(suggestion, club)

    st.divider()
    frame = pd.DataFrame(
        [
            {
                "Title": s.title,
                "Author": s.author,
                "Match": s.score,
                "Source": s.source_label,
                "Genres": ", ".join(s.genres[:5]),
                "Already with": ", ".join(s.already_with),
                "New for": ", ".join(s.missing_for),
                "Why": " | ".join(s.reasons),
            }
            for s in chosen
        ]
    )
    st.download_button(
        "Download suggestions as CSV",
        frame.to_csv(index=False).encode("utf-8"),
        file_name="bookclub_suggestions.csv",
        mime="text/csv",
    )

    with st.expander("How each member's taste was read"):
        for person, profile in result.profiles.items():
            stated = ", ".join(profile.stated_genres) or "none stated"
            inferred = ", ".join(genre for genre, _ in profile.top_genres(6)) or "none"
            blocked = ", ".join(sorted(profile.blocked_genres)) or "nothing"
            joining = "joining" if person in result.participants else "sitting out"
            st.markdown(
                f"**{person}** ({joining}) - {profile.book_count} books on their "
                f"shelves  \nStated genres: {stated}  \n"
                f"Strongest signals: {inferred}  \nAbsolute no: {blocked}"
            )


def render_suggestion_card(suggestion, club) -> None:
    with st.container(border=True):
        cover, details = st.columns([1, 2])
        with cover:
            show_cover(
                cover_url(suggestion.cover_id, suggestion.isbn),
                suggestion.title,
                width=90,
            )
        with details:
            st.markdown(f"**{suggestion.title}**")
            st.caption(suggestion.author)
            st.markdown(f"Match **{suggestion.score:.0f}** · {suggestion.source_label}")
        if suggestion.genres:
            st.caption(" ".join(genre_chip(g) for g in suggestion.genres[:3]))
        for reason in suggestion.reasons[:3]:
            st.write(f"- {reason}")
        if suggestion.source == COLLECTIVE and suggestion.missing_for:
            st.caption("New for: " + ", ".join(suggestion.missing_for))
        if suggestion.alternate_titles:
            st.caption("Also shelved as: " + ", ".join(suggestion.alternate_titles))

        on_shortlist = suggestion.book_key in club.shortlist_keys()
        if on_shortlist:
            st.button(
                "On the shortlist",
                key=widget_key(f"short_{suggestion.book_key}"),
                disabled=True,
                width="stretch",
            )
        elif st.button(
            "Add to shortlist",
            key=widget_key(f"short_{suggestion.book_key}"),
            width="stretch",
        ):
            club.add_to_shortlist(
                suggestion.book_key, suggestion.title, suggestion.author
            )
            store_club()
            st.rerun()


# -------------------------------------------------------------- next read tab


def render_next_read(library, settings: dict) -> None:
    club = get_club()
    participants = settings["participants"]

    st.subheader("Vote on the shortlist")
    st.write(
        "Add books from the Suggestions tab, then everyone votes. A no counts "
        "double against a yes, so nobody gets dragged through a book they dread."
    )

    if not club.shortlist:
        st.info("The shortlist is empty. Add a few books on the Suggestions tab.")
    else:
        tallies = club.tallies(participants)
        for tally in tallies:
            with st.container(border=True):
                header, score = st.columns([5, 1])
                header.markdown(f"**{tally.title}**")
                header.caption(f"{tally.author} · {tally.summary()}")
                score.metric("Score", f"{tally.score:.0f}")

                columns = st.columns(len(participants) + 1)
                for column, person in zip(columns, participants):
                    with column:
                        current = club.vote_of(tally.book_key, person)
                        choice = st.radio(
                            person,
                            [YES, MAYBE, NO],
                            index=[YES, MAYBE, NO].index(current) if current else None,
                            format_func=lambda value: VOTE_LABELS[value],
                            key=widget_key(f"vote_{tally.book_key}_{person}"),
                            horizontal=True,
                        )
                        if choice and choice != current:
                            club.record_vote(tally.book_key, person, choice)
                            store_club()
                            st.rerun()
                with columns[-1]:
                    if st.button("Remove", key=widget_key(f"drop_{tally.book_key}")):
                        club.remove_from_shortlist(tally.book_key)
                        store_club()
                        st.rerun()
                if tally.blocked_by:
                    st.caption("Vetoed by " + ", ".join(tally.blocked_by))

        winner = club.winner(participants)
        if winner:
            st.success(
                f"🏆 The club's next read: **{winner.title}** by {winner.author} "
                f"({winner.summary()})"
            )
            if not st.session_state.get("celebrated") == winner.book_key:
                st.balloons()
                st.session_state["celebrated"] = winner.book_key
        elif any(t.votes for t in tallies):
            st.warning("No book has a positive score yet. Keep voting or add more.")

        if st.button("Clear the shortlist and votes"):
            club.clear_shortlist()
            store_club()
            st.rerun()

    st.divider()
    st.subheader("Book roulette")
    st.write(
        "Cannot decide? Spin for a random book from the pile you all want to read."
    )
    if st.button("🎲 Spin", type="primary"):
        st.session_state["roulette"] = spin_roulette(library, participants)
        st.session_state["roulette_spun"] = True

    picked = st.session_state.get("roulette")
    if picked is not None:
        cover, details = st.columns([1, 3])
        with cover:
            show_cover(book_cover_url(picked), picked.title, width=140)
        with details:
            st.markdown(f"### {picked.title}")
            st.write(picked.author)
            if picked.all_genres:
                st.caption(" ".join(genre_chip(g) for g in picked.all_genres[:4]))
            wanted = sorted(picked.to_read_by)
            if wanted:
                st.write("On the to-read list of: " + ", ".join(wanted))
            if picked.alternate_titles:
                st.caption("Also shelved as: " + ", ".join(sorted(picked.alternate_titles)))
        if st.session_state.pop("roulette_spun", False):
            st.balloons()
    elif st.session_state.get("roulette_spun"):
        st.info("Nothing to spin yet: no shared to-read books.")


# ------------------------------------------------------------------------ main


def main() -> None:
    st.title("📚 Book Club")
    ensure_club_session()

    library, problems = get_library()
    for problem in problems:
        st.warning(problem)

    club = get_club()

    with st.sidebar:
        st.header("Club")
        clubs = list_clubs()
        current = st.session_state.get("club_name")
        if clubs:
            index = clubs.index(current) if current in clubs else 0
            picked = st.selectbox(
                "Whose shelves are these?",
                clubs,
                index=index,
                help="Each club has its own exports, votes and preferences. "
                "Genre lookups are shared, so a book fetched once is reused.",
            )
            if picked != current:
                activate_club(picked)
                st.rerun()
        else:
            st.info("Create a club below, then upload everyone's CSV exports.")

        new_name = st.text_input(
            "Start another club",
            placeholder="thursday-club",
            key="new_club_name",
        )
        if st.button("Create club"):
            try:
                created = create_club(new_name)
                activate_club(created.name)
                st.rerun()
            except ValueError as error:
                st.error(str(error))
        st.caption(
            "Genre lookups live in data/cache and are reused across every club."
        )

        st.divider()
        st.header("Next book")
        if library is not None and library.people:
            st.caption("Who is joining? Someone sitting out stops steering the picks.")
            for person in library.people:
                joining = st.checkbox(
                    person,
                    value=club.is_joining(person),
                    key=widget_key(f"joining_{person}"),
                )
                if joining != club.is_joining(person):
                    club.set_joining(person, joining)
                    store_club()
                    st.rerun()
        participants = (
            club.participants(library.people) if library is not None else []
        )

        st.divider()
        st.header("Filters")
        read_filter = st.radio(
            "Books someone has already read",
            [READ_FILTER_INCLUDE, READ_FILTER_PARTICIPANTS, READ_FILTER_ANYONE],
            index=1,
            key="read_filter",
            format_func=lambda value: READ_FILTER_LABELS[value],
            help=(
                "A book a member loved is a strong tip, but a poor pick for a "
                "shared read. Your call."
            ),
        )
        include_discoveries = st.checkbox(
            "Include new discoveries from Open Library", value=True
        )
        library_limit = st.slider("Suggestions from the club's shelves", 5, 60, 24)
        discovery_limit = st.slider("New discoveries", 0, 40, 12)
        min_year = st.number_input(
            "Only discover books published from",
            min_value=1800,
            max_value=2026,
            value=1990,
            step=5,
            help="Open Library favours old classics without a floor here.",
        )
        st.divider()
        st.caption(
            "This club's exports, votes and preferences stay in "
            "data/clubs/<name>/. Genre lookups are shared in data/cache/."
        )

    settings = {
        "include_discoveries": include_discoveries,
        "library_limit": library_limit,
        "discovery_limit": discovery_limit,
        "min_year": int(min_year),
        "participants": participants,
        "read_filter": read_filter,
    }

    if library is None or not library.people:
        st.info(
            "Start by uploading each member's Goodreads or StoryGraph CSV export "
            "on the Exports tab."
        )

    exports_tab, preferences_tab, overlap_tab, suggestions_tab, next_tab = st.tabs(
        ["Exports", "Preferences", "Overlap and stats", "Suggestions", "Next read"]
    )
    with exports_tab:
        render_upload(library)
    with preferences_tab:
        render_preferences(library)
    with overlap_tab:
        if library.people:
            render_overlap(library)
        else:
            st.info("Upload some exports first.")
    with suggestions_tab:
        render_suggestions(library, settings)
    with next_tab:
        if library.people:
            render_next_read(library, settings)
        else:
            st.info("Upload some exports first.")


if __name__ == "__main__":
    main()
