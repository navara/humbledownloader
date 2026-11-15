"""Item titles must map to the folder names hbd created. Titles and folder names only.

tests/data/folder_names.json holds made-up titles covering every naming rule seen in
real titles.
"""

import json
import os

import pytest

from humbledownloader.catalog import parse_item
from humbledownloader.flight import flight_text
from humbledownloader.paths import clean_name
from tests.flightdata import download, entitlement, item_props, page, struct

HERE = os.path.dirname(__file__)


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_title(pair):
    ents = [entitlement("e", "Product", [download("k", "ebook", [struct("f.pdf")])])]
    text = flight_text(page(item_props("p", pair["title"], ents, type_=pair["type"])))
    assert clean_name(parse_item(text, "p").files[0].product_title) == pair["folder"]


MOCK = load(os.path.join(HERE, "data", "folder_names.json"))


def test_mock_table_covers_every_title_type():
    assert len(MOCK) == 16
    assert {p["type"] for p in MOCK} == {
        "bundle",
        "storefront",
        "subscriptioncontent",
        "widget",
    }


@pytest.mark.parametrize("pair", MOCK, ids=lambda p: p["folder"][:40])
def test_title_maps_to_existing_folder(pair):
    check_title(pair)
