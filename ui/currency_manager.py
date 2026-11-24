"""Currency List & Exchange Rate Manager (Lists -> Currency List)."""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from accounting.engine import AccountingEngine
from accounting.money import format_rate, parse_rate_to_micros
from ui.styles import make_readonly_item, show_error, style_table_defaults


class CurrencyDialog(QDialog):
    def __init__(self, engine: AccountingEngine, existing: dict | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.existing = existing
        self.setWindowTitle("Update Exchange Rate" if existing else "Add Currency")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        home_ccy = engine.get_home_currency()

        self.code_edit = QLineEdit()
        self.name_edit = QLineEdit()
        self.symbol_edit = QLineEdit()
        self.rate_edit = QLineEdit("1.000000")

        if existing:
            self.code_edit.setText(existing["code"])
            self.code_edit.setReadOnly(True)
            self.name_edit.setText(existing["name"])
            self.symbol_edit.setText(existing["symbol"])
            self.rate_edit.setText(format_rate(existing["exchange_rate_micros"]))
            if existing["code"] == home_ccy:
                self.rate_edit.setReadOnly(True)

        form.addRow("Currency Code (e.g. EUR):", self.code_edit)
        form.addRow("Currency Name:", self.name_edit)
        form.addRow("Symbol (e.g. €):", self.symbol_edit)
        form.addRow(f"Rate to 1 {home_ccy}:", self.rate_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        try:
            micros = parse_rate_to_micros(self.rate_edit.text())
            self.engine.upsert_currency(
                code=self.code_edit.text(),
                name=self.name_edit.text(),
                symbol=self.symbol_edit.text(),
                exchange_rate_micros=micros,
            )
            self.accept()
        except Exception as exc:
            show_error(self, "Currency Error", str(exc))


class CurrencyManagerView(QWidget):
    """Manage currencies and their exchange rates against the Home Currency."""

    def __init__(
        self,
        engine: AccountingEngine,
        on_data_changed: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.on_data_changed = on_data_changed
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.title_lbl = QLabel("Currency List & Exchange Rates")
        self.title_lbl.setStyleSheet("font-size: 16px; font-weight: 700; color: #1a365d;")
        top.addWidget(self.title_lbl)
        top.addStretch()

        add_btn = QPushButton("+ Add Currency")
        add_btn.setProperty("class", "PrimaryButton")
        add_btn.clicked.connect(self._add_currency)
        top.addWidget(add_btn)

        edit_btn = QPushButton("Update Selected Rate")
        edit_btn.clicked.connect(self._edit_selected)
        top.addWidget(edit_btn)

        layout.addLayout(top)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Code", "Name", "Symbol", "Exchange Rate (to Home)", "Last Updated"])
        style_table_defaults(self.table)
        self.table.cellDoubleClicked.connect(lambda _r, _c: self._edit_selected())
        layout.addWidget(self.table)

    def refresh(self) -> None:
        home = self.engine.get_home_currency()
        self.title_lbl.setText(f"Currency List & Exchange Rates (Home Currency: {home})")
        self._rows = self.engine.list_currencies()
        self.table.setRowCount(len(self._rows))
        for idx, c in enumerate(self._rows):
            code_label = f"{c['code']} (HOME)" if c["code"] == home else c["code"]
            self.table.setItem(idx, 0, make_readonly_item(code_label))
            self.table.setItem(idx, 1, make_readonly_item(c["name"]))
            self.table.setItem(idx, 2, make_readonly_item(c["symbol"]))
            self.table.setItem(idx, 3, make_readonly_item(format_rate(c["exchange_rate_micros"]), align_right=True))
            self.table.setItem(idx, 4, make_readonly_item(c["updated_at"]))
        self.table.resizeColumnsToContents()

    def _add_currency(self) -> None:
        dlg = CurrencyDialog(self.engine, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.on_data_changed()

    def _edit_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            return
        dlg = CurrencyDialog(self.engine, existing=self._rows[row], parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.on_data_changed()
