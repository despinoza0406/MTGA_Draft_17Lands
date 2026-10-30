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
from PIL import Image, ImageDraw, ImageTk
from src import constants
from src.ui.styles import Theme
from src.match_history import update_history, summarize, event_format
from src.game_review import (
    GameLog,
    update_reviews,
    review_game,
    game_summary_text,
    load_benchmarks,
    patterns,
    life_diff_series,
    key_cards,
    spotlight_game,
    LEVEL_SYMBOLS,
    LEVEL_HELP,
    KIND_HELP,
    SPOTLIGHT_HELP,
)
from src.ui.components import art_color, sparkline_points, HoverTip
from src.ui.card_art import CardArt

logger = logging.getLogger(__name__)

LIMITED_HINTS = ("Draft", "Sealed")
COLUMN_HELP = {
    "#0": "The three best-rated spells you cast, then your life minus the opponent's, turn by turn "
          "(blue line: won, orange: lost).",
    "flags": "How many ?! findings the game has. ★ marks the game most worth a replay.",
}


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
        # Spotlight: the one game to replay first (hidden when nothing is flagged).
        self.spotlight = ttk.Frame(left)
        self.lbl_spotlight = ttk.Label(self.spotlight, text="", font=Theme.scaled_font(9, "bold"),
                                       foreground=Theme.ARENA["gold"], wraplength=Theme.scaled_val(460), justify="left")
        self.lbl_spotlight.pack(side="left", fill="x", expand=True)
        ttk.Button(self.spotlight, text="▶ Replay it", bootstyle="warning-outline",
                   command=self._replay_spotlight).pack(side="right")
        self.lbl_spotlight.bind("<Enter>", lambda e: HoverTip.show(self, SPOTLIGHT_HELP, e.x_root, e.y_root))
        self.lbl_spotlight.bind("<Leave>", HoverTip.hide)
        self._spotlight_index = None
        self.games_table = ttk.Treeview(
            left,
            columns=("game", "event", "result", "side", "flags"),
            show="tree headings",
        )
        self.games_table.heading("#0", text="Key cards · life difference")
        self.games_table.column("#0", width=Theme.scaled_val(190), stretch=False)
        self._row_images = []
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
        self.games_table.tag_configure("spotlight", foreground=Theme.ARENA["gold"])
        self.games_table.bind("<<TreeviewSelect>>", self._show_review)
        self.games_table.bind("<Motion>", self._on_games_hover, add="+")
        self.games_table.bind("<Leave>", HoverTip.hide, add="+")
        self.games_table.bind("<Double-1>", lambda e: self._open_replay())

        right = ttk.Frame(panes, padding=Theme.scaled_val((8, 0, 0, 0)))
        panes.add(right, weight=1)
        ttk.Label(right, text="PATTERNS (your Limited games)", font=Theme.scaled_font(9, "bold")).pack(anchor="w")
        self.txt_patterns = tkinter.Text(
            right, wrap="word", height=9, font=Theme.scaled_font(9), relief="flat", borderwidth=0
        )
        self.txt_patterns.pack(fill="x", pady=Theme.scaled_val((4, 8)))
        self.txt_patterns.configure(state="disabled")
        top = ttk.Frame(right)
        top.pack(fill="x")
        ttk.Label(top, text="FINDINGS (after the game, from your logs)", font=Theme.scaled_font(9, "bold")).pack(side="left")
        ttk.Button(top, text="Copy summary", bootstyle="info-outline", command=self._copy_summary).pack(side="right")
        ttk.Button(top, text="▶ Replay", bootstyle="success-outline", command=self._open_replay).pack(
            side="right", padx=Theme.scaled_val(5)
        )
        self.txt_review = tkinter.Text(
            right, wrap="word", height=18, font=Theme.scaled_font(9), relief="flat", borderwidth=0
        )
        self.txt_review.pack(fill="both", expand=True, pady=Theme.scaled_val((4, 0)))
        self.txt_review.configure(state="disabled")
        self.txt_review.tag_configure("review", foreground=Theme.ARENA["gold"])
        self.txt_review.tag_configure("legend", foreground=Theme.ARENA["muted"])
        for level, help_text in LEVEL_HELP.items():
            HoverTip.bind_text_tag(self.txt_review, f"sym_{level}", help_text)
        for i, help_text in enumerate(KIND_HELP.values()):
            HoverTip.bind_text_tag(self.txt_review, f"kind_{i}", help_text)
        self._apply_text_colors()
        Theme.bind_theme_change(self, lambda e: self._apply_text_colors())

    def _apply_text_colors(self):
        # Plain tk.Text ignores ttk themes; paint it with the active palette.
        for text in (self.txt_review, self.txt_patterns):
            text.configure(
                background=Theme.BG_TERTIARY,
                foreground=Theme.TEXT_MAIN,
                insertbackground=Theme.TEXT_MAIN,
                selectbackground=Theme.ACCENT,
            )

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
        self._row_images = []
        self._lookup = lookup
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
                image=self._row_image(game, lookup, str(len(self._games) - 1)),
                values=(
                    game.game_number,
                    game.event or "-",
                    {True: "Win", False: "Loss", None: "?"}[game.won],
                    "Play" if game.on_play else "Draw",
                    sum(f.level == "review" for f in findings),
                ),
            )

        self._show_spotlight()

        limited = [(g, f) for g, f in self._games if any(h in g.event for h in LIMITED_HINTS)]
        lines = patterns(limited, lookup) or ["No Limited games recorded yet."]
        self._set_text(self.txt_patterns, "\n".join(f"• {line}" for line in lines))

    def _row_image(self, game, lookup, iid=None):
        """Three overlapping key-card dots and a life-difference sparkline."""
        a = Theme.ARENA
        scale = Theme.current_scale
        w, h = int(180 * scale), int(24 * scale)
        img = Image.new("RGB", (w, h), Theme.BG_PRIMARY if Theme.BG_PRIMARY.startswith("#") else a["bg"])
        draw = ImageDraw.Draw(img)
        d = h - 4
        try:
            cards = key_cards(game, lookup)
        except Exception:
            cards = []
        refresh = (lambda: self._refresh_row_image(iid)) if iid is not None else None
        for i, card in enumerate(cards):
            x = 2 + i * (d - 6)
            draw.ellipse((x, 2, x + d, 2 + d), fill=art_color(card.get("colors")), outline=a["bg"], width=2)
            art = CardArt.pil(self, card, d - 4, d - 4, shape="circle", on_ready=refresh)
            if art is not None:
                img.paste(art, (x + 2, 4), art)
        left = int(70 * scale)
        draw.line((left, h / 2, w - 4, h / 2), fill=a["line_strong"])
        points = sparkline_points(life_diff_series(game), w - 4 - left, h - 2)
        if len(points) >= 4:
            pts = [(left + points[i], 1 + points[i + 1]) for i in range(0, len(points), 2)]
            draw.line(pts, fill=a["good"] if game.won else a["bad"], width=max(1, int(1.5 * scale)))
        photo = ImageTk.PhotoImage(img)
        self._row_images.append(photo)
        return photo

    def _refresh_row_image(self, iid):
        """Redraw one game's row once its key-card art has downloaded."""
        try:
            if not self.games_table.exists(iid):
                return
            game = self._games[int(iid)][0]
            self.games_table.item(iid, image=self._row_image(game, self._lookup, iid))
        except (tkinter.TclError, IndexError, ValueError):
            pass

    @staticmethod
    def _set_text(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _selected(self):
        selection = self.games_table.selection()
        return self._games[int(selection[0])] if selection else None

    def _show_spotlight(self):
        pick = spotlight_game(self._games)
        self._spotlight_index = pick[0] if pick else None
        if not pick:
            self.spotlight.pack_forget()
            return
        index, reason = pick
        game = self._games[index][0]
        iid = str(index)
        values = list(self.games_table.item(iid, "values"))
        values[-1] = f"★ {values[-1]}"
        self.games_table.item(iid, values=values, tags=("spotlight",))
        self.lbl_spotlight.config(text=f"★ Review this one: game {game.game_number} of {game.event}. {reason}")
        self.spotlight.pack(fill="x", pady=Theme.scaled_val((0, 4)), before=self.games_table)
        if not self.games_table.selection():
            self.games_table.selection_set(iid)
            self.games_table.see(iid)

    def _replay_spotlight(self):
        if self._spotlight_index is None:
            return
        self.games_table.selection_set(str(self._spotlight_index))
        self._open_replay()

    def _on_games_hover(self, event):
        if self.games_table.identify_region(event.x, event.y) != "heading":
            return HoverTip.hide()
        column = self.games_table.identify_column(event.x)
        name = "#0" if column == "#0" else self.games_table.column(column, "id")
        HoverTip.show(self, COLUMN_HELP.get(name), event.x_root, event.y_root)

    def _show_review(self, _event=None):
        selected = self._selected()
        if not selected:
            return
        game, findings = selected
        t = self.txt_review
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("end", "Hover a symbol or a finding to see what it means.   ", "legend")
        for level in ("review", "ok", "info"):
            t.insert("end", LEVEL_SYMBOLS[level], (f"sym_{level}", "review" if level == "review" else ""))
            t.insert("end", f" {dict(review='second look', ok='fine', info='context')[level]}   ", "legend")
        t.insert("end", f"\n\n{'Won' if game.won else 'Lost'} on the {'play' if game.on_play else 'draw'}, "
                        f"{game.mulligans} mulligan(s), {len(game.turns)} turns.\n\n")
        if not findings:
            t.insert("end", "Nothing flagged.")
        kinds = list(KIND_HELP)
        for f in findings:
            where = "Game" if f.turn == 0 else f"Turn {f.turn}"
            t.insert("end", LEVEL_SYMBOLS.get(f.level, "•"), (f"sym_{f.level}", "review" if f.level == "review" else ""))
            tags = (f"kind_{kinds.index(f.kind)}",) if f.kind in kinds else ()
            t.insert("end", f" {where}: {f.text}\n", tags + (("review",) if f.level == "review" else ()))
        t.configure(state="disabled")

    def _open_replay(self):
        selected = self._selected()
        if not selected:
            return
        from src.ui.windows.game_replay import GameReplayWindow

        game, findings = selected
        data = self.draft.set_data
        GameReplayWindow(
            self.winfo_toplevel(), game, findings, data.get_data_by_id, getattr(data, "_resolve_unknown_id", None)
        )

    def _copy_summary(self):
        selected = self._selected()
        if not selected:
            return
        game, findings = selected
        text = game_summary_text(game, self.draft.set_data.get_data_by_id, findings)
        self.clipboard_clear()
        self.clipboard_append(text)
