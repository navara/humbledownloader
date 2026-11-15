import pytest

from humbledownloader.flight import LayoutError, find_value, flight_text
from tests.flightdata import page


def test_chunks_are_joined_across_boundaries():
    html = page({"bundleItems": [{"title": 'a "quoted" } title'}] * 20}, chunk_size=7)
    text = flight_text(html)
    items = find_value(text, "bundleItems")
    assert len(items) == 20
    assert items[0]["title"] == 'a "quoted" } title'


def test_page_without_flight_data():
    with pytest.raises(LayoutError):
        flight_text("<html>login</html>")


def test_missing_key_is_none():
    assert find_value(flight_text(page({"a": 1})), "bundleItems") is None


def test_non_container_value_is_layout_error():
    with pytest.raises(LayoutError):
        find_value(flight_text(page({"bundleItems": "$undefined"})), "bundleItems")


def test_cut_off_value_is_layout_error():
    with pytest.raises(LayoutError):
        find_value('"bundleItems":[{"a":1}', "bundleItems")


def test_escaped_quotes_and_backslashes_inside_strings():
    text = flight_text(page({"x": {"t": 'back\\slash \\" ]}'}}))
    assert find_value(text, "x") == {"t": 'back\\slash \\" ]}'}
