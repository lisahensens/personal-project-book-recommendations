# Book Club

A local app for picking the next book together. Upload each member's Goodreads or
StoryGraph library, see what you already share, and get suggestions for what to
read next.

You can run more than one club on the same machine. Each club keeps its own
members, votes and preferences. Genre lookups are shared, so a book fetched once
does not need fetching again for another club.

## Run it

You need Python 3.11 or newer.

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open the URL Streamlit prints (usually <http://localhost:8501>).

## Get everyone's export

**Goodreads:** My Books → Import and export → Export Library.

**StoryGraph:** Manage Account → Manage Your Data → Export StoryGraph Library.

Put the member's name in the filename. `goodreads_library_export_lisa.csv`
becomes Lisa.

## Use it

1. **Create or pick a club** in the sidebar. Friends starting from scratch type a
   name and click *Create club*.
2. **Exports** — upload one CSV per member. You only do this once per club.
3. **Fetch genres** on the same tab. Goodreads exports have no genres, so the app
   looks them up on Open Library. A large shelf takes a while; you can stop and
   continue later. Suggestions still work before this finishes.
4. **Preferences** — each person lists genres they like, would rather skip, and
   never want (absolute no). Small libraries still count equally here.
5. **Overlap and stats** — shared to-reads, shared authors and genres, and a few
   club awards.
6. **Suggestions** — ranked picks from books someone already has, plus optional
   new discoveries. Each card says why it was suggested. Add the ones you like to
   the shortlist.
7. **Next read** — vote yes / maybe / no. A no outweighs a yes, so nobody is
   stuck with a book they refuse. Or spin the roulette for a random shared
   to-read.

## Sidebar

- **Who is joining?** Uncheck anyone sitting this round out. Their taste and
  vetoes stop counting; their books stay in the pool.
- **Already read.** Defaults to hiding books anyone joining has already finished.
  You can include them, or hide anything any member has read.

## What the suggestions do (and skip)

Picks come from two places: books on someone's shelf that others are missing, and
new titles in genres the club likes. A 20-book shelf pulls as hard as a 3,000-book
one.

The list stays startable and in English: standalones and first volumes, later
volumes only for people who already have the earlier ones, and no editions
without an English title. Absolute-no genres and the already-read setting always
win over a high score.

## Notes

- Genre lookup is about 1–2 seconds per book and is cached. Copy `data/cache/`
  with the project if you move laptops, so you do not look the same books up
  twice.
- Some books have no genre data on Open Library. Stated preferences cover that
  gap.
- The same book is matched across members even when titles differ (series labels,
  translated editions like *Zeis* / *Scythe*). A translated standalone with no
  series label may still show up twice.
- Your exports, votes and preferences stay on your machine under
  `data/clubs/<club>/` and are not committed to git.
