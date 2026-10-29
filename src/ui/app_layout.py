"""
src/ui/app_layout.py
Responsible exclusively for the UI construction, layout management, and window geometry.
Extracts all Tkinter widget boilerplate out of the main application context.
"""

import tkinter
from tkinter import ttk
import logging

from src.ui.styles import Theme
from src.ui.top_bar import TopBarControls
from src.ui.dashboard import DashboardFrame

# Main Tab Panels
from src.ui.windows.taken_cards import TakenCardsPanel
from src.ui.windows.suggest_deck import SuggestDeckPanel
from src.ui.windows.custom_deck import CustomDeckPanel
from src.ui.windows.compare import ComparePanel
from src.ui.windows.download import DownloadWindow
from src.ui.windows.tier_list_panel import TierListWindow
from src.ui.windows.match_history_panel import MatchHistoryPanel

logger = logging.getLogger(__name__)


class AppLayoutManager:
    """Builds and manages the main application visual layout and tab architecture."""

    def __init__(self, app_context):
        self.app = app_context
        self.root = app_context.root
        self.config = app_context.configuration
        self.tabs_visible = True

        # UI Elements registry
        self.main_container = None
        self.top_bar = None
        self.nav_rail = None
        self.nav_buttons = {}
        self.dashboard_page = None
        self.dashboard = None
        self.tab_controls = None
        self.lbl_session_info = None
        self.notebook = None

        # Panels
        self.panel_taken = None
        self.panel_suggest = None
        self.panel_custom = None
        self.panel_compare = None
        self.panel_data = None
        self.panel_tiers = None

    # Pages reachable from the left rail: (label, icon, panel attribute).
    # "dashboard_page" is the live draft view; the rest are the old tabs.
    NAV_PAGES = [
        ("Draft", "▦", "dashboard_page"),
        ("Pool", "▤", "panel_taken"),
        ("Deck", "☰", "panel_suggest"),
        ("Custom", "✎", "panel_custom"),
        ("Compare", "⇄", "panel_compare"),
        ("Tiers", "★", "panel_tiers"),
        ("History", "▮", "panel_history"),
    ]

    def build(self):
        """Left navigation rail plus one full-size page at a time.

        There are no splitters between the draft view and the other panels any
        more: each page takes the whole window, so nothing needs resizing.
        """
        if self.main_container:
            self.main_container.destroy()

        self.main_container = ttk.Frame(self.root)
        self.main_container.pack(fill="both", expand=True)
        self.main_container.columnconfigure(1, weight=1)
        self.main_container.rowconfigure(0, weight=1)

        self.nav_rail = ttk.Frame(self.main_container, style="Nav.TFrame")
        self.nav_rail.grid(row=0, column=0, sticky="ns")
        ttk.Separator(self.main_container, orient="vertical").grid(row=0, column=0, sticky="nse")

        content = ttk.Frame(self.main_container, padding=Theme.scaled_val((10, 8, 10, 8)))
        content.grid(row=0, column=1, sticky="nsew")

        self.top_bar = TopBarControls(content, self.app)
        self.top_bar.pack(fill="x", pady=(0, Theme.scaled_val(8)))

        self.notebook = ttk.Notebook(content)
        self.notebook.pack(fill="both", expand=True)

        self.dashboard_page = ttk.Frame(self.notebook)
        self.dashboard = DashboardFrame(
            self.dashboard_page,
            self.config,
            self.app.interactions.on_card_select,
            self.app._refresh_ui_data,
            on_advisor_click=self.app.interactions.show_tooltip_from_advisor,
            on_context_menu=self.app.interactions.on_card_context_menu,
        )
        self.tab_controls = ttk.Frame(self.dashboard_page, padding=Theme.scaled_val((4, 4, 4, 0)))
        self.tab_controls.pack(side="bottom", fill="x")
        self.lbl_session_info = ttk.Label(
            self.tab_controls,
            font=Theme.scaled_font(8),
            bootstyle="secondary",
            anchor="w",
        )
        self.lbl_session_info.pack(side="left", fill="x", expand=True)
        self.dashboard.pack(side="top", fill="both", expand=True)

        self._build_panels()
        self._build_nav()
        self._style_pages()
        Theme.bind_theme_change(self.main_container, lambda e: self._style_pages())

    def _style_pages(self):
        """Hide the notebook's own tabs (the rail replaces them) and paint the rail."""
        style = ttk.Style()
        style.layout("Pages.TNotebook.Tab", [])
        bg = Theme.BG_PRIMARY
        style.configure(
            "Pages.TNotebook", borderwidth=0, tabmargins=0, padding=0,
            background=bg, bordercolor=bg, lightcolor=bg, darkcolor=bg,
        )
        # ttkbootstrap rewrites a style= keyword to its own TNotebook style, so
        # set it straight on the Tk widget.
        self.root.tk.call(self.notebook._w, "configure", "-style", "Pages.TNotebook")
        hover = Theme.ARENA["raised"] if Theme.is_arena() else Theme.BG_SECONDARY
        style.configure("Nav.TFrame", background=bg)
        for name, fg, back in (
            ("Nav.TButton", Theme.TEXT_MUTED, bg),
            ("NavActive.TButton", Theme.TEXT_MAIN, hover),
        ):
            style.configure(
                name, background=back, foreground=fg, borderwidth=0, relief="flat",
                bordercolor=back, lightcolor=back, darkcolor=back, focuscolor=back,
                justify="center", padding=Theme.scaled_val((4, 8)),
                font=Theme.scaled_font(8, "bold"),
            )
            style.map(name, background=[("active", hover)], foreground=[("active", Theme.TEXT_MAIN)])
        if self.nav_buttons:
            self._sync_nav()

    def _build_nav(self):
        ttk.Label(
            self.nav_rail, text="17L", font=Theme.display_font(15),
            foreground=Theme.ARENA["gold"] if Theme.is_arena() else Theme.ACCENT,
        ).pack(pady=Theme.scaled_val((12, 10)))
        self.nav_buttons = {}
        for label, icon, attr in self.NAV_PAGES:
            page = getattr(self, attr)
            btn = self._nav_button(f"{icon}\n{label.upper()}", lambda p=page: self.notebook.select(p))
            self.nav_buttons[str(page)] = btn
        ttk.Frame(self.nav_rail, style="Nav.TFrame").pack(fill="both", expand=True)
        self._nav_button("▩\nGRADES", self.app._open_grades)
        self._nav_button("◎\nTRACKER", self.app._open_match_tracker)
        data_btn = self._nav_button("⚙\nDATA", lambda: self.notebook.select(self.panel_data))
        self.nav_buttons[str(self.panel_data)] = data_btn
        ttk.Frame(self.nav_rail, style="Nav.TFrame", height=Theme.scaled_val(8)).pack()
        self.notebook.bind("<<NotebookTabChanged>>", lambda e: self._sync_nav(), add="+")
        self._sync_nav()

    def _nav_button(self, text, command):
        btn = ttk.Button(
            self.nav_rail, text=text, width=9,
            takefocus=False, cursor="hand2", command=command,
        )
        btn.configure(style="Nav.TButton")  # a style= keyword is rewritten by ttkbootstrap
        btn.pack(fill="x")
        return btn

    def _sync_nav(self):
        try:
            current = self.notebook.select()
        except tkinter.TclError:
            return
        for page, btn in self.nav_buttons.items():
            btn.configure(style="NavActive.TButton" if page == current else "Nav.TButton")

    def _build_panels(self):
        """Constructs and injects the individual feature panels into the notebook."""
        self.panel_taken = TakenCardsPanel(
            self.notebook, self.app.orchestrator.scanner, self.config
        )
        self.panel_custom = CustomDeckPanel(
            self.notebook, self.app.orchestrator.scanner, self.config, self.app
        )
        self.panel_suggest = SuggestDeckPanel(
            self.notebook,
            self.app.orchestrator.scanner,
            self.config,
            on_export_custom=lambda deck, sb: [
                self.panel_custom.import_deck(deck, sb),
                self.notebook.select(self.panel_custom),
            ],
            app_context=self.app,
        )
        self.panel_compare = ComparePanel(
            self.notebook, self.app.orchestrator.scanner, self.config
        )
        self.panel_data = DownloadWindow(
            self.notebook,
            self.app.orchestrator.scanner.set_list,
            self.config,
            self.app._on_dataset_update,
        )
        self.panel_tiers = TierListWindow(
            self.notebook, self.config, self.app._refresh_ui_data
        )
        self.panel_history = MatchHistoryPanel(
            self.notebook, self.app.orchestrator.scanner, self.config
        )

        self.notebook.add(self.dashboard_page, text=" Draft ")
        self.notebook.add(self.panel_data, text=" Datasets ")
        self.notebook.add(self.panel_taken, text=" Card Pool ")
        self.notebook.add(self.panel_suggest, text=" Deck Builder ")
        self.notebook.add(self.panel_custom, text=" Custom Deck ")
        self.notebook.add(self.panel_compare, text=" Comparisons ")
        self.notebook.add(self.panel_tiers, text=" Tier Lists ")
        self.notebook.add(self.panel_history, text=" History ")

        # Safely trigger dataset UI refreshes if the panel supports it
        self.notebook.bind(
            "<<NotebookTabChanged>>",
            lambda e: (
                self.panel_data.refresh()
                if hasattr(self.panel_data, "refresh")
                and "Datasets" in self.notebook.tab(self.notebook.select(), "text")
                else (
                    self.panel_history.refresh()
                    if "History" in self.notebook.tab(self.notebook.select(), "text")
                    else None
                )
            ),
        )

    def toggle_tabs(self):
        """Kept for callers of the old split layout; every page is full size now."""

    def ensure_tabs_visible(self):
        pass

    def update_session_info(self, event_name, draft_id, start_time):
        """Displays technical metadata silently in the footer."""
        if not self.lbl_session_info:
            return
        parts = [str(p) for p in [event_name, draft_id, start_time] if p]
        self.lbl_session_info.config(text=" | ".join(parts))

    def restore_window_state(self):
        """Applies user's saved window dimensions and layouts on startup."""
        try:
            geom = self.config.settings.main_window_geometry
            width = int(geom.split("x")[0]) if geom and "x" in geom else 0
            if width >= Theme.scaled_val(1000):
                self.root.geometry(geom)
            elif geom and "+" in geom and width > 1:
                # Saved from the old narrow stacked layout: widen, keep position.
                pos = geom[geom.index("+"):]
                self.root.geometry(f"{Theme.scaled_val(1280)}x{Theme.scaled_val(860)}{pos}")
            else:
                self.root.geometry(f"{Theme.scaled_val(1200)}x{Theme.scaled_val(800)}")

            self.root.update_idletasks()

            self.root.after(100, self.dashboard.auto_fit_sidebar)

        except Exception as e:
            logger.warning(f"Failed to apply window preferences: {e}")

    def save_window_state(self):
        """Captures window sizes and sash positions prior to closing."""
        if self.root.state() == "normal":
            self.config.settings.main_window_geometry = self.root.geometry()

        # Sash positions are no longer saved: the layout sizes itself.
