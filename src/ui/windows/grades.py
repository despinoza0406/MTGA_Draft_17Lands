"""
src/ui/windows/grades.py
Set grades: every card in the loaded set placed on a grade-by-color grid
(17lands / Blitz style), so the whole format reads at a glance.
"""

import tkinter
from tkinter import font as tkfont, ttk
from src import constants
from src.card_logic import format_win_rate
from src.ui.styles import Theme
from src.ui.components import art_color

COLUMNS = [("W", "White"), ("U", "Blue"), ("B", "Black"), ("R", "Red"), ("G", "Green"),
           ("M", "Multicolor"), ("C", "Colorless")]
RARITIES = [("All", None), ("Commons", "common"), ("Uncommons", "uncommon"),
            ("Rares", "rare"), ("Mythics", "mythic")]
GRADES = [g.strip() for g in constants.GRADE_DEVIATION_DICT] + ["F"]


def column_of(card):
    colors = [c for c in card.get("colors", []) if c in "WUBRG"]
    if not colors:
        return "C"
    return colors[0] if len(colors) == 1 else "M"


def grade_grid(cards, metrics, archetype="All Decks", rarity=None, min_games=200):
    """{grade: {column: [(name, wr, rarity, colors)]}} sorted best first."""
    grid, seen = {}, set()
    for card in cards:
        # The dataset lists every printing (alt arts, showcase) under its own id.
        if card.get("name") in seen or (rarity and card.get("rarity") != rarity):
            continue
        stats = card.get("deck_colors", {}).get(archetype, {})
        wr = stats.get("gihwr", 0.0)
        # Older dumps carry "gih" (games in hand); custom ones carry "samples".
        games = stats.get("gih", stats.get("samples"))
        if not wr or (games is not None and games < min_games):
            continue
        grade = format_win_rate(wr, archetype, "gihwr", metrics, constants.RESULT_FORMAT_GRADE)
        if grade not in GRADES:
            continue
        seen.add(card.get("name"))
        grid.setdefault(grade, {}).setdefault(column_of(card), []).append(
            (card.get("name", ""), wr, card.get("rarity", ""), card.get("colors", []))
        )
    for columns in grid.values():
        for entries in columns.values():
            entries.sort(key=lambda e: -e[1])
    return grid


