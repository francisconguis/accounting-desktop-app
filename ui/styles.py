"""Classic QuickBooks Desktop-inspired PySide6 stylesheet and UI helpers."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QTableWidget, QTableWidgetItem, QWidget

APP_STYLESHEET = """
QMainWindow, QDialog, QMessageBox {
    background-color: #f0f4f8;
    color: #111827;
}
QWidget {
    color: #111827;
}
QLabel {
    color: #111827;
}
QStatusBar {
    background-color: #e2e8f0;
    color: #111827;
    border-top: 1px solid #cbd5e0;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTextEdit {
    background-color: #ffffff;
    color: #111827;
    border: 1px solid #cbd5e0;
    border-radius: 4px;
    padding: 4px 6px;
    selection-background-color: #2b6cb0;
    selection-color: #ffffff;
}
QLineEdit:read-only {
    background-color: #f1f5f9;
    color: #334155;
}
QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #111827;
    selection-background-color: #ebf4ff;
    selection-color: #1a365d;
    border: 1px solid #cbd5e0;
}
QPushButton {
    background-color: #e2e8f0;
    color: #111827;
    border: 1px solid #cbd5e0;
    border-radius: 4px;
    padding: 6px 12px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #cbd5e0;
}
QPushButton:disabled {
    background-color: #edf2f7;
    color: #94a3b8;
}
QCheckBox {
    color: #111827;
}
QMenuBar {
    background-color: #1b365d;
    color: #ffffff;
    padding: 3px 6px;
    font-size: 13px;
}
QMenuBar::item {
    color: #ffffff;
    background: transparent;
}
QMenuBar::item:selected {
    background-color: #2c5282;
}
QMenu {
    background-color: #ffffff;
    color: #111827;
    border: 1px solid #cbd5e0;
    font-size: 13px;
}
QMenu::item {
    color: #111827;
    padding: 5px 20px;
}
QMenu::item:selected {
    background-color: #ebf4ff;
    color: #1b365d;
}
QWidget#LeftNavBar {
    background-color: #1e3a5f;
    border-right: 1px solid #142842;
}
QPushButton[class="NavButton"] {
    background-color: transparent;
    color: #e2e8f0;
    text-align: left;
    padding: 10px 14px;
    border: none;
    border-radius: 4px;
    font-size: 13px;
    font-weight: 500;
}
QPushButton[class="NavButton"]:hover {
    background-color: #2b4c7e;
    color: #ffffff;
}
QPushButton[class="PrimaryButton"] {
    background-color: #2b6cb0;
    color: #ffffff;
    border: none;
    border-radius: 4px;
    padding: 7px 14px;
    font-weight: 600;
}
QPushButton[class="PrimaryButton"]:hover {
    background-color: #2c5282;
}
QPushButton[class="PrimaryButton"]:disabled {
    background-color: #a0aec0;
    color: #edf2f7;
}
QPushButton[class="SuccessButton"] {
    background-color: #2f855a;
    color: #ffffff;
    border: none;
    border-radius: 4px;
    padding: 7px 14px;
    font-weight: 600;
}
QPushButton[class="SuccessButton"]:hover {
    background-color: #276749;
}
QPushButton[class="FlowNode"] {
    background-color: #ffffff;
    color: #1a365d;
    border: 2px solid #90cdf4;
    border-radius: 8px;
    padding: 12px 14px;
    font-weight: 600;
    font-size: 12px;
    min-width: 125px;
    min-height: 54px;
}
QPushButton[class="FlowNode"]:hover {
    background-color: #ebf8ff;
    border-color: #2b6cb0;
}
QGroupBox {
    font-weight: 600;
    color: #1a365d;
    border: 1px solid #cbd5e0;
    border-radius: 6px;
    margin-top: 10px;
    padding: 12px 10px 10px 10px;
    background-color: #ffffff;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
}
QTabWidget::pane {
    border: 1px solid #cbd5e0;
    background-color: #f7fafc;
}
QTabBar::tab {
    background-color: #e2e8f0;
    color: #2d3748;
    padding: 7px 14px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    font-size: 12px;
}
QTabBar::tab:selected {
    background-color: #ffffff;
    color: #1a365d;
    font-weight: 600;
}
QTableWidget, QTreeWidget {
    background-color: #ffffff;
    alternate-background-color: #f7fafc;
    color: #111827;
    gridline-color: #e2e8f0;
    border: 1px solid #cbd5e0;
    font-size: 12px;
    selection-background-color: #dbeafe;
    selection-color: #111827;
}
QTableWidget::item, QTreeWidget::item {
    color: #111827;
}
QHeaderView::section {
    background-color: #edf2f7;
    color: #1a365d;
    font-weight: 600;
    padding: 5px 8px;
    border: 1px solid #cbd5e0;
}
"""


def show_error(parent: QWidget, title: str, message: str) -> None:
    QMessageBox.critical(parent, title, message)


def show_info(parent: QWidget, title: str, message: str) -> None:
    QMessageBox.information(parent, title, message)


def make_readonly_item(text: str, align_right: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if align_right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item


def style_table_defaults(table: QTableWidget) -> None:
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(True)
