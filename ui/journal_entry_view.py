"""General Journal Entry Form with Strict Zero-Sum Home-Currency Save Guard."""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

from PySide6.QtWidgets import (
    QComboBox,
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
from accounting.models import JournalLineInput
from accounting.money import (
    convert_to_home,
    format_money,
    format_rate,
    parse_money_to_cents,
    parse_rate_to_micros,
)
from ui.styles import make_readonly_item, show_error, show_info, style_table_defaults


class JournalEntryView(QWidget):
    """Multi-row General Journal Entry form. Save is disabled unless Home Debits == Home Credits & Total > 0."""

    def __init__(
        self,
        engine: AccountingEngine,
        on_data_changed: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.on_data_changed = on_data_changed
        self._accounts: list[dict[str, Any]] = []
        self._entities: list[tuple[str, str, int | None]] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        hdr = QLabel("Make General Journal Entries")
        hdr.setStyleSheet("font-size: 16px; font-weight: 700; color: #1a365d;")
        layout.addWidget(hdr)

        top = QHBoxLayout()
        f1 = QFormLayout()
        f2 = QFormLayout()

        self.date_edit = QLineEdit(date.today().isoformat())
        self.ref_edit = QLineEdit()
        self.ref_edit.setPlaceholderText("(Auto-generated if blank)")
        self.ccy_combo = QComboBox()
        self.ccy_combo.currentIndexChanged.connect(self._on_ccy_changed)
        self.rate_edit = QLineEdit("1.000000")
        self.rate_edit.textChanged.connect(self._recalc)
        self.memo_edit = QLineEdit()

        f1.addRow("Entry Date:", self.date_edit)
        f1.addRow("Reference #:", self.ref_edit)
        f1.addRow("Memo:", self.memo_edit)
        f2.addRow("Currency:", self.ccy_combo)
        f2.addRow("Exchange Rate (to Home):", self.rate_edit)

        top.addLayout(f1)
        top.addLayout(f2)
        layout.addLayout(top)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Account", "Debit (Foreign)", "Credit (Foreign)", "Debit (Home)", "Credit (Home)", "Name (Customer/Vendor)"]
        )
        style_table_defaults(self.table)
        layout.addWidget(self.table)

        btns = QHBoxLayout()
        add_btn = QPushButton("+ Add Row")
        add_btn.clicked.connect(self._add_row)
        rem_btn = QPushButton("Remove Row")
        rem_btn.clicked.connect(self._remove_row)
        btns.addWidget(add_btn)
        btns.addWidget(rem_btn)
        btns.addStretch()

        self.balance_lbl = QLabel("Home Debits: $0.00  |  Home Credits: $0.00  |  Out of Balance: $0.00")
        self.balance_lbl.setStyleSheet("font-size: 13px; font-weight: 700;")
        btns.addWidget(self.balance_lbl)
        layout.addLayout(btns)

        footer = QHBoxLayout()
        footer.addStretch()
        self.save_btn = QPushButton("Save & Post Journal Entry")
        self.save_btn.setProperty("class", "PrimaryButton")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save)
        footer.addWidget(self.save_btn)
        layout.addLayout(footer)

    def refresh(self) -> None:
        self._accounts = self.engine.list_accounts()
        self._entities = [("(None)", "None", None)]
        for c in self.engine.list_customers():
            self._entities.append((f"Customer: {c['name']}", "Customer", c["id"]))
        for v in self.engine.list_vendors():
            self._entities.append((f"Vendor: {v['name']}", "Vendor", v["id"]))

        self.ccy_combo.blockSignals(True)
        self.ccy_combo.clear()
        for c in self.engine.list_currencies():
            self.ccy_combo.addItem(f"{c['code']} - {c['name']}", c["code"])
        idx = self.ccy_combo.findData(self.engine.get_home_currency())
        if idx >= 0:
            self.ccy_combo.setCurrentIndex(idx)
        self.ccy_combo.blockSignals(False)
        self._on_ccy_changed()

        if self.table.rowCount() < 2:
            self.table.setRowCount(0)
            self._add_row()
            self._add_row()

    def _on_ccy_changed(self) -> None:
        ccy = self.ccy_combo.currentData()
        if not ccy:
            return
        rate = self.engine.get_exchange_rate(ccy)
        self.rate_edit.setText(format_rate(rate))
        self.rate_edit.setReadOnly(ccy == self.engine.get_home_currency())
        self._recalc()

    def _add_row(self) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)

        acct_combo = QComboBox()
        for a in self._accounts:
            acct_combo.addItem(f"{a['account_number']} {a['name']} ({a['account_type']})", a["id"])

        debit_edit = QLineEdit("0.00")
        credit_edit = QLineEdit("0.00")
        debit_edit.textChanged.connect(self._recalc)
        credit_edit.textChanged.connect(self._recalc)

        ent_combo = QComboBox()
        for label, etype, eid in self._entities:
            ent_combo.addItem(label, (etype, eid))

        self.table.setCellWidget(r, 0, acct_combo)
        self.table.setCellWidget(r, 1, debit_edit)
        self.table.setCellWidget(r, 2, credit_edit)
        self.table.setItem(r, 3, make_readonly_item("$0.00", align_right=True))
        self.table.setItem(r, 4, make_readonly_item("$0.00", align_right=True))
        self.table.setCellWidget(r, 5, ent_combo)
        self.table.resizeColumnsToContents()

    def _remove_row(self) -> None:
        r = self.table.currentRow()
        if r >= 0 and self.table.rowCount() > 2:
            self.table.removeRow(r)
            self._recalc()

    def _recalc(self) -> None:
        home = self.engine.get_home_currency()
        try:
            rate = parse_rate_to_micros(self.rate_edit.text())
        except Exception:
            self.save_btn.setEnabled(False)
            return

        tot_dh = 0
        tot_ch = 0
        valid_lines = 0
        has_conflict = False

        for r in range(self.table.rowCount()):
            dw = self.table.cellWidget(r, 1)
            cw = self.table.cellWidget(r, 2)
            if not isinstance(dw, QLineEdit) or not isinstance(cw, QLineEdit):
                continue
            try:
                df = parse_money_to_cents(dw.text())
                cf = parse_money_to_cents(cw.text())
            except Exception:
                self.save_btn.setEnabled(False)
                return
            if df < 0 or cf < 0 or (df > 0 and cf > 0):
                has_conflict = True
            dh = convert_to_home(df, rate)
            ch = convert_to_home(cf, rate)
            tot_dh += dh
            tot_ch += ch
            if dh > 0 or ch > 0:
                valid_lines += 1
            self.table.setItem(r, 3, make_readonly_item(format_money(dh, home), align_right=True))
            self.table.setItem(r, 4, make_readonly_item(format_money(ch, home), align_right=True))

        diff = tot_dh - tot_ch
        balanced = (not has_conflict) and (valid_lines >= 2) and (tot_dh > 0) and (tot_dh == tot_ch)
        self.balance_lbl.setText(
            f"Home Debits: {format_money(tot_dh, home)}  |  "
            f"Home Credits: {format_money(tot_ch, home)}  |  "
            f"Diff: {format_money(diff, home)}"
        )
        self.balance_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {'#2f855a' if balanced else '#c53030'};"
        )
        self.save_btn.setEnabled(balanced)

    def _save(self) -> None:
        try:
            ccy = self.ccy_combo.currentData()
            rate = parse_rate_to_micros(self.rate_edit.text())
            lines: list[JournalLineInput] = []

            for r in range(self.table.rowCount()):
                aw = self.table.cellWidget(r, 0)
                dw = self.table.cellWidget(r, 1)
                cw = self.table.cellWidget(r, 2)
                ew = self.table.cellWidget(r, 5)
                if not isinstance(aw, QComboBox) or not isinstance(dw, QLineEdit) or not isinstance(cw, QLineEdit):
                    continue
                df = parse_money_to_cents(dw.text())
                cf = parse_money_to_cents(cw.text())
                if df == 0 and cf == 0:
                    continue
                dh = convert_to_home(df, rate)
                ch = convert_to_home(cf, rate)
                etype, eid = ew.currentData() if isinstance(ew, QComboBox) else ("None", None)
                lines.append(
                    JournalLineInput(
                        account_id=aw.currentData(),
                        debit_foreign=df,
                        credit_foreign=cf,
                        debit_home=dh,
                        credit_home=ch,
                        entity_type=etype,
                        entity_id=eid,
                    )
                )

            ref = self.ref_edit.text().strip() or None
            txn_id = self.engine.create_manual_journal_entry(
                entry_date=self.date_edit.text().strip(),
                lines=lines,
                currency_code=ccy,
                exchange_rate_micros=rate,
                memo=self.memo_edit.text().strip(),
                reference_no=ref,
            )
            show_info(self, "Journal Entry Posted", f"Balanced Journal Entry #{txn_id} posted.")
            self.ref_edit.clear()
            self.memo_edit.clear()
            self.table.setRowCount(0)
            self._add_row()
            self._add_row()
            self._recalc()
            self.on_data_changed()
        except Exception as exc:
            show_error(self, "Journal Entry Error", str(exc))
