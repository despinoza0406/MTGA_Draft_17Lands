import os
import time
from types import SimpleNamespace

from src.ui.app import DraftApp


def app(log_path, game="g1", active=True, enabled=True):
    opened = []
    fake = SimpleNamespace(
        GAME_LOG_FRESH_SECONDS=DraftApp.GAME_LOG_FRESH_SECONDS,
        GAME_WATCH_MS=DraftApp.GAME_WATCH_MS,
        game_tracker=SimpleNamespace(snapshot=lambda: {"active": active, "game": game}),
        _auto_tracker_game=None,
        configuration=SimpleNamespace(settings=SimpleNamespace(
            arena_log_location=str(log_path), auto_open_tracker=enabled)),
        _open_match_tracker=lambda: opened.append(1),
        root=SimpleNamespace(after=lambda ms, fn: None),
        _watch_game=lambda: None,  # the next poll, scheduled with root.after
    )
    return fake, opened


def test_opens_once_when_a_game_starts(tmp_path):
    log = tmp_path / "Player.log"
    log.write_text("x")
    fake, opened = app(log)
    DraftApp._watch_game(fake)
    DraftApp._watch_game(fake)
    assert opened == [1]
    fake.game_tracker.snapshot = lambda: {"active": True, "game": "g2"}
    DraftApp._watch_game(fake)
    assert opened == [1, 1]


def test_no_open_for_old_logs_inactive_games_or_when_off(tmp_path):
    log = tmp_path / "Player.log"
    log.write_text("x")
    old = time.time() - 3600
    os.utime(log, (old, old))
    stale, opened = app(log)
    DraftApp._watch_game(stale)
    assert opened == []

    fresh = tmp_path / "fresh.log"
    fresh.write_text("x")
    for fake, opened in (app(fresh, active=False), app(fresh, enabled=False)):
        DraftApp._watch_game(fake)
        assert opened == []
