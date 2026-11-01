from types import SimpleNamespace

from src.ui.app_controller import AppController


def controller(arena_file, live="Player.log"):
    switched = []
    ctl = SimpleNamespace(
        orchestrator=SimpleNamespace(live_log_path=live, scanner=SimpleNamespace(arena_file=arena_file)),
        app=SimpleNamespace(layout_manager=SimpleNamespace(show_draft_page=lambda: switched.append(1))),
    )
    return ctl, switched


def test_switches_once_per_live_draft():
    ctl, switched = controller("Player.log")
    AppController._follow_live_pick(ctl, "d1", 1, 1, ["card"])
    AppController._follow_live_pick(ctl, "d1", 1, 2, ["card"])
    assert switched == [1]
    AppController._follow_live_pick(ctl, "d2", 1, 1, ["card"])
    assert switched == [1, 1]


def test_no_switch_without_a_pack_or_for_old_logs():
    ctl, switched = controller("Player.log")
    AppController._follow_live_pick(ctl, "d1", 0, 0, [])
    old, old_switched = controller("DraftLog_old.log")
    AppController._follow_live_pick(old, "d1", 1, 1, ["card"])
    assert switched == [] and old_switched == []
