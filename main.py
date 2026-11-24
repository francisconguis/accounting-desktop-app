"""Application entry point for the Local-First Double-Entry Accounting Desktop App."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def _apply_light_palette(app: QApplication) -> None:
    """Force a crisp black-on-white light palette even when Windows is in Dark Mode."""
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor("#f0f4f8"))
    pal.setColor(QPalette.ColorRole.WindowText, QColor("#111827"))
    pal.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#f7fafc"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#111827"))
    pal.setColor(QPalette.ColorRole.Text, QColor("#111827"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#6b7280"))
    pal.setColor(QPalette.ColorRole.Button, QColor("#e2e8f0"))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor("#111827"))
    pal.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.Highlight, QColor("#2b6cb0"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(pal)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("QuickBooks Desktop Clone")
    app.setOrganizationName("LocalAccounting")
    _apply_light_palette(app)

    db_path = Path(__file__).resolve().parent / "company_file.db"
    if len(sys.argv) > 1:
        db_path = Path(sys.argv[1])

    window = MainWindow(db_path=db_path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
