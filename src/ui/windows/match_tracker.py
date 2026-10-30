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
from src.ui.components import CardStrips

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
        self._cards = {}

        self._build_ui()
        self._refresh()

    def _build_ui(self):
        a = Theme.ARENA
        pad = Theme.scaled_val(10)
        self.lbl_library = ttk.Label(self, text="", font=Theme.display_font(14))
        self.lbl_library.pack(anchor="w", padx=pad, pady=(pad, 4))

        odds = ttk.Frame(self)
        odds.pack(fill="x", padx=pad, pady=(0, pad))
        self.lbl_land = self._odds_box(odds, "LAND NEXT")
        self.lbl_spell = self._odds_box(odds, "SPELL NEXT")

        self.library = CardStrips(self, height=Theme.scaled_val(380))
        self.library.pack(fill="both", expand=True, padx=Theme.scaled_val(6))

        ttk.Label(
            self, text="OPPONENT HAS SHOWN", font=Theme.scaled_font(9, "bold"),
            foreground=a["bad"],
        ).pack(anchor="w", padx=pad, pady=(pad, 2))
        self.lbl_opponent = ttk.Label(
            self, text="-", wraplength=Theme.scaled_val(290), justify="left",
            foreground=a["body"], font=Theme.scaled_font(10),
        )
        self.lbl_opponent.pack(anchor="w", padx=pad, pady=(0, pad))

    def _odds_box(self, parent, title):
        box = ttk.Frame(parent)
        box.pack(side="left", fill="x", expand=True)
        ttk.Label(box, text=title, font=Theme.scaled_font(8, "bold"), foreground=Theme.ARENA["muted"]).pack(anchor="w")
        value = ttk.Label(box, text="-", font=Theme.display_font(22))
        value.pack(anchor="w")
        detail = ttk.Label(box, text="", font=Theme.scaled_font(9), foreground=Theme.ARENA["muted"])
        detail.pack(anchor="w")
        return value, detail

    def _card_info(self, grp_id):
        if grp_id not in self._cards:
            cards = self.app.orchestrator.scanner.set_data.get_data_by_id([str(grp_id)])
            card = cards[0] if cards else {}
            name = card.get("name")
            if not name or str(name).isdigit():
                return {"name": f"#{grp_id}"}  # not resolved yet; retried next refresh
            self._cards[grp_id] = card
        return self._cards[grp_id]

    def _card_name(self, grp_id):
        return self._card_info(grp_id).get("name", f"#{grp_id}")

    def _refresh(self):
        if not self.winfo_exists():
            return
        snap = self.tracker.snapshot()
        size = snap.get("library_size") or 0
        if snap["active"]:
            self.lbl_library.config(text=f"Library {size}")
        elif snap["game"]:
            self.lbl_library.config(text="Game over. Waiting for the next game...")
        else:
            self.lbl_library.config(text="Waiting for a game (enable Detailed Logs in Arena)...")

        rows, lands = [], 0
        for grp_id, left in snap["remaining"].items():
            card = self._card_info(grp_id)
            is_land = "Land" in card.get("types", [])
            lands += left if is_land else 0
            odds = snap["draw_odds"].get(grp_id, 0.0)
            rows.append({
                "cost": "–" if is_land else int(card.get("cmc", 0) or 0),
                "sort": (-1 if is_land else int(card.get("cmc", 0) or 0), card.get("name", "")),
                "name": card.get("name", f"#{grp_id}"),
                "colors": card.get("colors", []),
                "count": left,
                "value": f"{odds:.0%}",
                "strong": odds >= 0.15,
                "card": card,
            })
        rows.sort(key=lambda r: r["sort"])
        self.library.set_rows(rows)

        spells = max(size - lands, 0)
        for (value, detail), n in ((self.lbl_land, lands), (self.lbl_spell, spells)):
            value.config(text=f"{n / size:.0%}" if size else "-")
            detail.config(text=f"{n} of {size}" if size else "")

        shown = sorted(self._card_name(g) for g in snap["opponent"])
        self.lbl_opponent.config(text=" · ".join(shown) if shown else "-")

        self._refresh_job = self.after(REFRESH_MS, self._refresh)

    def close(self):
        self.tailer.stop()
        if getattr(self, "_refresh_job", None):
            self.after_cancel(self._refresh_job)
        self.destroy()
        self.on_close()
