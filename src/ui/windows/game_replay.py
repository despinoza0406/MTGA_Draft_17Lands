"""
src/ui/windows/game_replay.py
Turn-by-turn replay of a recorded game, read like a chess game review: the
battlefield drawn with combat arrows, a move list per round with ?! on flagged
turns, a life-difference graph to jump around, and a "learn from your mistakes"
mode that stops on each flagged turn before showing what you did.
"""

import tkinter
from tkinter import ttk
from typing import Callable, Dict, List

from src import constants
from src.ui.styles import Theme
from src.ui.components import art_color, blend
from src.ui.card_art import CardArt
from src.game_review import BASIC_LAND_COLORS, GameLog, Finding, can_pay

LEVEL_ICONS = {"review": "⚠️", "ok": "✓", "info": "ℹ️"}
EVENT_TEXT = {
    "cast": "{who} cast {card}",
    "land": "{who} played {card}",
    "draw": "You drew {card}",
    "died": "{card} ({whose}) died",
    "sacrificed": "{card} ({whose}) was sacrificed",
    "exiled": "{card} ({whose}) was exiled",
    "returned to hand": "{card} ({whose}) returned to hand",
    "countered": "{card} ({whose}) was countered",
    "discarded": "{who} discarded {card}",
    "milled": "{card} ({whose}) was milled",
}


def replay_steps(game: GameLog) -> List[Dict]:
    """Two steps per turn: what you had at the start, and how the turn ended."""
    steps = []
    for entry in game.timeline:
        if entry.get("start"):
            steps.append({"entry": entry, "phase": "start", "snapshot": entry["start"]})
        if entry.get("end"):
            steps.append({"entry": entry, "phase": "end", "snapshot": entry["end"]})
    return steps


def event_lines(entry: Dict, name: Callable[[int], str]) -> List[str]:
    lines = []
    for event in entry.get("events", []):
        kind = event[0]
        if kind == "damage":
            _, who, source, target, amount = event
            target_text = {"you": "you", "opp": "the opponent"}.get(target) or name(target)
            lines.append(f"{name(source)} dealt {amount} to {target_text}")
        elif kind in EVENT_TEXT:
            _, who, grp = event
            lines.append(
                EVENT_TEXT[kind].format(
                    who="You" if who == "you" else "Opponent",
                    whose="yours" if who == "you" else "theirs",
                    card=name(grp),
                )
            )
    combat = entry.get("combat")
    if combat:
        mine = [name(g) for who, g in combat["attackers"] if who == "you"]
        theirs = [name(g) for who, g in combat["attackers"] if who == "opp"]
        attack = f"You attacked with {', '.join(mine)}" if mine else f"Opponent attacked with {', '.join(theirs)}"
        blocks = "; ".join(f"{name(b)} blocked {', '.join(name(a) for a in att) or '?'}" for b, att in combat["blocks"])
        lines.append(f"⚔ {attack}. " + (f"Blocks: {blocks}." if blocks else "No blocks."))
    return lines


def land_colors(grp_ids: List[int], card: Callable[[int], Dict]) -> List[str]:
    out = []
    for g in grp_ids:
        c = card(g)
        basic = BASIC_LAND_COLORS.get(str(c.get(constants.DATA_FIELD_NAME, "")).removeprefix("Snow-Covered "))
        out.append(basic or "".join(c.get("colors") or c.get("color_identity") or []))
    return out


def castable_marks(snapshot: Dict, card: Callable[[int], Dict]) -> Dict[int, str]:
    """For each nonland card in hand: "now" if your untapped lands pay for it,
    "with land drop" if one more land from your hand would, else ""."""
    untapped = [land[0] for land in snapshot["my_lands"] if not land[3]]
    sources = land_colors(untapped, card)
    hand_lands = [g for g in snapshot["hand"] if "Land" in card(g).get("types", [])]
    marks = {}
    for g in snapshot["hand"]:
        c = card(g)
        if "Land" in c.get("types", []):
            continue
        cmc = float(c.get("cmc", 0) or 0)
        cost = c.get("mana_cost", "")
        if can_pay(cost, cmc, sources):
            marks[g] = "now"
        elif hand_lands and any(can_pay(cost, cmc, sources + land_colors([land], card)) for land in hand_lands):
            marks[g] = "with land drop"
        else:
            marks[g] = ""
    return marks


