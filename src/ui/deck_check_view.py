"""
src/ui/deck_check_view.py
The Deck Check panel shared by the Deck Builder and the Sealed Studio: a text
box listing what a deck does well, what it's short of, and sideboard swaps.
"""

import tkinter
from typing import Dict, List

from src.ui.styles import Theme
from src.ui.components import AutoScrollbar

EMPTY_TEXT = ("Build a deck (or send one here from Suggest Deck / Load Arena deck) "
              "and this tab checks it: what it does well, what it's short of, and "
              "which sideboard cards would make it better.")


def make_check_text(parent) -> tkinter.Text:
    """A read-only, styled text box filling parent's grid cell (0, 0)."""
    parent.rowconfigure(0, weight=1)
    parent.columnconfigure(0, weight=1)
    text = tkinter.Text(
        parent, wrap="word", relief="flat", borderwidth=0,
        bg=Theme.BG_PRIMARY, fg=Theme.TEXT_MAIN, font=Theme.scaled_font(10),
        padx=Theme.scaled_val(8), pady=Theme.scaled_val(6), cursor="arrow",
        highlightthickness=0,
    )
    scroll = AutoScrollbar(parent, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    text.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")
    tag = text.tag_configure
    tag("h", font=Theme.scaled_font(11, "bold"), spacing1=Theme.scaled_val(10), spacing3=Theme.scaled_val(4))
    tag("big", font=Theme.scaled_font(13, "bold"), spacing3=Theme.scaled_val(4))
    tag("good", foreground=Theme.SUCCESS, font=Theme.scaled_font(10, "bold"))
    tag("fix", foreground=Theme.WARNING, font=Theme.scaled_font(10, "bold"))
    tag("info", foreground=Theme.TEXT_MUTED, font=Theme.scaled_font(10, "bold"))
    tag("muted", foreground=Theme.TEXT_MUTED)
    tag("line", spacing3=Theme.scaled_val(5), lmargin2=Theme.scaled_val(22))
    text.configure(state="disabled")
    return text


def render_deck_check(text: tkinter.Text, deck: List[Dict], sideboard: List[Dict], metrics,
                      set_code: str = "", event: str = "", empty_text: str = EMPTY_TEXT) -> None:
    from src.advisor.deck_check import check_deck
    from src.advisor.deck_model import load_deck_model

    text.configure(state="normal")
    text.delete("1.0", "end")
    if not deck:
        text.insert("end", empty_text, "muted")
        text.configure(state="disabled")
        return
    sets = [c.get("set") for c in deck + sideboard if c.get("set")]
    set_code = set_code or (max(set(sets), key=sets.count) if sets else "")
    model = load_deck_model(set_code, event) if set_code else None
    result = check_deck(deck, sideboard, metrics, model, is_bo3="Trad" in str(event))

    colors = "".join(result.colors) + (f" + {''.join(result.splash)}" if result.splash else "")
    line = f"{colors or 'Colorless'} · Est. {result.estimate}" if result.estimate else colors
    text.insert("end", line + "\n", "big")
    if result.model_estimate:
        spells = sum(c.get("count", 1) for c in deck if "Land" not in c.get("types", []))
        text.insert("end", f"🤖 Model build in these colors: Est. {result.model_estimate} · "
                           f"{result.model_same} of your {spells} spells are the same.\n", "muted")

    groups = (("good", "✔ What's working"), ("fix", "⚠ What to improve"), ("info", "ℹ Worth knowing"))
    icons = {"good": "✔", "fix": "⚠", "info": "ℹ"}
    for level, title in groups:
        notes = [n for n in result.notes if n.level == level]
        if not notes:
            continue
        text.insert("end", title + "\n", "h")
        for n in notes:
            text.insert("end", f"{icons[level]} {n.title}: ", level)
            text.insert("end", n.text + "\n", "line")
    if result.swaps:
        text.insert("end", "⇄ Swaps to consider\n", "h")
        for sw in result.swaps:
            text.insert("end", f"⇄ {sw.out} → {sw.into}: ", "fix")
            text.insert("end", sw.reason + "\n", "line")
    elif result.notes:
        text.insert("end", "\nNo sideboard card clearly beats one you play.\n", "muted")
    text.configure(state="disabled")
