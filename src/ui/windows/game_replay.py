"""
src/ui/windows/game_replay.py
Turn-by-turn replay of a recorded game: both boards, your hand (with what you
could cast), life, graveyards, what happened during the turn, and the review
findings for that turn.
"""

import tkinter
from tkinter import ttk
from typing import Callable, Dict, List

from src import constants
from src.ui.styles import Theme
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


class GameReplayWindow(tkinter.Toplevel):
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

        result = {True: "Won", False: "Lost", None: "?"}[game.won]
        self.title(f"Replay — {game.event or 'game'} — {result} on the {'play' if game.on_play else 'draw'}")
        self.geometry(f"{Theme.scaled_val(1150)}x{Theme.scaled_val(720)}")
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
        pad = Theme.scaled_val(6)
        nav = ttk.Frame(self, padding=pad)
        nav.pack(fill="x")
        ttk.Button(nav, text="◀ Prev", command=lambda: self.move(-1)).pack(side="left")
        ttk.Button(nav, text="Next ▶", command=lambda: self.move(1)).pack(side="left", padx=pad)
        self.lbl_step = ttk.Label(nav, text="", font=Theme.scaled_font(12, "bold"), bootstyle="primary")
        self.lbl_step.pack(side="left", padx=pad)
        ttk.Label(nav, text="(← → keys)", font=Theme.scaled_font(8)).pack(side="right")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(body)
        right.pack(side="right", fill="both", expand=True)

        self.opp_frame = ttk.Labelframe(left, text="OPPONENT", padding=pad)
        self.opp_frame.pack(fill="x", padx=pad)
        self.opp_board = self._table(self.opp_frame, ("card", "pt", "state"), ("Battlefield", "P/T", ""), (330, 80, 140), 4)
        self.lbl_opp = ttk.Label(self.opp_frame, text="", font=Theme.scaled_font(9))
        self.lbl_opp.pack(anchor="w")

        middle = ttk.Labelframe(right, text="THIS TURN", padding=pad)
        middle.pack(fill="both", expand=True, padx=pad, pady=(0, pad))
        self.txt_turn = tkinter.Text(middle, wrap="word", width=40, font=Theme.scaled_font(10), relief="flat", borderwidth=0)
        self.txt_turn.pack(fill="both", expand=True)
        self.txt_turn.tag_configure("flag", foreground=Theme.WARNING)
        self.txt_turn.tag_configure("head", font=Theme.scaled_font(9, "bold"))

        self.my_frame = ttk.Labelframe(left, text="YOU", padding=pad)
        self.my_frame.pack(fill="x", padx=pad, pady=(0, pad))
        self.my_board = self._table(self.my_frame, ("card", "pt", "state"), ("Battlefield", "P/T", ""), (330, 80, 140), 4)
        self.lbl_me = ttk.Label(self.my_frame, text="", font=Theme.scaled_font(9))
        self.lbl_me.pack(anchor="w")
        self.hand = self._table(self.my_frame, ("card", "cost", "castable"), ("Hand", "Cost", "Castable"), (330, 80, 110), 8)

        self._apply_colors()
        Theme.bind_theme_change(self, lambda e: self._apply_colors(), application_wide=False)

    def _table(self, parent, cols, titles, widths, height):
        table = ttk.Treeview(parent, columns=cols, show="headings", height=height)
        for col, title, width in zip(cols, titles, widths):
            table.heading(col, text=title)
            table.column(col, width=Theme.scaled_val(width), stretch=col == "card", anchor="w" if col == "card" else "center")
        table.pack(fill="x", pady=(0, Theme.scaled_val(4)))
        return table

    def _apply_colors(self):
        self.configure(bg=Theme.BG_PRIMARY)
        self.txt_turn.configure(background=Theme.BG_TERTIARY, foreground=Theme.TEXT_MAIN,
                                insertbackground=Theme.TEXT_MAIN, selectbackground=Theme.ACCENT)

    # --- rendering ---
    def move(self, delta: int):
        if self.steps:
            self.index = max(0, min(len(self.steps) - 1, self.index + delta))
            self.render()

    def _fill_board(self, table, cards):
        table.delete(*table.get_children())
        for grp, power, toughness, tapped, *rest in cards:
            loyalty = rest[0] if rest else None
            attached = rest[1] if len(rest) > 1 else None
            pt = f"{power or 0}/{toughness}" if toughness is not None else (f"loyalty {loyalty}" if loyalty is not None else "")
            state = ", ".join(x for x in ("tapped" if tapped else "", f"on {self.name(attached)}" if attached else "") if x)
            table.insert("", "end", values=(self.name(grp), pt, state))

    def render(self):
        if not self.steps:
            self.lbl_step.config(text="No replay data for this game (recorded before replays existed).")
            return
        step = self.steps[self.index]
        entry, snap = step["entry"], step["snapshot"]
        whose = "Your turn" if entry["active"] == "you" else "Opponent's turn"
        phase = "start (what you had)" if step["phase"] == "start" else "end (how it finished)"
        self.lbl_step.config(text=f"Turn {entry['turn']} · {whose} · {phase}   [{self.index + 1}/{len(self.steps)}]")

        my_life, opp_life = snap["life"]
        self._fill_board(self.opp_board, snap["opp_board"])
        opp_untapped = sum(1 for land in snap["opp_lands"] if not land[3])
        self.lbl_opp.config(
            text=f"Life {opp_life} · Lands {len(snap['opp_lands'])} ({opp_untapped} untapped) · Hand {snap['opp_hand']} cards · "
                 f"Graveyard: {', '.join(self.name(g) for g in snap['opp_graveyard']) or '-'}"
        )

        self._fill_board(self.my_board, snap["my_board"])
        my_untapped = [land[0] for land in snap["my_lands"] if not land[3]]
        colors = "".join(sorted(set("".join(land_colors(my_untapped, self.card))))) or "-"
        self.lbl_me.config(
            text=f"Life {my_life} · Lands {len(snap['my_lands'])} ({len(my_untapped)} untapped: {colors}) · "
                 f"Library {snap['library']} · Graveyard: {', '.join(self.name(g) for g in snap['my_graveyard']) or '-'}"
        )

        marks = castable_marks(snap, self.card)
        self.hand.delete(*self.hand.get_children())
        for g in snap["hand"]:
            c = self.card(g)
            mark = {"now": "✓ now", "with land drop": "✓ + land", "": ""}.get(marks.get(g, ""), "")
            self.hand.insert("", "end", values=(self.name(g), c.get("mana_cost", "") or "land", mark))

        self.txt_turn.configure(state="normal")
        self.txt_turn.delete("1.0", "end")
        turn_findings = [f for f in self.findings if f.turn == entry["turn"] or (f.turn == 0 and self.index == 0)]
        if turn_findings:
            self.txt_turn.insert("end", "REVIEW\n", "head")
            for f in turn_findings:
                tag = "flag" if f.level == "review" else ""
                self.txt_turn.insert("end", f"{LEVEL_ICONS.get(f.level, '•')} {f.text}\n", tag)
            self.txt_turn.insert("end", "\n")
        if step["phase"] == "end":
            lines = event_lines(entry, self.name)
            self.txt_turn.insert("end", "WHAT HAPPENED\n", "head")
            self.txt_turn.insert("end", "\n".join(lines) if lines else "Nothing notable.")
        else:
            castable = [self.name(g) for g, m in marks.items() if m]
            if entry["active"] == "you":
                text = "Start of your turn. Castable: " + (", ".join(castable) if castable else "nothing")
            else:
                instants = [self.name(g) for g, m in marks.items()
                            if m == "now" and ("Instant" in self.card(g).get("types", [])
                                               or "Flash" in (self.card(g).get("keywords") or []))]
                text = "Start of the opponent's turn. Instant-speed options: " + (", ".join(instants) or "none")
            self.txt_turn.insert("end", text + ". Press → to see what happened.")
        self.txt_turn.configure(state="disabled")
