import json

from PIL import Image

from src import constants
from src.ui import card_art
from src.ui.card_art import CardArt, art_url, shape_image


def test_art_url_uses_scryfall_art_crop():
    card = {"image": ["https://cards.scryfall.io/large/front/b/a/ba.jpg?1"]}
    assert art_url(card) == "https://cards.scryfall.io/art_crop/front/b/a/ba.jpg?1"
    assert art_url({"image": []}) is None
    assert art_url({}) is None
    assert art_url({"image": ["https://example.com/x.jpg"]}) is None


def test_shape_image_crops_to_size_and_masks_circle():
    source = Image.new("RGB", (626, 457), (200, 50, 50))
    rect = shape_image(source, 46, 24)
    assert rect.size == (46, 24) and rect.mode == "RGB"
    circle = shape_image(source, 20, 20, shape="circle")
    assert circle.mode == "RGBA"
    assert circle.getpixel((0, 0))[3] == 0
    assert circle.getpixel((10, 10))[3] == 255


def test_index_set_finds_art_on_other_printings(tmp_path, monkeypatch):
    custom = {"card_ratings": {
        "1": {"name": "Bolt", "image": []},
        "2": {"name": "Bolt", "image": ["https://cards.scryfall.io/large/front/a/b/bolt.jpg"]},
    }}
    full = {"card_ratings": {"3": {"name": "Shock", "image": ["https://cards.scryfall.io/large/front/s/h/shock.jpg"]}}}
    (tmp_path / "FRA_PremierDraft_All_Custom-AllTime-1_Data.json").write_text(json.dumps(custom), encoding="utf-8")
    (tmp_path / "Y26FRA_PremierDraft_All_Data.json").write_text(json.dumps(full), encoding="utf-8")
    (tmp_path / "WOE_PremierDraft_All_Data.json").write_text(json.dumps({"card_ratings": {}}), encoding="utf-8")
    monkeypatch.setattr(constants, "SETS_FOLDER", str(tmp_path))
    index = CardArt._index_set("FRA")
    assert index["Bolt"].endswith("/art_crop/front/a/b/bolt.jpg")
    assert "Shock" in index


def test_disabled_art_returns_nothing(monkeypatch):
    monkeypatch.setattr(CardArt, "enabled", False)
    card = {"image": ["https://cards.scryfall.io/large/front/b/a/ba.jpg"]}
    assert CardArt.pil(None, card, 10, 10) is None
    assert CardArt.get(None, card, 10, 10) is None