def turn_summary(entry: Dict, name: Callable[[int], str]) -> str:
    """One short move-list line for a turn: lands, spells and damage to the other player."""
    active = entry.get("active")
    parts, hits = [], 0
    for event in entry.get("events", []):
        kind = event[0]
        if kind in ("land", "cast") and event[1] == active:
            parts.append(name(event[2]))
        elif kind == "damage" and event[1] == active and event[3] in ("you", "opp") and event[3] != active:
            hits += event[4]
    if hits:
        parts.append(f"hits for {hits}")
    return " · ".join(parts) or "—"


def move_rows(game: GameLog, name: Callable[[int], str], flagged_turns) -> List[Dict]:
    """Rounds of (your turn, opponent's turn), the way a chess move list reads."""
    rows = {}
    for entry in game.timeline:
        rnd = (entry["turn"] + 1) // 2
        row = rows.setdefault(rnd, {"round": rnd, "you": "", "opp": "", "you_turn": None, "opp_turn": None})
        side = "you" if entry["active"] == "you" else "opp"
        mark = "?! " if entry["turn"] in flagged_turns else ""
        row[side] = mark + turn_summary(entry, name)
        row[f"{side}_turn"] = entry["turn"]
    return [rows[k] for k in sorted(rows)]


class GameReplayWindow(tkinter.Toplevel):
    CARD_W, CARD_H = 92, 122
    HAND_W, HAND_H = 74, 86

    def __init__(self, parent, game: GameLog, findings: List[Finding], lookup, resolve_name=None):
        super().__init__(parent)
        self.game = game
        self.findings = findings
        self.lookup = lookup
        # Tokens and other sets aren't in the 17Lands dataset; the Arena card DB knows them.
        self.resolve_name = resolve_name
        self._cards = {}
        self.steps = replay_steps(game)
        self.index = 0
        self.learning = False
        self.flagged_turns = {f.turn for f in findings if f.level == "review" and f.turn}

        result = {True: "Won", False: "Lost", None: "?"}[game.won]
        self.subtitle = f"{game.event or 'Game'} · {result.lower()} on the {'play' if game.on_play else 'draw'}"
        self.title(f"Replay — {game.event or 'game'} — {result} on the {'play' if game.on_play else 'draw'}")
        self.geometry(f"{Theme.scaled_val(1360)}x{Theme.scaled_val(860)}")
        self.configure(bg=Theme.BG_PRIMARY)
        self._build_ui()
        self.bind("<Left>", lambda e: self.move(-1))
        self.bind("<Right>", lambda e: self.move(1))
        self.focus_set()
        self.render()

    # --- data helpers ---
    def card(self, grp_id) -> Dict:
        if grp_id not in self._cards:
            found = self.lookup([str(grp_id)])
            self._cards[grp_id] = found[0] if found else {}
        return self._cards[grp_id]

    def name(self, grp_id) -> str:
        name = self.card(grp_id).get(constants.DATA_FIELD_NAME)
        if (not name or str(name).isdigit()) and self.resolve_name:
            try:
                name = self.resolve_name(str(grp_id))
            except Exception:
                name = None
            if name and not str(name).isdigit():
                self._cards[grp_id] = {**self.card(grp_id), constants.DATA_FIELD_NAME: name}
        return name if name and not str(name).isdigit() else f"#{grp_id}"

    # --- layout ---
    def _build_ui(self):
        a = Theme.ARENA
        pad = Theme.scaled_val(10)
        header = ttk.Frame(self, padding=(pad, pad, pad, 6))
        header.pack(fill="x")
        titles = ttk.Frame(header)
        titles.pack(side="left", fill="x", expand=True)
        ttk.Label(titles, text=self.subtitle.upper(), font=Theme.scaled_font(8, "bold"), foreground=a["muted"]).pack(anchor="w")
        self.lbl_step = ttk.Label(titles, text="", font=Theme.display_font(16))
        self.lbl_step.pack(anchor="w")
        ttk.Button(header, text="Learn from your mistakes", bootstyle="warning", command=self.learn).pack(side="right")
        for text, cmd in (("▶|", lambda: self.go(len(self.steps) - 1)), ("▶", lambda: self.move(1)),
                          ("◀", lambda: self.move(-1)), ("|◀", lambda: self.go(0))):
            ttk.Button(header, text=text, width=3, command=cmd).pack(side="right", padx=(0, 4))

        self.graph = tkinter.Canvas(self, height=Theme.scaled_val(120), bg=a["bg"], highlightthickness=0)
        self.graph.pack(side="bottom", fill="x", padx=pad, pady=(0, pad))
        self.graph.bind("<Configure>", lambda e: self._draw_graph())
        self.graph.bind("<Button-1>", self._on_graph_click)

        body = ttk.PanedWindow(self, orient=tkinter.HORIZONTAL)
        body.pack(fill="both", expand=True)
        self.board = tkinter.Canvas(body, bg=a["surface"], highlightthickness=0)
        self.board.bind("<Configure>", lambda e: self._draw_board())
        body.add(self.board, weight=3)

        side = ttk.Frame(body, padding=(pad, 0, pad, 0))
        body.add(side, weight=1)
        self.moves = ttk.Treeview(side, columns=("n", "you", "opp"), show="headings", height=12, selectmode="browse")
        for col, title, width in (("n", "#", 30), ("you", "YOU", 150), ("opp", "OPPONENT", 150)):
            self.moves.heading(col, text=title)
            self.moves.column(col, width=Theme.scaled_val(width), stretch=col != "n", anchor="w")
        self.moves.pack(fill="both", expand=True)
        self.moves.tag_configure("flag", foreground=a["gold"])
        self.moves.bind("<<TreeviewSelect>>", self._on_move_select)
        self._move_rows = move_rows(self.game, self.name, self.flagged_turns)
        for i, row in enumerate(self._move_rows):
            tags = ("flag",) if {row["you_turn"], row["opp_turn"]} & self.flagged_turns else ()
            self.moves.insert("", "end", iid=str(i), values=(row["round"], row["you"], row["opp"]), tags=tags)

        self.txt_turn = tkinter.Text(side, wrap="word", height=12, font=Theme.scaled_font(10), relief="flat", borderwidth=0)
        self.txt_turn.pack(fill="x", pady=(pad, pad))
        self.txt_turn.tag_configure("flag", foreground=a["gold"])
        self.txt_turn.tag_configure("head", font=Theme.scaled_font(8, "bold"), foreground=a["muted"])

        self._apply_colors()
        Theme.bind_theme_change(self, lambda e: self._apply_colors(), application_wide=False)

    def _apply_colors(self):
        self.configure(bg=Theme.BG_PRIMARY)
        self.txt_turn.configure(background=Theme.BG_PRIMARY, foreground=Theme.ARENA["body"],
                                insertbackground=Theme.TEXT_MAIN, selectbackground=Theme.ACCENT)

    # --- navigation ---
    def move(self, delta: int):
        if self.steps:
            self.go(self.index + delta)

    def go(self, index: int):
        if self.steps:
            self.index = max(0, min(len(self.steps) - 1, index))
            self.render()

    def learn(self):
        """Jump to the start of the next flagged turn and hide the answer until → is pressed."""
        self.learning = True
        for i in list(range(self.index + 1, len(self.steps))) + list(range(0, self.index + 1)):
            step = self.steps[i]
            if step["phase"] == "start" and step["entry"]["turn"] in self.flagged_turns:
                self.go(i)
                return
        self.learning = False
        self.render()

    def _on_move_select(self, _event=None):
        sel = self.moves.selection()
        if not sel or not self.steps:
            return
        row = self._move_rows[int(sel[0])]
        # Selection also changes when render() follows the current step; the
        # <<TreeviewSelect>> it fires arrives later, so ignore our own rows.
        if self.steps[self.index]["entry"]["turn"] in (row["you_turn"], row["opp_turn"]):
            return
        turn = row["you_turn"] or row["opp_turn"]
        for i, step in enumerate(self.steps):
            if step["entry"]["turn"] == turn and step["phase"] == "end":
                self.go(i)
                return

    def _on_graph_click(self, event):
        if not self.steps:
            return
        left, right = self._graph_bounds()
        frac = (event.x - left) / max(right - left, 1)
        self.go(round(frac * (len(self.steps) - 1)))

    # --- drawing ---
    def _graph_bounds(self):
        return Theme.scaled_val(40), max(self.graph.winfo_width(), 400) - Theme.scaled_val(12)

    def _draw_graph(self):
        a = Theme.ARENA
        g = self.graph
        g.delete("all")
        if not self.steps:
            return
        diffs = [s["snapshot"]["life"][0] - s["snapshot"]["life"][1] for s in self.steps]
        limit = max(10, max(abs(d) for d in diffs))
        h = max(g.winfo_height(), 80)
        top, bottom = Theme.scaled_val(34), h - Theme.scaled_val(18)
        mid = (top + bottom) / 2
        left, right = self._graph_bounds()
        x = lambda i: left + (right - left) * i / max(len(diffs) - 1, 1)
        y = lambda d: mid - d * (mid - top) / limit
        small = Theme.scaled_font(8)
        g.create_text(0, 8, text="LIFE DIFFERENCE (YOU − OPPONENT) · CLICK TO JUMP", anchor="nw", fill=a["muted"], font=Theme.scaled_font(8, "bold"))
        g.create_text(right, 8, text="◆ ?! questionable move", anchor="ne", fill=a["gold"], font=small)
        g.create_line(left, mid, right, mid, fill=a["line_strong"])
        for label, value in ((f"+{limit}", limit), ("0", 0), (f"−{limit}", -limit)):
            g.create_text(left - 6, y(value), text=label, anchor="e", fill=a["muted"], font=small)
        pts = [(x(i), y(d)) for i, d in enumerate(diffs)]
        if len(pts) > 1:
            g.create_polygon([(left, mid)] + pts + [(pts[-1][0], mid)], fill=blend(a["good"], a["bg"], 0.18), outline="")
            g.create_line(pts, fill=a["good"], width=2)
        last_turn = None
        for i, step in enumerate(self.steps):
            turn = step["entry"]["turn"]
            if turn != last_turn:
                g.create_text(x(i), bottom + 4, text=str(turn), anchor="n", fill=a["muted"], font=small)
                last_turn = turn
            if step["phase"] == "end" and turn in self.flagged_turns:
                cx, cy, r = x(i), y(diffs[i]), Theme.scaled_val(6)
                g.create_polygon(cx, cy - r, cx + r, cy, cx, cy + r, cx - r, cy, fill=a["gold"], outline="")
        cx = x(self.index)
        g.create_line(cx, top - 6, cx, bottom, fill=a["text"], dash=(3, 3))

    def _card_box(self, c, x, y, grp, power, toughness, tapped, loyalty=None, attached=None, state=None, fresh=False):
        a = Theme.ARENA
        w, h = Theme.scaled_val(self.CARD_W), Theme.scaled_val(self.CARD_H)
        card = self.card(grp)
        border = {"attacking": a["bad"], "blocking": a["good"]}.get(state, a["gold"] if fresh else a["control"])
        width = 2 if state or fresh else 1
        face = blend(a["raised"], a["surface"], 0.6) if tapped else a["raised"]
        c.create_rectangle(x, y, x + w, y + h, fill=face, outline=border, width=width)
        self._art(c, card, x + 4, y + 4, w - 8, Theme.scaled_val(48), face, 0.45 if tapped else 0.0)
        c.create_text(x + 6, y + Theme.scaled_val(56), text=self.name(grp), anchor="nw", width=w - 10,
                      fill=a["muted"] if tapped else a["text"], font=Theme.scaled_font(8, "bold"))
        note = f"on {self.name(attached)}" if attached else ""
        if note:
            c.create_text(x + 6, y + h - Theme.scaled_val(26), text=note, anchor="nw", width=w - 10,
                          fill=a["muted"], font=Theme.scaled_font(7))
        pt = f"{power or 0}/{toughness}" if toughness is not None else (f"L {loyalty}" if loyalty is not None else "")
        if pt:
            c.create_rectangle(x + w - Theme.scaled_val(38), y + h - Theme.scaled_val(20), x + w - 4, y + h - 4,
                               fill=a["bg"], outline=a["control"])
            c.create_text(x + w - Theme.scaled_val(21), y + h - Theme.scaled_val(12), text=pt, fill=a["text"],
                          font=Theme.display_font(10))
        badge = {"attacking": "ATTACKING", "blocking": "BLOCKING"}.get(state) or ("NEW" if fresh else ("TAPPED" if tapped else ""))
        if badge:
            c.create_text(x, y - 3, text=badge, anchor="sw", fill=border if border != a["control"] else a["muted"],
                          font=Theme.scaled_font(7, "bold"))
        return (x + w / 2, y + h / 2, x, y, w, h)

    def _art(self, c, card, x, y, w, h, face, dim=0.0):
        """The card's art crop, or its colour block until (or unless) the art loads."""
        photo = CardArt.get(c, card, w, h, dim=dim, on_ready=self._art_ready)
        if photo is not None:
            c.create_image(x, y, image=photo, anchor="nw")
        else:
            art = art_color(card.get("colors"))
            c.create_rectangle(x, y, x + w, y + h, fill=blend(art, face, 1.0 - dim * 0.9), width=0)

    def _art_ready(self):
        if self.winfo_exists():
            self._draw_board()

    def _board_row(self, c, cards, y, left, right, states, fresh):
        """Lay out a battlefield row; returns grp -> list of card centres."""
        w = Theme.scaled_val(self.CARD_W)
        gap = Theme.scaled_val(14)
        per_row = max(1, int((right - left + gap) // (w + gap)))
        spots = {}
        for i, (grp, power, toughness, tapped, *rest) in enumerate(cards):
            x = left + (i % per_row) * (w + gap)
            yy = y + (i // per_row) * (Theme.scaled_val(self.CARD_H) + gap + 8)
            box = self._card_box(c, x, yy, grp, power, toughness, tapped,
                                 rest[0] if rest else None, rest[1] if len(rest) > 1 else None,
                                 states.get(grp), grp in fresh)
            spots.setdefault(grp, []).append(box)
        rows = (len(cards) + per_row - 1) // per_row if cards else 1
        return spots, rows * (Theme.scaled_val(self.CARD_H) + gap + 8)

    def _player_strip(self, c, x, y, life, label, detail, color):
        a = Theme.ARENA
        s = Theme.scaled_val(44)
        c.create_rectangle(x, y, x + s, y + s, outline=color, width=1, fill=a["surface"])
        c.create_text(x + s / 2, y + s / 2, text=str(life), fill=a["text"], font=Theme.display_font(18))
        c.create_text(x + s + 10, y + 4, text=label, anchor="nw", fill=color, font=Theme.scaled_font(8, "bold"))
        c.create_text(x + s + 10, y + s - 4, text=detail, anchor="sw", fill=a["muted"], font=Theme.scaled_font(9))
        return (x + s / 2, y + s / 2)

    def _lands_text(self, lands):
        names = {}
        for land in lands:
            n = self.name(land[0])
            names[n] = names.get(n, 0) + 1
        untapped = sum(1 for land in lands if not land[3])
        listed = " · ".join(f"{n} ×{k}" if k > 1 else n for n, k in names.items())
        return f"{listed or 'no lands'}   ({len(lands)} lands, {untapped} open)"

    def _draw_board(self):
        c = self.board
        c.delete("all")
        if not self.steps:
            c.create_text(20, 20, text="No replay data for this game (recorded before replays existed).",
                          anchor="nw", fill=Theme.ARENA["muted"], font=Theme.scaled_font(11))
            return
        a = Theme.ARENA
        step = self.steps[self.index]
        entry, snap = step["entry"], step["snapshot"]
        width = max(c.winfo_width(), 600)
        left, right = Theme.scaled_val(24), width - Theme.scaled_val(24)
        my_life, opp_life = snap["life"]

        combat = entry.get("combat") if step["phase"] == "end" else None
        states = {}
        if combat:
            for _who, g in combat["attackers"]:
                states[g] = "attacking"
            for b, _att in combat["blocks"]:
                states[b] = "blocking"
        before = self.steps[self.index - 1]["snapshot"] if self.index > 0 else None
        def fresh_of(key):
            if not before:
                return set()
            old = {card[0] for card in before[key]}
            return {card[0] for card in snap[key] if card[0] not in old}

        y = Theme.scaled_val(14)
        opp_face = self._player_strip(
            c, left, y, opp_life, "OPPONENT",
            f"Hand {snap['opp_hand']} · Graveyard {len(snap['opp_graveyard'])}", a["bad"])
        c.create_text(left + Theme.scaled_val(250), y + Theme.scaled_val(22), text=self._lands_text(snap["opp_lands"]),
                      anchor="w", fill=a["body"], font=Theme.scaled_font(9))
        y += Theme.scaled_val(76)
        opp_spots, used = self._board_row(c, snap["opp_board"], y, left, right, states, fresh_of("opp_board"))
        y += used

        c.create_line(left, y, right, y, fill=a["line"])
        y += Theme.scaled_val(26)
        my_spots, used = self._board_row(c, snap["my_board"], y, left, right, states, fresh_of("my_board"))
        y += used

        untapped = [land[0] for land in snap["my_lands"] if not land[3]]
        me_face = self._player_strip(
            c, left, y, my_life, "YOU",
            f"Library {snap['library']} · Graveyard {len(snap['my_graveyard'])}", a["good"])
        c.create_text(left + Theme.scaled_val(250), y + Theme.scaled_val(22), text=self._lands_text(snap["my_lands"]),
                      anchor="w", fill=a["body"], font=Theme.scaled_font(9))
        y += Theme.scaled_val(60)

        marks = castable_marks(snap, self.card)
        hw, hh = Theme.scaled_val(self.HAND_W), Theme.scaled_val(self.HAND_H)
        c.create_text(left, y, text="HAND · gold = castable now, blue = castable after a land drop", anchor="nw",
                      fill=a["muted"], font=Theme.scaled_font(8, "bold"))
        y += Theme.scaled_val(18)
        for i, g in enumerate(snap["hand"]):
            x = left + i * (hw + 6)
            if x + hw > right:
                break
            mark = marks.get(g, "")
            border = a["gold"] if mark == "now" else (a["good"] if mark else a["control"])
            c.create_rectangle(x, y, x + hw, y + hh, fill=a["raised"], outline=border, width=2 if mark else 1)
            self._art(c, self.card(g), x + 3, y + 3, hw - 6, Theme.scaled_val(29), a["raised"])
            c.create_text(x + 5, y + Theme.scaled_val(36), text=self.name(g), anchor="nw", width=hw - 8,
                          fill=a["text"], font=Theme.scaled_font(7, "bold"))

        if combat:
            self._draw_combat(c, combat, opp_spots, my_spots, opp_face, me_face)
        c.configure(scrollregion=(0, 0, width, y + hh + 20))

    def _draw_combat(self, c, combat, opp_spots, my_spots, opp_face, me_face):
        a = Theme.ARENA
        spots = {"you": my_spots, "opp": opp_spots}
        blocked = {g for _b, att in combat["blocks"] for g in att}
        for who, g in combat["attackers"]:
            boxes = spots[who].get(g)
            if not boxes or g in blocked:
                continue
            sx, sy = boxes[0][0], boxes[0][1]
            tx, ty = me_face if who == "opp" else opp_face
            c.create_line(sx, sy, tx, ty, fill=a["bad"], width=3, arrow="last", arrowshape=(14, 16, 6), smooth=True)
        for b, att in combat["blocks"]:
            for side, other in (("you", "opp"), ("opp", "you")):
                bbox = spots[side].get(b)
                if not bbox:
                    continue
                for g in att:
                    target = spots[other].get(g)
                    if target:
                        c.create_line(bbox[0][0], bbox[0][1], target[0][0], target[0][1], fill=a["good"], width=3,
                                      arrow="last", arrowshape=(14, 16, 6))
                break

    def render(self):
        if not self.steps:
            self.lbl_step.config(text="No replay data for this game")
            self._draw_board()
            return
        step = self.steps[self.index]
        entry = step["entry"]
        whose = "You" if entry["active"] == "you" else "Opponent"
        phase = "start of turn" if step["phase"] == "start" else "end of turn"
        self.lbl_step.config(text=f"Turn {entry['turn']} · {whose} · {phase}   {self.index + 1}/{len(self.steps)}")
        self._draw_board()
        self._draw_graph()
        self._sync_moves(entry["turn"])
        self._write_notes(step)

    def _sync_moves(self, turn):
        for i, row in enumerate(self._move_rows):
            if turn in (row["you_turn"], row["opp_turn"]):
                if self.moves.selection() != (str(i),):
                    self.moves.selection_set(str(i))
                self.moves.see(str(i))
                break

    def _write_notes(self, step):
        entry = step["entry"]
        marks = castable_marks(step["snapshot"], self.card)
        t = self.txt_turn
        t.configure(state="normal")
        t.delete("1.0", "end")
        hide = self.learning and step["phase"] == "start"
        turn_findings = [f for f in self.findings if f.turn == entry["turn"] or (f.turn == 0 and self.index == 0)]
        if hide and entry["turn"] in self.flagged_turns:
            t.insert("end", "YOUR MOVE\n", "head")
            t.insert("end", f"Turn {entry['turn']} was flagged. Look at the board and your hand: what would you do? "
                            "Press → to see what you did and the review.\n", "flag")
        elif turn_findings:
            t.insert("end", "REVIEW\n", "head")
            for f in turn_findings:
                prefix = "?! " if f.level == "review" else ("✓ " if f.level == "ok" else "· ")
                t.insert("end", f"{prefix}{f.text}\n", "flag" if f.level == "review" else "")
            t.insert("end", "\n")
        if step["phase"] == "end":
            self.learning = False
            lines = event_lines(entry, self.name)
            t.insert("end", "WHAT HAPPENED\n", "head")
            t.insert("end", "\n".join(lines) if lines else "Nothing notable.")
        else:
            castable = [self.name(g) for g, m in marks.items() if m]
            if entry["active"] == "you":
                text = "Castable: " + (", ".join(castable) if castable else "nothing")
            else:
                instants = [self.name(g) for g, m in marks.items()
                            if m == "now" and ("Instant" in self.card(g).get("types", [])
                                               or "Flash" in (self.card(g).get("keywords") or []))]
                text = "Opponent's turn. Your instant-speed options: " + (", ".join(instants) or "none")
            t.insert("end", "START OF TURN\n", "head")
            t.insert("end", text + ".")
        t.configure(state="disabled")