class GradesWindow(tkinter.Toplevel):
    def __init__(self, parent, app_context, on_close):
        super().__init__(parent)
        self.app = app_context
        self.on_close = on_close
        self.title("Set Grades")
        self.geometry(f"{Theme.scaled_val(1180)}x{Theme.scaled_val(780)}")
        self.configure(bg=Theme.BG_PRIMARY)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.rarity = tkinter.StringVar(value="All")
        self.archetype = tkinter.StringVar(value="All Decks")
        self._build_ui()
        self.redraw()

    def _build_ui(self):
        a = Theme.ARENA
        pad = Theme.scaled_val(14)
        header = ttk.Frame(self)
        header.pack(fill="x", padx=pad, pady=(pad, 6))
        title = ttk.Frame(header)
        title.pack(side="left", fill="x", expand=True)
        self.lbl_context = ttk.Label(title, text="", font=Theme.scaled_font(8, "bold"), foreground=a["muted"])
        self.lbl_context.pack(anchor="w")
        ttk.Label(title, text="Set grades", font=Theme.display_font(20)).pack(anchor="w")

        controls = ttk.Frame(header)
        controls.pack(side="right")
        for label, _ in RARITIES:
            ttk.Radiobutton(
                controls, text=label.upper(), value=label, variable=self.rarity,
                bootstyle="toolbutton-outline", command=self.redraw,
            ).pack(side="left", padx=1)
        self.combo = ttk.Combobox(controls, textvariable=self.archetype, state="readonly", width=10)
        self.combo.pack(side="left", padx=(10, 0))
        self.combo.bind("<<ComboboxSelected>>", lambda e: self.redraw())

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=pad, pady=(0, pad))
        self.canvas = tkinter.Canvas(body, bg=a["bg"], highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))

    def _data(self):
        scanner = self.app.orchestrator.scanner
        cards = list(scanner.set_data.get_card_ratings().values())
        metrics = scanner.retrieve_set_metrics()
        return cards, metrics

    def redraw(self):
        if not self.winfo_exists():
            return
        a = Theme.ARENA
        c = self.canvas
        c.delete("all")
        cards, metrics = self._data()
        if cards and not self.combo["values"]:
            keys = list(cards[0].get("deck_colors", {}).keys())
            self.combo["values"] = [k for k in keys if k == "All Decks" or len(k) == 2]
        rarity = dict(RARITIES)[self.rarity.get()]
        grid = grade_grid(cards, metrics, self.archetype.get(), rarity)
        mean, _ = metrics.get_metrics(self.archetype.get(), "gihwr") if metrics else (0, 0)
        es, et = self.app.orchestrator.scanner.retrieve_current_limited_event()
        self.lbl_context.config(
            text=f"{es} · {et} · GAMES IN HAND WIN RATE · {self.archetype.get().upper()} AVERAGE {mean:.1f}%".upper()
        )
        if not grid:
            c.create_text(20, 30, text="No card data loaded for this set.", anchor="w", fill=a["muted"],
                          font=Theme.scaled_font(11))
            return

        width = max(c.winfo_width(), 700)
        label_w = Theme.scaled_val(64)
        col_w = (width - label_w) / len(COLUMNS)
        entry_h = Theme.scaled_val(40)
        head_h = Theme.scaled_val(34)
        name_font = Theme.scaled_font(9, "bold")
        meta_font = Theme.scaled_font(9)
        r = Theme.scaled_val(14)
        measure = tkfont.Font(font=name_font).measure

        for i, (sym, name) in enumerate(COLUMNS):
            x = label_w + i * col_w + 8
            pip = Theme.MANA.get(sym, "#D9C27A")
            c.create_oval(x, head_h / 2 - 8, x + 16, head_h / 2 + 8, fill=pip, width=0)
            c.create_text(x + 24, head_h / 2, text=name.upper(), anchor="w", fill=a["muted"], font=meta_font)
        c.create_line(0, head_h, width, head_h, fill=a["line_strong"])

        y = head_h
        for grade in GRADES:
            columns = grid.get(grade)
            if not columns:
                continue
            rows = max(len(v) for v in columns.values())
            h = rows * entry_h + 16
            tone = a["gold"] if grade.startswith("A") else a["good"] if grade.startswith("B") else (
                a["muted"] if grade.startswith("C") else a["bad"])
            c.create_rectangle(10, y + 10, 10 + 40, y + 50, outline=tone, width=2)
            c.create_text(30, y + 30, text=grade, fill=tone, font=Theme.display_font(15))
            for i, (sym, _) in enumerate(COLUMNS):
                x = label_w + i * col_w
                c.create_line(x, y, x, y + h, fill=a["line_soft"])
                for j, (card, wr, rar, colors) in enumerate(columns.get(sym, [])):
                    cy = y + 8 + j * entry_h + entry_h / 2
                    c.create_oval(x + 8, cy - r, x + 8 + 2 * r, cy + r, fill=art_color(colors), outline=a["control"])
                    tx = x + 16 + 2 * r
                    room = col_w - 2 * r - 22
                    label = card
                    if measure(label) > room:
                        while len(label) > 1 and measure(label + "…") > room:
                            label = label[:-1]
                        label = label.rstrip(" ,") + "…"
                    c.create_text(tx, cy - 7, text=label, anchor="w", fill=a["text"], font=name_font)
                    c.create_text(tx, cy + 9, text=f"{wr:.1f}% · {rar}", anchor="w", fill=a["muted"], font=meta_font)
            y += h
            c.create_line(0, y, width, y, fill=a["line_soft"])
        c.configure(scrollregion=(0, 0, width, y + 10))

    def close(self):
        self.destroy()
        self.on_close()
