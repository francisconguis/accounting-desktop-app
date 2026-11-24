"""Vendor Center (Split-Pane), Enter Bills, Pay Bills, and Write Checks / Expenses."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from accounting.engine import AccountingEngine, AccountingError
from accounting.models import BillLineInput
from accounting.money import (
    convert_to_home,
    format_money,
    format_rate,
    parse_money_to_cents,
    parse_rate_to_micros,
)
from ui.styles import make_readonly_item, show_error, show_info, style_table_defaults


class VendorDialog(QDialog):
    def __init__(
        self,
        engine: AccountingEngine,
        existing: Optional[dict[str, Any]] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.existing = existing
        self.setWindowTitle("Edit Vendor" if existing else "New Vendor")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.company_edit = QLineEdit()
        self.email_edit = QLineEdit()
        self.phone_edit = QLineEdit()
        self.addr_edit = QLineEdit()
        self.ccy_combo = QComboBox()
        for c in engine.list_currencies():
            self.ccy_combo.addItem(f"{c['code']} - {c['name']}", c["code"])
        self.terms_edit = QLineEdit("Net 30")

        if existing:
            self.name_edit.setText(existing["name"])
            self.company_edit.setText(existing["company_name"])
            self.email_edit.setText(existing["email"])
            self.phone_edit.setText(existing["phone"])
            self.addr_edit.setText(existing["address"])
            idx = self.ccy_combo.findData(existing["currency_code"])
            if idx >= 0:
                self.ccy_combo.setCurrentIndex(idx)
            self.terms_edit.setText(existing["terms"])
        else:
            idx = self.ccy_combo.findData(engine.get_home_currency())
            if idx >= 0:
                self.ccy_combo.setCurrentIndex(idx)

        form.addRow("Vendor Name:", self.name_edit)
        form.addRow("Company Name:", self.company_edit)
        form.addRow("Email:", self.email_edit)
        form.addRow("Phone:", self.phone_edit)
        form.addRow("Address:", self.addr_edit)
        form.addRow("Currency:", self.ccy_combo)
        form.addRow("Payment Terms:", self.terms_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        try:
            if not self.name_edit.text().strip():
                raise AccountingError("Vendor Name is required.")
            if self.existing:
                self.engine.update_vendor(
                    vendor_id=self.existing["id"],
                    name=self.name_edit.text(),
                    company_name=self.company_edit.text(),
                    email=self.email_edit.text(),
                    phone=self.phone_edit.text(),
                    address=self.addr_edit.text(),
                    currency_code=self.ccy_combo.currentData(),
                    terms=self.terms_edit.text(),
                )
            else:
                self.engine.create_vendor(
                    name=self.name_edit.text(),
                    company_name=self.company_edit.text(),
                    email=self.email_edit.text(),
                    phone=self.phone_edit.text(),
                    address=self.addr_edit.text(),
                    currency_code=self.ccy_combo.currentData(),
                    terms=self.terms_edit.text(),
                )
            self.accept()
        except Exception as exc:
            show_error(self, "Vendor Error", str(exc))


class VendorCenterView(QWidget):
    """Split-pane Vendor Center: vendor list + currency/open balance on left, bills/payments on right."""

    def __init__(
        self,
        engine: AccountingEngine,
        open_action: Callable[[str], None],
        on_data_changed: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.open_action = open_action
        self.on_data_changed = on_data_changed
        self._vendors: list[dict[str, Any]] = []
        self._txns: list[dict[str, Any]] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        bar = QHBoxLayout()
        title = QLabel("Vendor Center (Accounts Payable)")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #1a365d;")
        bar.addWidget(title)
        bar.addStretch()

        new_btn = QPushButton("+ New Vendor")
        new_btn.setProperty("class", "PrimaryButton")
        new_btn.clicked.connect(self._new_vendor)
        bar.addWidget(new_btn)

        edit_btn = QPushButton("Edit Vendor")
        edit_btn.clicked.connect(self._edit_vendor)
        bar.addWidget(edit_btn)

        bill_btn = QPushButton("Enter Bill")
        bill_btn.clicked.connect(lambda: self.open_action("enter_bills"))
        bar.addWidget(bill_btn)

        pay_btn = QPushButton("Pay Bills")
        pay_btn.clicked.connect(lambda: self.open_action("pay_bills"))
        bar.addWidget(pay_btn)
        layout.addLayout(bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.vend_table = QTableWidget(0, 4)
        self.vend_table.setHorizontalHeaderLabels(["Vendor", "Ccy", "Open Balance", "Home Equiv."])
        style_table_defaults(self.vend_table)
        self.vend_table.itemSelectionChanged.connect(self._on_vendor_selected)
        splitter.addWidget(self.vend_table)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 0, 0, 0)
        rtop = QHBoxLayout()
        self.detail_lbl = QLabel("All Vendor Transactions")
        self.detail_lbl.setStyleSheet("font-weight: 700; color: #1a365d;")
        rtop.addWidget(self.detail_lbl)
        rtop.addStretch()
        void_btn = QPushButton("Void Selected Txn")
        void_btn.clicked.connect(self._void_selected_txn)
        rtop.addWidget(void_btn)
        rl.addLayout(rtop)

        self.txn_table = QTableWidget(0, 8)
        self.txn_table.setHorizontalHeaderLabels(
            ["Date", "Type", "Number", "Status", "Ccy", "Foreign Total", "Home Total", "Memo"]
        )
        style_table_defaults(self.txn_table)
        rl.addWidget(self.txn_table)
        splitter.addWidget(right)
        splitter.setSizes([360, 640])
        layout.addWidget(splitter)

    def refresh(self) -> None:
        home = self.engine.get_home_currency()
        self._vendors = self.engine.list_vendors()
        self.vend_table.setRowCount(len(self._vendors))
        for idx, v in enumerate(self._vendors):
            self.vend_table.setItem(idx, 0, make_readonly_item(v["name"]))
            self.vend_table.setItem(idx, 1, make_readonly_item(v["currency_code"]))
            self.vend_table.setItem(
                idx, 2, make_readonly_item(format_money(v["open_balance_foreign"], v["currency_code"]), align_right=True)
            )
            self.vend_table.setItem(
                idx, 3, make_readonly_item(format_money(v["open_balance_home"], home), align_right=True)
            )
        self.vend_table.resizeColumnsToContents()
        self._on_vendor_selected()

    def _on_vendor_selected(self) -> None:
        home = self.engine.get_home_currency()
        row = self.vend_table.currentRow()
        if row < 0 or row >= len(self._vendors):
            self._txns = self.engine.list_transactions(txn_types=("Bill", "BillPayment", "Check", "Expense"))
            self.detail_lbl.setText("All Vendor Transactions")
        else:
            v = self._vendors[row]
            self._txns = self.engine.list_transactions(
                txn_types=("Bill", "BillPayment", "Check", "Expense"), entity_id=v["id"]
            )
            self.detail_lbl.setText(f"{v['name']} ({v['currency_code']}) — {v['email']} | {v['terms']}")

        self.txn_table.setRowCount(len(self._txns))
        for idx, t in enumerate(self._txns):
            self.txn_table.setItem(idx, 0, make_readonly_item(t["txn_date"]))
            self.txn_table.setItem(idx, 1, make_readonly_item(t["txn_type"]))
            self.txn_table.setItem(idx, 2, make_readonly_item(t["txn_number"]))
            self.txn_table.setItem(idx, 3, make_readonly_item(t["status"]))
            self.txn_table.setItem(idx, 4, make_readonly_item(t["currency_code"]))
            self.txn_table.setItem(
                idx, 5, make_readonly_item(format_money(t["total_foreign"], t["currency_code"]), align_right=True)
            )
            self.txn_table.setItem(
                idx, 6, make_readonly_item(format_money(t["total_home"], home), align_right=True)
            )
            self.txn_table.setItem(idx, 7, make_readonly_item(t["memo"] or ""))
        self.txn_table.resizeColumnsToContents()

    def _new_vendor(self) -> None:
        dlg = VendorDialog(self.engine, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.on_data_changed()

    def _edit_vendor(self) -> None:
        row = self.vend_table.currentRow()
        if 0 <= row < len(self._vendors):
            dlg = VendorDialog(self.engine, existing=self._vendors[row], parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.refresh()
                self.on_data_changed()

    def _void_selected_txn(self) -> None:
        row = self.txn_table.currentRow()
        if row < 0 or row >= len(self._txns):
            return
        txn = self._txns[row]
        try:
            self.engine.void_transaction(txn["id"])
            show_info(self, "Voided", f"Transaction {txn['txn_number']} has been voided.")
            self.refresh()
            self.on_data_changed()
        except Exception as exc:
            show_error(self, "Void Error", str(exc))


class EnterBillsView(QWidget):
    """Enter Vendor Bills: supports both Expense Accounts and Inventory Items (creating FIFO layers)."""

    def __init__(
        self,
        engine: AccountingEngine,
        on_data_changed: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.on_data_changed = on_data_changed
        self._items_cache: list[dict[str, Any]] = []
        self._expense_accts: list[dict[str, Any]] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        hdr = QLabel("Enter Bills (Accounts Payable / FIFO Inventory Purchase)")
        hdr.setStyleSheet("font-size: 16px; font-weight: 700; color: #1a365d;")
        layout.addWidget(hdr)

        top = QHBoxLayout()
        f1 = QFormLayout()
        f2 = QFormLayout()

        self.vendor_combo = QComboBox()
        self.vendor_combo.currentIndexChanged.connect(self._on_vendor_changed)
        self.date_edit = QLineEdit(date.today().isoformat())
        self.due_edit = QLineEdit((date.today() + timedelta(days=30)).isoformat())

        self.ccy_lbl = QLabel("USD")
        self.ccy_lbl.setStyleSheet("font-weight: 700; color: #2b6cb0;")
        self.rate_edit = QLineEdit("1.000000")
        self.rate_edit.textChanged.connect(self._recalc)
        self.memo_edit = QLineEdit()

        f1.addRow("Vendor:", self.vendor_combo)
        f1.addRow("Bill Date:", self.date_edit)
        f1.addRow("Due Date:", self.due_edit)
        f2.addRow("Currency:", self.ccy_lbl)
        f2.addRow("Exchange Rate (to Home):", self.rate_edit)
        f2.addRow("Memo:", self.memo_edit)

        top.addLayout(f1)
        top.addLayout(f2)
        layout.addLayout(top)

        self.lines_table = QTableWidget(0, 7)
        self.lines_table.setHorizontalHeaderLabels(
            ["Item (Optional - FIFO)", "Expense Account", "Description", "Qty", "Unit Cost (Foreign)", "Amount (Foreign)", "Amount (Home)"]
        )
        style_table_defaults(self.lines_table)
        layout.addWidget(self.lines_table)

        btns = QHBoxLayout()
        add_btn = QPushButton("+ Add Line")
        add_btn.clicked.connect(self._add_row)
        rem_btn = QPushButton("Remove Line")
        rem_btn.clicked.connect(self._remove_row)
        btns.addWidget(add_btn)
        btns.addWidget(rem_btn)
        btns.addStretch()

        self.total_lbl = QLabel("Total: $0.00")
        self.total_lbl.setStyleSheet("font-size: 14px; font-weight: 700; color: #1a365d;")
        btns.addWidget(self.total_lbl)
        layout.addLayout(btns)

        footer = QHBoxLayout()
        footer.addStretch()
        save_btn = QPushButton("Save & Post Bill")
        save_btn.setProperty("class", "SuccessButton")
        save_btn.clicked.connect(self._save_bill)
        footer.addWidget(save_btn)
        layout.addLayout(footer)

    def refresh(self) -> None:
        self._items_cache = self.engine.list_items()
        self._expense_accts = self.engine.list_accounts(account_types=("Expense", "Asset"))
        self.vendor_combo.blockSignals(True)
        self.vendor_combo.clear()
        for v in self.engine.list_vendors():
            self.vendor_combo.addItem(f"{v['name']} ({v['currency_code']})", v)
        self.vendor_combo.blockSignals(False)
        self._on_vendor_changed()
        if self.lines_table.rowCount() == 0:
            self._add_row()

    def _on_vendor_changed(self) -> None:
        v = self.vendor_combo.currentData()
        if not v:
            return
        ccy = v["currency_code"]
        self.ccy_lbl.setText(ccy)
        self.rate_edit.setText(format_rate(self.engine.get_exchange_rate(ccy)))
        self.rate_edit.setReadOnly(ccy == self.engine.get_home_currency())
        self._recalc()

    def _add_row(self) -> None:
        r = self.lines_table.rowCount()
        self.lines_table.insertRow(r)

        item_combo = QComboBox()
        item_combo.addItem("(Expense Account Only)", None)
        for it in self._items_cache:
            item_combo.addItem(f"{it['sku']} - {it['name']} ({it['type']})", it)

        acct_combo = QComboBox()
        for a in self._expense_accts:
            acct_combo.addItem(f"{a['account_number']} {a['name']}", a["id"])

        desc_edit = QLineEdit()
        qty_spin = QSpinBox()
        qty_spin.setRange(1, 1_000_000)
        qty_spin.setValue(1)
        rate_edit = QLineEdit("0.00")

        def on_item_pick(_idx: int) -> None:
            it = item_combo.currentData()
            if it:
                desc_edit.setText(it["name"])
                rate_edit.setText(format_money(it["purchase_cost"], show_symbol=False))
                target_acct = it["asset_account_id"] if it["type"] == "Inventory" else it["expense_account_id"]
                idx = acct_combo.findData(target_acct)
                if idx >= 0:
                    acct_combo.setCurrentIndex(idx)
                self._recalc()

        item_combo.currentIndexChanged.connect(on_item_pick)
        qty_spin.valueChanged.connect(self._recalc)
        rate_edit.textChanged.connect(self._recalc)

        self.lines_table.setCellWidget(r, 0, item_combo)
        self.lines_table.setCellWidget(r, 1, acct_combo)
        self.lines_table.setCellWidget(r, 2, desc_edit)
        self.lines_table.setCellWidget(r, 3, qty_spin)
        self.lines_table.setCellWidget(r, 4, rate_edit)
        self.lines_table.setItem(r, 5, make_readonly_item("0.00", align_right=True))
        self.lines_table.setItem(r, 6, make_readonly_item("0.00", align_right=True))
        self.lines_table.resizeColumnsToContents()

    def _remove_row(self) -> None:
        r = self.lines_table.currentRow()
        if r >= 0:
            self.lines_table.removeRow(r)
            self._recalc()

    def _recalc(self) -> None:
        ccy = self.ccy_lbl.text() or "USD"
        home = self.engine.get_home_currency()
        try:
            rate = parse_rate_to_micros(self.rate_edit.text())
        except Exception:
            rate = 1_000_000

        tot_f = 0
        tot_h = 0
        for r in range(self.lines_table.rowCount()):
            item_w = self.lines_table.cellWidget(r, 0)
            qty_w = self.lines_table.cellWidget(r, 3)
            rate_w = self.lines_table.cellWidget(r, 4)
            if not isinstance(qty_w, QSpinBox) or not isinstance(rate_w, QLineEdit):
                continue
            try:
                unit_f = parse_money_to_cents(rate_w.text())
            except Exception:
                unit_f = 0
            it = item_w.currentData() if isinstance(item_w, QComboBox) else None
            amt_f = qty_w.value() * unit_f
            if it and it["type"] == "Inventory":
                amt_h = qty_w.value() * convert_to_home(unit_f, rate)
            else:
                amt_h = convert_to_home(amt_f, rate)
            tot_f += amt_f
            tot_h += amt_h
            self.lines_table.setItem(r, 5, make_readonly_item(format_money(amt_f, ccy), align_right=True))
            self.lines_table.setItem(r, 6, make_readonly_item(format_money(amt_h, home), align_right=True))

        self.total_lbl.setText(f"Total: {format_money(tot_f, ccy)}  |  Home Equiv: {format_money(tot_h, home)}")

    def _save_bill(self) -> None:
        v = self.vendor_combo.currentData()
        if not v:
            show_error(self, "Missing Vendor", "Please create and select a vendor first.")
            return
        try:
            rate = parse_rate_to_micros(self.rate_edit.text())
            lines: list[BillLineInput] = []
            for r in range(self.lines_table.rowCount()):
                item_w = self.lines_table.cellWidget(r, 0)
                acct_w = self.lines_table.cellWidget(r, 1)
                desc_w = self.lines_table.cellWidget(r, 2)
                qty_w = self.lines_table.cellWidget(r, 3)
                rate_w = self.lines_table.cellWidget(r, 4)
                it = item_w.currentData() if isinstance(item_w, QComboBox) else None
                acct_id = acct_w.currentData() if isinstance(acct_w, QComboBox) else None
                unit_f = parse_money_to_cents(rate_w.text()) if isinstance(rate_w, QLineEdit) else 0
                if unit_f <= 0:
                    continue
                lines.append(
                    BillLineInput(
                        item_id=it["id"] if it else None,
                        account_id=acct_id,
                        description=desc_w.text().strip() if isinstance(desc_w, QLineEdit) else "",
                        quantity=qty_w.value() if isinstance(qty_w, QSpinBox) else 1,
                        rate_foreign=unit_f,
                    )
                )
            if not lines:
                raise AccountingError("Enter at least one bill line with cost > 0.")

            txn_id = self.engine.create_bill(
                vendor_id=v["id"],
                txn_date=self.date_edit.text().strip(),
                due_date=self.due_edit.text().strip(),
                lines=lines,
                currency_code=v["currency_code"],
                exchange_rate_micros=rate,
                memo=self.memo_edit.text().strip(),
            )
            show_info(self, "Bill Posted", f"Bill #{txn_id} posted (FIFO layers created for inventory items).")
            self.lines_table.setRowCount(0)
            self._add_row()
            self.memo_edit.clear()
            self.refresh()
            self.on_data_changed()
        except Exception as exc:
            show_error(self, "Bill Error", str(exc))


class PayBillsView(QWidget):
    """Pay Vendor Bills via Checking with automatic Realized FX Gain/Loss calculation."""

    def __init__(
        self,
        engine: AccountingEngine,
        on_data_changed: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.on_data_changed = on_data_changed
        self._open_bills: list[dict[str, Any]] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        hdr = QLabel("Pay Bills")
        hdr.setStyleSheet("font-size: 16px; font-weight: 700; color: #1a365d;")
        layout.addWidget(hdr)

        top = QHBoxLayout()
        f1 = QFormLayout()
        f2 = QFormLayout()

        self.vendor_combo = QComboBox()
        self.vendor_combo.currentIndexChanged.connect(self._load_open_bills)
        self.date_edit = QLineEdit(date.today().isoformat())
        self.bank_combo = QComboBox()

        self.ccy_lbl = QLabel("USD")
        self.ccy_lbl.setStyleSheet("font-weight: 700; color: #2b6cb0;")
        self.rate_edit = QLineEdit("1.000000")
        self.rate_edit.textChanged.connect(self._update_preview)
        self.memo_edit = QLineEdit()

        f1.addRow("Vendor:", self.vendor_combo)
        f1.addRow("Payment Date:", self.date_edit)
        f1.addRow("Pay From Bank:", self.bank_combo)
        f2.addRow("Bill Currency:", self.ccy_lbl)
        f2.addRow("Payment Exchange Rate:", self.rate_edit)
        f2.addRow("Memo:", self.memo_edit)

        top.addLayout(f1)
        top.addLayout(f2)
        layout.addLayout(top)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Pay", "Bill #", "Date", "Due Date", "Orig Rate", "Open Balance", "Amount to Pay"]
        )
        style_table_defaults(self.table)
        layout.addWidget(self.table)

        footer = QHBoxLayout()
        self.preview_lbl = QLabel("Total Payment: $0.00 | Realized FX: $0.00")
        self.preview_lbl.setStyleSheet("font-weight: 700; color: #1a365d;")
        footer.addWidget(self.preview_lbl)
        footer.addStretch()

        pay_btn = QPushButton("Pay Selected Bills")
        pay_btn.setProperty("class", "SuccessButton")
        pay_btn.clicked.connect(self._pay_bills)
        footer.addWidget(pay_btn)
        layout.addLayout(footer)

    def refresh(self) -> None:
        self.bank_combo.clear()
        for a in self.engine.list_accounts(account_types=("Asset",)):
            if a["detail_type"] in ("Checking", "Savings"):
                self.bank_combo.addItem(f"{a['account_number']} {a['name']}", a["id"])

        self.vendor_combo.blockSignals(True)
        self.vendor_combo.clear()
        for v in self.engine.list_vendors():
            self.vendor_combo.addItem(f"{v['name']} ({v['currency_code']})", v)
        self.vendor_combo.blockSignals(False)
        self._load_open_bills()

    def _load_open_bills(self) -> None:
        v = self.vendor_combo.currentData()
        if not v:
            self.table.setRowCount(0)
            return
        ccy = v["currency_code"]
        self.ccy_lbl.setText(ccy)
        self.rate_edit.setText(format_rate(self.engine.get_exchange_rate(ccy)))
        self.rate_edit.setReadOnly(ccy == self.engine.get_home_currency())

        self._open_bills = self.engine.get_open_bills(v["id"])
        self.table.setRowCount(len(self._open_bills))
        for idx, b in enumerate(self._open_bills):
            chk = QCheckBox()
            chk.stateChanged.connect(self._update_preview)
            amt_edit = QLineEdit(format_money(b["open_foreign"], show_symbol=False))
            amt_edit.textChanged.connect(self._update_preview)
            self.table.setCellWidget(idx, 0, chk)
            self.table.setItem(idx, 1, make_readonly_item(b["txn_number"]))
            self.table.setItem(idx, 2, make_readonly_item(b["txn_date"]))
            self.table.setItem(idx, 3, make_readonly_item(b["due_date"] or ""))
            self.table.setItem(idx, 4, make_readonly_item(format_rate(b["exchange_rate_micros"]), align_right=True))
            self.table.setItem(
                idx, 5, make_readonly_item(format_money(b["open_foreign"], b["currency_code"]), align_right=True)
            )
            self.table.setCellWidget(idx, 6, amt_edit)
        self.table.resizeColumnsToContents()
        self._update_preview()

    def _update_preview(self) -> None:
        ccy = self.ccy_lbl.text() or "USD"
        home = self.engine.get_home_currency()
        try:
            rate = parse_rate_to_micros(self.rate_edit.text())
        except Exception:
            rate = 1_000_000

        tot_f = 0
        tot_cash_h = 0
        tot_fx_loss = 0
        for idx, b in enumerate(self._open_bills):
            chk = self.table.cellWidget(idx, 0)
            amt_w = self.table.cellWidget(idx, 6)
            if not isinstance(chk, QCheckBox) or not chk.isChecked() or not isinstance(amt_w, QLineEdit):
                continue
            try:
                app_f = parse_money_to_cents(amt_w.text())
            except Exception:
                continue
            open_f = int(b["open_foreign"])
            open_h = int(b["open_home"])
            if app_f <= 0 or open_f <= 0:
                continue
            orig_h = open_h if app_f == open_f else (open_h * app_f + open_f // 2) // open_f
            cash_h = convert_to_home(app_f, rate)
            tot_f += app_f
            tot_cash_h += cash_h
            tot_fx_loss += cash_h - orig_h

        fx_label = f"Loss {format_money(tot_fx_loss, home)}" if tot_fx_loss >= 0 else f"Gain {format_money(-tot_fx_loss, home)}"
        self.preview_lbl.setText(
            f"Payment: {format_money(tot_f, ccy)} ({format_money(tot_cash_h, home)})  |  Realized FX: {fx_label}"
        )

    def _pay_bills(self) -> None:
        v = self.vendor_combo.currentData()
        if not v:
            return
        try:
            rate = parse_rate_to_micros(self.rate_edit.text())
            apps: list[tuple[int, int]] = []
            for idx, b in enumerate(self._open_bills):
                chk = self.table.cellWidget(idx, 0)
                amt_w = self.table.cellWidget(idx, 6)
                if isinstance(chk, QCheckBox) and chk.isChecked() and isinstance(amt_w, QLineEdit):
                    apps.append((b["id"], parse_money_to_cents(amt_w.text())))
            if not apps:
                raise AccountingError("Check at least one open bill to pay.")

            self.engine.pay_bills(
                vendor_id=v["id"],
                txn_date=self.date_edit.text().strip(),
                applications=apps,
                bank_account_id=self.bank_combo.currentData(),
                currency_code=v["currency_code"],
                exchange_rate_micros=rate,
                memo=self.memo_edit.text().strip(),
            )
            show_info(self, "Bills Paid", "Bill payment posted to ledger.")
            self.memo_edit.clear()
            self._load_open_bills()
            self.on_data_changed()
        except Exception as exc:
            show_error(self, "Pay Bills Error", str(exc))


class WriteChecksView(QWidget):
    """Write Checks / Record Direct Bank Expense (Expense Account or FIFO Inventory purchase)."""

    def __init__(
        self,
        engine: AccountingEngine,
        on_data_changed: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.on_data_changed = on_data_changed
        self._items_cache: list[dict[str, Any]] = []
        self._expense_accts: list[dict[str, Any]] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        hdr = QLabel("Write Checks / Record Expense")
        hdr.setStyleSheet("font-size: 16px; font-weight: 700; color: #1a365d;")
        layout.addWidget(hdr)

        top = QHBoxLayout()
        f1 = QFormLayout()
        f2 = QFormLayout()

        self.bank_combo = QComboBox()
        self.vendor_combo = QComboBox()
        self.date_edit = QLineEdit(date.today().isoformat())
        self.memo_edit = QLineEdit()

        f1.addRow("Bank Account:", self.bank_combo)
        f1.addRow("Pay to the Order of (Vendor):", self.vendor_combo)
        f2.addRow("Date:", self.date_edit)
        f2.addRow("Memo:", self.memo_edit)

        top.addLayout(f1)
        top.addLayout(f2)
        layout.addLayout(top)

        self.lines_table = QTableWidget(0, 6)
        self.lines_table.setHorizontalHeaderLabels(
            ["Item (Optional - FIFO)", "Expense Account", "Description", "Qty", "Rate", "Amount"]
        )
        style_table_defaults(self.lines_table)
        layout.addWidget(self.lines_table)

        btns = QHBoxLayout()
        add_btn = QPushButton("+ Add Line")
        add_btn.clicked.connect(self._add_row)
        rem_btn = QPushButton("Remove Line")
        rem_btn.clicked.connect(self._remove_row)
        btns.addWidget(add_btn)
        btns.addWidget(rem_btn)
        btns.addStretch()

        self.total_lbl = QLabel("Check Total: $0.00")
        self.total_lbl.setStyleSheet("font-size: 14px; font-weight: 700; color: #1a365d;")
        btns.addWidget(self.total_lbl)
        layout.addLayout(btns)

        footer = QHBoxLayout()
        footer.addStretch()
        save_btn = QPushButton("Save & Post Check")
        save_btn.setProperty("class", "SuccessButton")
        save_btn.clicked.connect(self._save_check)
        footer.addWidget(save_btn)
        layout.addLayout(footer)

    def refresh(self) -> None:
        self._items_cache = self.engine.list_items()
        self._expense_accts = self.engine.list_accounts(account_types=("Expense", "Asset"))
        self.bank_combo.clear()
        for a in self.engine.list_accounts(account_types=("Asset",)):
            if a["detail_type"] in ("Checking", "Savings"):
                self.bank_combo.addItem(f"{a['account_number']} {a['name']}", a["id"])

        self.vendor_combo.clear()
        self.vendor_combo.addItem("(None / Cash Payee)", None)
        for v in self.engine.list_vendors():
            self.vendor_combo.addItem(v["name"], v["id"])

        if self.lines_table.rowCount() == 0:
            self._add_row()

    def _add_row(self) -> None:
        r = self.lines_table.rowCount()
        self.lines_table.insertRow(r)

        item_combo = QComboBox()
        item_combo.addItem("(Expense Account Only)", None)
        for it in self._items_cache:
            item_combo.addItem(f"{it['sku']} - {it['name']}", it)

        acct_combo = QComboBox()
        for a in self._expense_accts:
            acct_combo.addItem(f"{a['account_number']} {a['name']}", a["id"])

        desc_edit = QLineEdit()
        qty_spin = QSpinBox()
        qty_spin.setRange(1, 1_000_000)
        qty_spin.setValue(1)
        rate_edit = QLineEdit("0.00")

        qty_spin.valueChanged.connect(self._recalc)
        rate_edit.textChanged.connect(self._recalc)

        self.lines_table.setCellWidget(r, 0, item_combo)
        self.lines_table.setCellWidget(r, 1, acct_combo)
        self.lines_table.setCellWidget(r, 2, desc_edit)
        self.lines_table.setCellWidget(r, 3, qty_spin)
        self.lines_table.setCellWidget(r, 4, rate_edit)
        self.lines_table.setItem(r, 5, make_readonly_item("0.00", align_right=True))
        self.lines_table.resizeColumnsToContents()

    def _remove_row(self) -> None:
        r = self.lines_table.currentRow()
        if r >= 0:
            self.lines_table.removeRow(r)
            self._recalc()

    def _recalc(self) -> None:
        home = self.engine.get_home_currency()
        tot = 0
        for r in range(self.lines_table.rowCount()):
            qty_w = self.lines_table.cellWidget(r, 3)
            rate_w = self.lines_table.cellWidget(r, 4)
            if not isinstance(qty_w, QSpinBox) or not isinstance(rate_w, QLineEdit):
                continue
            try:
                unit = parse_money_to_cents(rate_w.text())
            except Exception:
                unit = 0
            amt = qty_w.value() * unit
            tot += amt
            self.lines_table.setItem(r, 5, make_readonly_item(format_money(amt, home), align_right=True))
        self.total_lbl.setText(f"Check Total: {format_money(tot, home)}")

    def _save_check(self) -> None:
        try:
            lines: list[BillLineInput] = []
            for r in range(self.lines_table.rowCount()):
                item_w = self.lines_table.cellWidget(r, 0)
                acct_w = self.lines_table.cellWidget(r, 1)
                desc_w = self.lines_table.cellWidget(r, 2)
                qty_w = self.lines_table.cellWidget(r, 3)
                rate_w = self.lines_table.cellWidget(r, 4)
                it = item_w.currentData() if isinstance(item_w, QComboBox) else None
                acct_id = acct_w.currentData() if isinstance(acct_w, QComboBox) else None
                unit = parse_money_to_cents(rate_w.text()) if isinstance(rate_w, QLineEdit) else 0
                if unit <= 0:
                    continue
                lines.append(
                    BillLineInput(
                        item_id=it["id"] if it else None,
                        account_id=acct_id,
                        description=desc_w.text().strip() if isinstance(desc_w, QLineEdit) else "",
                        quantity=qty_w.value() if isinstance(qty_w, QSpinBox) else 1,
                        rate_foreign=unit,
                    )
                )
            if not lines:
                raise AccountingError("Add at least one line with amount > 0.")

            self.engine.write_check(
                txn_date=self.date_edit.text().strip(),
                lines=lines,
                bank_account_id=self.bank_combo.currentData(),
                vendor_id=self.vendor_combo.currentData(),
                memo=self.memo_edit.text().strip(),
            )
            show_info(self, "Check Posted", "Check / Expense posted to ledger.")
            self.lines_table.setRowCount(0)
            self._add_row()
            self.memo_edit.clear()
            self.on_data_changed()
        except Exception as exc:
            show_error(self, "Check Error", str(exc))
