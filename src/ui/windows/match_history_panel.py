"""
src/ui/windows/match_history_panel.py
Personal match history: your record per format, set and deck colors, read from
the Arena logs and kept in Match_History.json.
"""

import os
import logging
from tkinter import ttk
from src import constants
from src.ui.styles import Theme
from src.match_history import update_history, summarize

logger = logging.getLogger(__name__)

LIMITED_HINTS = ("Draft", "Sealed")


class MatchHistoryPanel(ttk.Frame):
    def __init__(self, parent, draft_manager, configuration):
        super().__init__(parent)
        self.draft = draft_manager
        self.configuration = configuration
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

        self.table = ttk.Treeview(
            self,
            columns=("format", "set", "colors", "record", "win_rate"),
            show="headings",
        )
        for col, title, width in (
            ("format", "Format", 160),
            ("set", "Set", 60),
            ("colors", "Colors", 80),
            ("record", "Record (W-L)", 110),
            ("win_rate", "Win %", 70),
        ):
            self.table.heading(col, text=title)
            self.table.column(col, width=Theme.scaled_val(width), stretch=col == "format")
        self.table.pack(fill="both", expand=True)

    def refresh(self):
        try:
            history = update_history(self._log_paths(), self.draft.set_data.get_data_by_id)
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
