"""
src/ui/windows/match_history_panel.py
Personal match history: your record per format, set and deck colors, read from
the Arena logs and kept in Match_History.json; plus a post-game review of each
game (kept in Game_Reviews.json).
"""

import os
import logging
import tkinter
from tkinter import ttk
from src import constants
from src.ui.styles import Theme
from src.match_history import update_history, summarize, event_format
from src.game_review import (
    GameLog,
    update_reviews,
    review_game,
    game_summary_text,
    load_benchmarks,
)

logger = logging.getLogger(__name__)

LIMITED_HINTS = ("Draft", "Sealed")
LEVEL_ICONS = {"review": "⚠️", "ok": "✓", "info": "ℹ️"}


class MatchHistoryPanel(ttk.Frame):
    def __init__(self, parent, draft_manager, configuration):
        super().__init__(parent)
        self.draft = draft_manager
        self.configuration = configuration
        self._games = []
        self._build_ui()

    def _log_paths(self):
        player_log = self.configuration.settings.arena_log_location
        if not player_log:
            return []
        return [os.path.join(os.path.dirname(player_log), "Player-prev.log"), player_log]

    def _build_ui(self):
        bar = ttk.Frame(self, style="Card.TFrame", padding=Theme.scaled_val(5))
        bar.pack(fill="x", pady=Theme.scaled_val((0, 5)))
        self.lbl_summary = ttk.Label(bar, text="", font=Theme.scaled_font(10, "bold"))
        self.lbl_summary.pack(side="left", padx=Theme.scaled_val(5))
        ttk.Button(bar, text="Refresh", command=self.refresh).pack(
            side="right", padx=Theme.scaled_val(5)
        )

        panes = ttk.PanedWindow(self, orient=tkinter.HORIZONTAL)
        panes.pack(fill="both", expand=True)

        left = ttk.Frame(panes)
        panes.add(left, weight=1)
        self.table = ttk.Treeview(
            left,
            columns=("format", "set", "colors", "record", "win_rate"),
            show="headings",
            height=6,
        )
        for col, title, width in (
            ("format", "Format", 130),
            ("set", "Set", 50),
            ("colors", "Colors", 80),
            ("record", "Record (W-L)", 100),
            ("win_rate", "Win %", 60),
        ):
            self.table.heading(col, text=title)
            self.table.column(col, width=Theme.scaled_val(width), stretch=col == "format")
        self.table.pack(fill="x")

        ttk.Label(left, text="GAME REVIEW", font=Theme.scaled_font(10, "bold"), bootstyle="primary").pack(
            anchor="w", pady=Theme.scaled_val((8, 2))
        )
        self.games_table = ttk.Treeview(
            left,
            columns=("game", "event", "result", "side", "flags"),
            show="headings",
        )
        for col, title, width in (
            ("game", "#", 40),
            ("event", "Event", 170),
            ("result", "Result", 60),
            ("side", "Play/Draw", 70),
            ("flags", "To review", 70),
        ):
            self.games_table.heading(col, text=title)
            self.games_table.column(col, width=Theme.scaled_val(width), stretch=col == "event")
        self.games_table.pack(fill="both", expand=True)
        self.games_table.bind("<<TreeviewSelect>>", self._show_review)

        right = ttk.Frame(panes, padding=Theme.scaled_val((8, 0, 0, 0)))
        panes.add(right, weight=1)
        top = ttk.Frame(right)
        top.pack(fill="x")
        ttk.Label(top, text="FINDINGS (after the game, from your logs)", font=Theme.scaled_font(9, "bold")).pack(side="left")
        ttk.Button(top, text="Copy summary", bootstyle="info-outline", command=self._copy_summary).pack(side="right")
        self.txt_review = tkinter.Text(right, wrap="word", height=18, font=Theme.scaled_font(9))
        self.txt_review.pack(fill="both", expand=True, pady=Theme.scaled_val((4, 0)))
        self.txt_review.configure(state="disabled")

    def refresh(self):
        lookup = self.draft.set_data.get_data_by_id
        try:
            history = update_history(self._log_paths(), lookup)
        except Exception as e:
            logger.warning(f"Match history error: {e}")
            history = []

        limited = [m for m in history if any(h in m.get("format", "") for h in LIMITED_HINTS)]
        if limited:
            wins = sum(m["won"] for m in limited)
            self.lbl_summary.config(
                text=f"Limited: {wins}-{len(limited) - wins} ({wins / len(limited):.0%}) "
                f"over {len(limited)} matches"
            )
        else:
            self.lbl_summary.config(
                text="No Limited matches recorded yet. Matches are saved while the tool runs."
            )

        self.table.delete(*self.table.get_children())
        for row in summarize(history):
            colors = row["colors"]
            self.table.insert(
                "",
                "end",
                values=(
                    row["format"],
                    row["set"] or "-",
                    constants.COLOR_NAMES_DICT.get(colors, colors) if colors else "-",
                    f"{row['wins']}-{row['losses']}",
                    f"{row['win_rate']:.0%}",
                ),
            )
        self._refresh_games(history, lookup)

    def _refresh_games(self, history, lookup):
        try:
            events = {m["match_id"]: m["event"] for m in history}
            stored = update_reviews(self._log_paths(), events)
        except Exception as e:
            logger.warning(f"Game review error: {e}")
            stored = {}

        self._games = []
        self.games_table.delete(*self.games_table.get_children())
        for key, data in reversed(list(stored.items())):
            game = GameLog.from_dict(data)
            fmt, set_code = event_format(game.event)
            findings = review_game(game, lookup, load_benchmarks(set_code, fmt))
            self._games.append((game, findings))
            self.games_table.insert(
                "",
                "end",
                iid=str(len(self._games) - 1),
                values=(
                    game.game_number,
                    game.event or "-",
                    {True: "Win", False: "Loss", None: "?"}[game.won],
                    "Play" if game.on_play else "Draw",
                    sum(f.level == "review" for f in findings),
                ),
            )

    def _selected(self):
        selection = self.games_table.selection()
        return self._games[int(selection[0])] if selection else None

    def _show_review(self, _event=None):
        selected = self._selected()
        if not selected:
            return
        game, findings = selected
        lines = [f"{'Won' if game.won else 'Lost'} on the {'play' if game.on_play else 'draw'}, "
                 f"{game.mulligans} mulligan(s), {len(game.turns)} turns.", ""]
        if not findings:
            lines.append("Nothing flagged.")
        for f in findings:
            where = "Mulligan" if f.turn == 0 else f"Turn {f.turn}"
            lines.append(f"{LEVEL_ICONS.get(f.level, '•')} {where}: {f.text}")
        lines += ["", "⚠️ = worth a second look, not necessarily a mistake."]
        self.txt_review.configure(state="normal")
        self.txt_review.delete("1.0", "end")
        self.txt_review.insert("1.0", "\n".join(lines))
        self.txt_review.configure(state="disabled")

    def _copy_summary(self):
        selected = self._selected()
        if not selected:
            return
        game, findings = selected
        text = game_summary_text(game, self.draft.set_data.get_data_by_id, findings)
        self.clipboard_clear()
        self.clipboard_append(text)
