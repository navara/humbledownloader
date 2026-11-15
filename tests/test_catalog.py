import pytest

from humbledownloader.catalog import parse_item, parse_library, purchased_product_ids
from humbledownloader.flight import LayoutError, flight_text
from tests.flightdata import download, entitlement, item_props, page, struct


def text_of(*props):
    return flight_text(page(*props))


def test_parse_library():
    t = text_of({"bundleItems": [{"productEntitlementId": "p1", "title": "B1"}]})
    assert parse_library(t) == [("p1", "B1")]


def test_library_without_bundle_items_is_layout_error():
    with pytest.raises(LayoutError):
        parse_library(text_of({"user": {}}))


def test_purchased_ids_keep_only_drm_free_and_ignore_key_data():
    rows = [
        {
            "type": "steam",
            "productEntitlementId": "p0",
            "entitlements": {"keys": ["X"]},
        },
        {"type": "download_drm_free", "productEntitlementId": "p1"},
        {"type": "download_drm_free", "productEntitlementId": "p1"},
        {"type": "download_drm_free", "productEntitlementId": "p2"},
    ]
    assert purchased_product_ids(rows) == ["p1", "p2"]


def test_parse_item_files_and_skips():
    ents = [
        entitlement(
            "e1",
            "Book: One",
            [download("book_ebook", "ebook", [struct("pub/book.pdf", 10, "AB" * 16)])],
        ),
        entitlement("e2", "No Content", "$undefined"),
        entitlement(
            "e3", "Game", [download("game_windows", "windows", [{"name": "Stream"}])]
        ),
    ]
    item = parse_item(text_of(item_props("p1", "Bundle", ents)), "p1")
    assert item.title == "Bundle"
    assert len(item.files) == 1
    f = item.files[0]
    assert (f.entitlement_id, f.download_key, f.platform) == (
        "e1",
        "book_ebook",
        "ebook",
    )
    assert f.file_name == "book.pdf"
    assert f.key == "e1/book_ebook/pub/book.pdf"
    assert f.md5 == "ab" * 16
    assert f.size == 10
    assert item.skipped == ["Game: Stream has no file"]


def test_parse_item_missing_required_field_names_it():
    ents = [
        entitlement(
            "e1", "T", [{"platform": "ebook", "downloadStruct": [struct("a.pdf")]}]
        )
    ]
    with pytest.raises(LayoutError, match="'key'"):
        parse_item(text_of(item_props("p1", "B", ents)), "p1")


def test_parse_item_without_user_library_item():
    with pytest.raises(LayoutError):
        parse_item(text_of({"something": {}}), "p1")


def test_storefront_suffix_is_dropped_for_the_folder_only():
    ents = [
        entitlement(
            "e1", "Quiet Lighthouse", [download("k", "windows", [struct("p.zip")])]
        )
    ]
    item = parse_item(
        text_of(item_props("p1", "Quiet Lighthouse (steam)", ents, type_="storefront")),
        "p1",
    )
    assert item.title == "Quiet Lighthouse (steam)"
    assert item.files[0].product_title == "Quiet Lighthouse"
    bundle = parse_item(text_of(item_props("p1", "Odd (steam)", ents)), "p1")
    assert bundle.files[0].product_title == "Odd (steam)"


@pytest.mark.parametrize(
    "st, reason",
    [
        (struct("<FILE_NAME>"), "placeholder"),
        (dict(struct("a.pdf"), fileSize="1000"), "size"),
        (dict(struct("a.pdf"), fileSize=True), "size"),
        (dict(struct("a.pdf"), md5="md5 hash"), "md5"),
        (dict(struct("a.pdf"), url={"web": "$undefined"}), "no file"),
    ],
)
def test_unusable_file_rows_are_skipped_with_a_reason(st, reason):
    ents = [entitlement("e1", "Book", [download("k", "ebook", [st])])]
    item = parse_item(text_of(item_props("p1", "B", ents)), "p1")
    assert item.files == [] and reason in item.skipped[0]


@pytest.mark.parametrize("value", ["$undefined", None, []])
def test_key_only_items_have_no_files(value):
    props = item_props("p1", "Keys only", [])
    if value is None:
        del props["userLibraryItem"]["entitlements"]["downloads"]
    else:
        props["userLibraryItem"]["entitlements"]["downloads"] = value
    assert parse_item(text_of(props), "p1").files == []


def test_unexpected_downloads_shape_is_layout_error():
    props = item_props("p1", "B", [])
    props["userLibraryItem"]["entitlements"]["downloads"] = {"odd": 1}
    with pytest.raises(LayoutError):
        parse_item(text_of(props), "p1")


@pytest.mark.parametrize("field", ["title", "id", "platform"])
def test_non_text_title_id_or_platform_is_layout_error(field):
    dl = download("k", "ebook", [struct("a.pdf")])
    ent = entitlement("e1", "Book", [dl])
    if field == "platform":
        dl["platform"] = ["ebook"]
    else:
        ent[field] = 5
    with pytest.raises(LayoutError, match="is not text"):
        parse_item(text_of(item_props("p1", "B", [ent])), "p1")


def test_non_text_item_title_is_layout_error():
    with pytest.raises(LayoutError, match="is not text"):
        parse_item(text_of(item_props("p1", 5, [])), "p1")


@pytest.mark.parametrize("value", ["2020-01-01T00:00:00", "someday"])
def test_date_without_time_zone_or_unreadable_is_layout_error(value):
    ent = entitlement("e1", "Book", [download("k", "ebook", [struct("a.pdf")])])
    ent["unavailableAfter"] = value
    with pytest.raises(LayoutError):
        parse_item(text_of(item_props("p1", "B", [ent])), "p1")


@pytest.mark.parametrize(
    "extra, reason",
    [
        ({"type": "steam"}, "not a DRM-free"),
        ({"expired": True}, "expired"),
        ({"unavailableAfter": "2020-01-01T00:00:00.000Z"}, "not available after"),
        ({"unavailableBefore": "2999-01-01T00:00:00.000Z"}, "not available before"),
    ],
)
def test_entitlements_the_site_would_not_offer_are_not_signed(extra, reason):
    ent = entitlement("e1", "Book", [download("k", "ebook", [struct("a.pdf")])])
    ent.update(extra)
    item = parse_item(text_of(item_props("p1", "B", [ent])), "p1")
    assert item.files == [] and reason in item.skipped[0]
