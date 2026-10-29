"""
src/game_tracker.py
In-game tracker built from Arena's detailed GRE logs: the cards left in your
library (and the odds of drawing each), and the cards your opponent has shown.

GameTracker is a pure parser fed line by line; LogTailer follows Player.log in
a background thread and feeds it.
"""

import os
import json
import time
import logging
import threading
from collections import Counter
from typing import Dict, Optional

logger = logging.getLogger(__name__)

GRE_MARKER = "greToClientEvent"
MY_OUT_OF_LIBRARY = {
    "ZoneType_Hand",
    "ZoneType_Battlefield",
    "ZoneType_Graveyard",
    "ZoneType_Exile",
    "ZoneType_Stack",
    "ZoneType_Revealed",
    "ZoneType_Command",
}
PUBLIC_ZONES = MY_OUT_OF_LIBRARY - {"ZoneType_Hand"}


class GameTracker:
    def __init__(self):
        self.seat: Optional[int] = None
        self.deck: Counter = Counter()
        self.game_key = None
        self.lock = threading.Lock()
        self._reset_game()

    def _reset_game(self):
        self.zones: Dict[int, Dict] = {}
        self.objects: Dict[int, Dict] = {}
        self.alias: Dict[int, int] = {}
        self.opponent_seen: Dict[int, int] = {}
        self.game_over = False

    def _canonical(self, instance_id: int) -> int:
        seen = set()
        while instance_id in self.alias and instance_id not in seen:
            seen.add(instance_id)
            instance_id = self.alias[instance_id]
        return instance_id

    def feed_line(self, line: str) -> bool:
        """Returns True when the line changed the tracked state."""
        if GRE_MARKER not in line:
            return False
        start = line.find("{")
        try:
            data = json.JSONDecoder().raw_decode(line, start)[0]
        except ValueError:
            return False
        messages = data.get(GRE_MARKER, {}).get("greToClientMessages", [])
        with self.lock:
            for message in messages:
                self._handle(message)
        return bool(messages)

    def _handle(self, message: Dict):
        seats = message.get("systemSeatIds") or []
        if seats:
            self.seat = seats[0]

        if message.get("type") == "GREMessageType_ConnectResp":
            deck = (message.get("connectResp") or {}).get("deckMessage") or {}
            if deck.get("deckCards"):
                self.deck = Counter(deck["deckCards"])
            return

        state = message.get("gameStateMessage")
        if not state:
            return

        info = state.get("gameInfo") or {}
        if info.get("matchID"):
            key = (info["matchID"], info.get("gameNumber", 1))
            if key != self.game_key:
                self.game_key = key
                self._reset_game()
            if info.get("stage") == "GameStage_GameOver":
                self.game_over = True
        if state.get("type") == "GameStateType_Full":
            self.zones, self.objects = {}, {}

        for zone in state.get("zones", []):
            self.zones[zone["zoneId"]] = zone
        for obj in state.get("gameObjects", []):
            self.objects[obj["instanceId"]] = obj
        for deleted in state.get("diffDeletedInstanceIds", []):
            self.objects.pop(deleted, None)

        for annotation in state.get("annotations", []):
            if "AnnotationType_ObjectIdChanged" not in annotation.get("type", []):
                continue
            details = {d["key"]: d.get("valueInt32", [None])[0] for d in annotation.get("details", [])}
            if details.get("orig_id") is not None and details.get("new_id") is not None:
                self.alias[details["new_id"]] = details["orig_id"]

        for obj in self.objects.values():
            zone = self.zones.get(obj.get("zoneId"), {})
            if (
                obj.get("type") == "GameObjectType_Card"
                and self.seat is not None
                and obj.get("ownerSeatId") != self.seat
                and zone.get("type") in PUBLIC_ZONES
            ):
                self.opponent_seen[self._canonical(obj["instanceId"])] = obj["grpId"]

    def snapshot(self) -> Dict:
        """Library size, remaining cards with draw odds (grpId keyed), opponent cards."""
        with self.lock:
            library_size, out = 0, Counter()
            for zone in self.zones.values():
                ids = zone.get("objectInstanceIds", [])
                if zone.get("type") == "ZoneType_Library" and zone.get("ownerSeatId") == self.seat:
                    library_size = len(ids)
                elif zone.get("type") in MY_OUT_OF_LIBRARY:
                    for instance_id in ids:
                        obj = self.objects.get(instance_id, {})
                        if obj.get("type") == "GameObjectType_Card" and obj.get("ownerSeatId") == self.seat:
                            out[obj["grpId"]] += 1
            remaining = self.deck - out
            return {
                "active": self.game_key is not None and not self.game_over and bool(self.deck),
                "game": self.game_key,
                "library_size": library_size,
                "remaining": dict(remaining),
                "draw_odds": {
                    g: n / library_size for g, n in remaining.items() if library_size
                },
                "opponent": dict(Counter(self.opponent_seen.values())),
            }


class LogTailer(threading.Thread):
    """Follows Player.log (reading it whole first, to pick up a game in progress)."""

    def __init__(self, path: str, tracker: GameTracker, interval: float = 0.5):
        super().__init__(daemon=True)
        self.path, self.tracker, self.interval = path, tracker, interval
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        position = 0
        while not self._stop_event.is_set():
            try:
                size = os.path.getsize(self.path)
                if size < position:  # Arena restarted and truncated the log
                    position = 0
                if size > position:
                    with open(self.path, encoding="utf-8", errors="ignore") as f:
                        f.seek(position)
                        for line in f:
                            if not line.endswith("\n"):
                                break  # partial line; read it next time
                            self.tracker.feed_line(line)
                            position += len(line.encode("utf-8"))
            except OSError:
                pass
            except Exception as e:
                logger.warning(f"Game tracker error: {e}")
            time.sleep(self.interval)
