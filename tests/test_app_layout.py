import pytest
import tkinter
from unittest.mock import MagicMock, patch
from src.ui.app_layout import AppLayoutManager
from src.configuration import Configuration
from src.ui.styles import Theme


class TestAppLayoutManager:
    @pytest.fixture
    def root(self):
        root = tkinter.Tk()
        Theme.apply(root, "Dark")
        yield root
        root.destroy()

    @pytest.fixture
    def mock_app(self, root):
        app = MagicMock()
        app.root = root
        app.configuration = Configuration()

        # Mock sub-components
        app.interactions = MagicMock()
        app.orchestrator = MagicMock()
        return app

    def test_build_layout_initialization(self, mock_app):
        """Verify the layout manager constructs the core UI shell without crashing."""
        layout = AppLayoutManager(mock_app)
        layout.build()

        assert layout.main_container is not None
        assert layout.nav_rail is not None
        assert layout.notebook is not None
        assert layout.dashboard is not None

        # The Draft page plus the seven feature panels, all full-size pages
        assert len(layout.notebook.tabs()) == 8
        assert layout.notebook.tabs()[0] == str(layout.dashboard_page)
        assert "History" in layout.notebook.tab(layout.notebook.tabs()[-1], "text")

    def test_nav_rail_switches_pages(self, mock_app):
        """Each rail button shows its page and is marked active."""
        layout = AppLayoutManager(mock_app)
        layout.build()

        history = str(layout.panel_history)
        layout.nav_buttons[history].invoke()
        layout.notebook.update()
        assert layout.notebook.select() == history
        assert layout.nav_buttons[history].cget("style") == "NavActive.TButton"
        assert layout.nav_buttons[str(layout.dashboard_page)].cget("style") == "Nav.TButton"

    def test_tab_helpers_are_harmless(self, mock_app):
        """Old callers still work: tabs are always visible in the page layout."""
        layout = AppLayoutManager(mock_app)
        layout.build()
        layout.toggle_tabs()
        layout.ensure_tabs_visible()
        assert layout.tabs_visible is True

    @patch("src.ui.app_layout.Theme.scaled_val", side_effect=lambda x: x)
    def test_save_and_restore_window_state(self, mock_scale, mock_app):
        """Wide geometries are kept; narrow ones from the old layout are widened."""
        layout = AppLayoutManager(mock_app)
        layout.build()

        mock_app.root.state = MagicMock(return_value="normal")
        mock_app.root.geometry = MagicMock(return_value="1400x900+10+10")
        layout.save_window_state()
        assert mock_app.configuration.settings.main_window_geometry == "1400x900+10+10"

        mock_app.root.geometry = MagicMock()
        with patch.object(mock_app.root, "after"):
            layout.restore_window_state()
        mock_app.root.geometry.assert_called_with("1400x900+10+10")

        mock_app.configuration.settings.main_window_geometry = "600x1041+52+52"
        mock_app.root.geometry = MagicMock()
        with patch.object(mock_app.root, "after"):
            layout.restore_window_state()
        mock_app.root.geometry.assert_called_with("1280x860+52+52")

    def test_session_info_updates_cleanly(self, mock_app):
        """Verify the bottom metadata text updates correctly and handles None."""
        layout = AppLayoutManager(mock_app)
        layout.build()

        layout.update_session_info("PremierDraft_OTJ", "uuid-1234", "10:00:00")
        text = layout.lbl_session_info.cget("text")

        assert "PremierDraft_OTJ" in text
        assert "uuid-1234" in text
        assert "10:00:00" in text

        # Test handling of missing data
        layout.update_session_info(None, None, None)
        assert layout.lbl_session_info.cget("text") == ""
