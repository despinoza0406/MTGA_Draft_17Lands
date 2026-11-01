import json

from src.deck_record import (
    classify_deck,
    latest_submitted_deck,
    load_snapshots,
    parse_submitted_decks,
    record_by_source,
    save_suggestions,
    source_text,
)


def submit_line(event, main, side=(), updated="2026-10-31T21:40:37"):
    request = {
        "EventName": event,
        "Summary": {"Attributes": [{"name": "LastUpdated", "value": f'"{updated}"'}]},
        "Deck": {"MainDeck": [{"cardId": c, "quantity": q} for c, q in main],
                 "Sideboard": [{"cardId": c, "quantity": q} for c, q in side]},
    }
    return "[UnityCrossThreadLogger]==> EventSetDeckV3 " + json.dumps({"id": "x", "request": json.dumps(request)})


def test_parse_submitted_decks_reads_main_and_sideboard(tmp_path):
    log = "\n".join([
        "noise",
        submit_line("PickTwoDraft_FRA", [(1, 2), (2, 1)], [(3, 1)]),
        "<== EventSetDeckV3(x)",
        submit_line("PickTwoDraft_FRA", [(1, 1), (4, 16)]),
    ])
    decks = parse_submitted_decks(log)
    assert len(decks) == 2
    assert decks[0]["main"] == ["1", "1", "2"] and decks[0]["sideboard"] == ["3"]
    assert decks[0]["updated"] == "2026-10-31T21:40:37" and decks[0]["event"] == "PickTwoDraft_FRA"
    old, new = tmp_path / "Player-prev.log", tmp_path / "Player.log"
    old.write_text(submit_line("E", [(9, 1)]), encoding="utf-8")
    new.write_text("nothing here", encoding="utf-8")
    assert latest_submitted_deck([str(old), str(new)])["main"] == ["9"]


def cards(*names):
    return [{"name": n} for n in names]


def test_suggestions_merge_per_pool_and_classify(tmp_path):
    path = str(tmp_path / "s.json")
    pool = cards(*[f"C{i}" for i in range(30)], "Forest")
    model = cards(*[f"C{i}" for i in range(23)], "Forest")
    other = cards(*[f"C{i}" for i in range(7, 30)])
    save_suggestions(pool, {"🤖 Model Build": model}, "E", path)
    save_suggestions(pool, {"RG Consistent": other}, "E", path)
    snaps = load_snapshots(path)
    assert len(snaps) == 1 and set(snaps[0]["decks"]) == {"🤖 Model Build", "RG Consistent"}
    assert "Forest" not in snaps[0]["pool"]

    played_model = [f"C{i}" for i in range(22)] + ["C25", "Forest"]
    info = classify_deck(played_model, snaps)
    assert info == {"source": "model", "label": "🤖 Model Build", "changes": 1}
    assert source_text(info) == "🤖 +1"
    played_other = [f"C{i}" for i in range(7, 30)]
    assert classify_deck(played_other, snaps)["source"] == "suggested"
    played_own = [f"C{i}" for i in [*range(6), *range(10, 20), *range(23, 30)]]
    assert classify_deck(played_own, snaps)["source"] == "own"
    assert classify_deck(["Not In Pool"] * 23, snaps) is None


def test_record_by_source():
    model = {"source": "model", "label": "🤖", "changes": 0}
    own = {"source": "own", "label": "x", "changes": 6}
    line = record_by_source([(True, model), (True, model), (False, own), (True, None)])
    assert line == "Deck you played (games): 🤖 Model build 2-0 · Your own build 0-1."
    assert record_by_source([(True, None)]) is None
