"""
src/ui/windows/match_tracker.py
Always-on-top in-game tracker: cards left in your library with the odds of
drawing each one next, and the cards your opponent has shown this game.
Needs Arena's "Detailed Logs (Plugin Support)" option.
"""

import tkinter
from tkinter import ttk
from src.ui.styles import Theme
from src.game_tracker import GameTracker, LogTailer

REFRESH_MS = 1000


class MatchTrackerWindow(tkinter.Toplevel):
    def __init__(self, parent, app_context, on_close):
        super().__init__(parent)
        self.app = app_context
        self.on_close = on_close
        self.title("Match Tracker")
        self.geometry(f"{Theme.scaled_val(320)}x{Theme.scaled_val(620)}")
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.close)
        # ttk styles are global and already active; only the window background
        # (a plain Tk option) needs the palette color.
        self.configure(bg=Theme.BG_PRIMARY)
        Theme.bind_theme_change(self, lambda e: self.configure(bg=Theme.BG_PRIMARY), application_wide=False)

        self.tracker = GameTracker()
        self.tailer = LogTailer(self.app.configuration.settings.arena_log_location, self.tracker)
        self.tailer.start()
        self._card_names = {}

        self._build_ui()
        self._refresh()

    def _build_ui(self):
        pad = Theme.scaled_val(6)
        self.lbl_library = ttk.Label(self, text="", font=Theme.scaled_font(11, "bold"), bootstyle="primary")
        self.lbl_library.pack(anchor="w", padx=pad, pady=(pad, 2))
        self.library_table = self._table(("card", "left", "odds"), ("Card", "Left", "Next draw"), (180, 45, 75))
        self.library_table.pack(fill="both", expand=True, padx=pad)

        ttk.Label(self, text="OPPONENT HAS SHOWN", font=Theme.scaled_font(10, "bold"), bootstyle="warning").pack(
            anchor="w", padx=pad, pady=(pad, 2)
        )
        self.opponent_table = self._table(("card", "seen"), ("Card", "Seen"), (240, 60))
        self.opponent_table.configure(height=8)
        self.opponent_table.pack(fill="both", expand=False, padx=pad, pady=(0, pad))

    def _table(self, cols, titles, widths):
        table = ttk.Treeview(self, columns=cols, show="headings", height=14)
        for col, title, width in zip(cols, titles, widths):
            table.heading(col, text=title)
            table.column(col, width=Theme.scaled_val(width), stretch=col == "card", anchor="w" if col == "card" else "e")
        return table

    def _card_name(self, grp_id):
        if grp_id not in self._card_names:
            cards = self.app.orchestrator.scanner.set_data.get_data_by_id([str(grp_id)])
            name = cards[0].get("name") if cards else None
            if not name or str(name).isdigit():
                return f"#{grp_id}"  # not resolved yet; retried next refresh
            self._card_names[grp_id] = name
        return self._card_names[grp_id]

    def _refresh(self):
        if not self.winfo_exists():
            return
        snap = self.tracker.snapshot()
        if snap["active"]:
            self.lbl_library.config(text=f"YOUR LIBRARY: {snap['library_size']} cards")
        elif snap["game"]:
            self.lbl_library.config(text="Game over. Waiting for the next game...")
        else:
            self.lbl_library.config(text="Waiting for a game (enable Detailed Logs in Arena)...")

        self.library_table.delete(*self.library_table.get_children())
        rows = sorted(
            snap["remaining"].items(),
            key=lambda kv: (-kv[1], self._card_name(kv[0])),
        )
        for grp_id, left in rows:
            odds = snap["draw_odds"].get(grp_id, 0.0)
            self.library_table.insert("", "end", values=(self._card_name(grp_id), left, f"{odds:.0%}"))

        self.opponent_table.delete(*self.opponent_table.get_children())
        for grp_id, seen in sorted(snap["opponent"].items(), key=lambda kv: self._card_name(kv[0])):
            self.opponent_table.insert("", "end", values=(self._card_name(grp_id), seen))

        self._refresh_job = self.after(REFRESH_MS, self._refresh)

    def close(self):
        self.tailer.stop()
        if getattr(self, "_refresh_job", None):
            self.after_cancel(self._refresh_job)
        self.destroy()
        self.on_close()
