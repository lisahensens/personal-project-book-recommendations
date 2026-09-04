from bookclub.ingest import (
    GOODREADS,
    READ,
    READING,
    STORYGRAPH,
    TO_READ,
    UnknownExportFormat,
    detect_format,
    load_export,
    person_name_from_filename,
)
import pytest

GOODREADS_CSV = (
    "Book Id,Title,Author,Author l-f,Additional Authors,ISBN,ISBN13,My Rating,"
    "Publisher,Binding,Number of Pages,Year Published,Original Publication Year,"
    "Date Read,Date Added,Bookshelves,Bookshelves with positions,Exclusive Shelf,"
    "My Review,Spoiler,Private Notes,Read Count,Owned Copies\n"
    '1,"Threshing Day (The Empyrean, #3.5)",Rebecca Yarros,"Yarros, Rebecca",,'
    '"=""1682818527""","=""9781682818527""",0,Red Tower,Kindle,,2026,2026,,'
    "2026/08/25,to-read,to-read (#3076),to-read,,,,0,0\n"
    '2,Secrets of Blackthorn Hall,Cassandra Clare,"Clare, Cassandra",,'
    '"=""""","=""""",5.0,,Hardcover,269,2024,2021,2026/08/28,2026/04/02,'
    "on-my-bookshelf,on-my-bookshelf (#245),read,,,,1,0\n"
    '3,Currently Going,Some Author,"Author, Some",,"=""""","=""""",0,,Paperback,'
    "100,2020,2020,,2026/01/01,,,currently-reading,,,,0,0\n"
)

STORYGRAPH_CSV = (
    "Title,Authors,Contributors,ISBN/UID,Format,Read Status,Date Added,"
    "Last Date Read,Star Rating,Review,Tags,Genres,Owned?\n"
    "Piranesi,Susanna Clarke,,9781526622426,digital,read,2026/02/01,2026/02/20,"
    "4.5,,,\"Fantasy, Mystery\",Yes\n"
    "The Bee Sting,Paul Murray,,9780374600305,paperback,to-read,2026/03/01,,"
    "0,,\"literary, irish\",,No\n"
)


def test_detect_format_recognises_both_platforms():
    assert detect_format(["Title", "Author", "Exclusive Shelf"]) == GOODREADS
    assert detect_format(["Title", "Authors", "Read Status"]) == STORYGRAPH


def test_detect_format_rejects_unrelated_csv():
    with pytest.raises(UnknownExportFormat):
        detect_format(["Name", "Email"])


def test_goodreads_export_is_normalised():
    records = load_export(GOODREADS_CSV, "Lisa")

    assert [record.shelf for record in records] == [TO_READ, READ, READING]
    assert all(record.person == "Lisa" for record in records)
    assert all(record.source == GOODREADS for record in records)

    first = records[0]
    # Goodreads wraps identifiers as Excel formulas, e.g. ="9781682818527".
    assert first.isbn13 == "9781682818527"
    assert first.isbn == "1682818527"
    assert first.rating is None  # a Goodreads rating of 0 means "not rated"
    assert first.source_genres == []

    second = records[1]
    assert second.rating == 5.0
    assert second.liked is True
    assert second.isbn == ""  # empty Excel formula becomes an empty string
    # "on-my-bookshelf" is a custom shelf, but the exclusive shelf says "read".
    assert second.shelf == READ


def test_storygraph_export_is_normalised():
    records = load_export(STORYGRAPH_CSV, "Laurens")

    piranesi, bee_sting = records
    assert piranesi.shelf == READ
    assert piranesi.rating == 4.5
    assert piranesi.liked is True
    assert piranesi.isbn13 == "9781526622426"
    assert piranesi.source_genres == ["Fantasy", "Mystery"]
    assert piranesi.source == STORYGRAPH

    assert bee_sting.shelf == TO_READ
    assert bee_sting.rating is None
    # Older exports have no Genres column, so member tags stand in for genres.
    assert bee_sting.source_genres == ["literary", "irish"]


def test_rows_without_a_title_are_skipped():
    csv = GOODREADS_CSV + '4,,No Title Author,,,"=""""","=""""",0,,,,,,,,,,to-read,,,,0,0\n'
    assert len(load_export(csv, "Lisa")) == 3


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("goodreads_library_export_lisa.csv", "Lisa"),
        ("storygraph-export-laurens-2026-01-01.csv", "Laurens"),
        ("anna.csv", "Anna"),
    ],
)
def test_person_name_from_filename(filename, expected):
    assert person_name_from_filename(filename) == expected
