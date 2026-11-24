"""Customer Center (Split-Pane), Create Invoice Form, and Receive Payments Form."""

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
from accounting.models import InvoiceLineInput
from accounting.money import (
    convert_to_home,
    format_money,
    format_rate,
    parse_money_to_cents,
    parse_rate_to_micros,
)
from ui.styles import make_readonly_item, show_error, show_info, style_table_defaults


class CustomerDialog(QDialog):
    def __init__(
        self,
        engine: AccountingEngine,
        existing: Optional[dict[str, Any]] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.existing = existing
        self.setWindowTitle("Edit Customer" if existing else "New Customer")
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
            self.addr_edit.setText(existing["billing_address"])
            idx = self.ccy_combo.findData(existing["currency_code"])
            if idx >= 0:
                self.ccy_combo.setCurrentIndex(idx)
            self.terms_edit.setText(existing["terms"])
        else:
            idx = self.ccy_combo.findData(engine.get_home_currency())
            if idx >= 0:
                self.ccy_combo.setCurrentIndex(idx)

        form.addRow("Customer Name:", self.name_edit)
        form.addRow("Company Name:", self.company_edit)
        form.addRow("Email:", self.email_edit)
        form.addRow("Phone:", self.phone_edit)
        form.addRow("Billing Address:", self.addr_edit)
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
                raise AccountingError("Customer Name is required.")
            if self.existing:
                self.engine.update_customer(
                    customer_id=self.existing["id"],
                    name=self.name_edit.text(),
                    company_name=self.company_edit.text(),
                    email=self.email_edit.text(),
                    phone=self.phone_edit.text(),
                    billing_address=self.addr_edit.text(),
                    currency_code=self.ccy_combo.currentData(),
                    terms=self.terms_edit.text(),
                )
            else:
                self.engine.create_customer(
                    name=self.name_edit.text(),
                    company_name=self.company_edit.text(),
                    email=self.email_edit.text(),
                    phone=self.phone_edit.text(),
                    billing_address=self.addr_edit.text(),
                    currency_code=self.ccy_combo.currentData(),
                    terms=self.terms_edit.text(),
                )
            self.accept()
        except Exception as exc:
            show_error(self, "Customer Error", str(exc))


class CustomerCenterView(QWidget):
    """Split-pane Customer Center: customer list + currency/open balance on left, history on right."""

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
        self._customers: list[dict[str, Any]] = []
        self._txns: list[dict[str, Any]] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        bar = QHBoxLayout()
        title = QLabel("Customer Center (Accounts Receivable)")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #1a365d;")
        bar.addWidget(title)
        bar.addStretch()

        new_cust_btn = QPushButton("+ New Customer")
        new_cust_btn.setProperty("class", "PrimaryButton")
        new_cust_btn.clicked.connect(self._new_customer)
        bar.addWidget(new_cust_btn)

        edit_cust_btn = QPushButton("Edit Customer")
        edit_cust_btn.clicked.connect(self._edit_customer)
        bar.addWidget(edit_cust_btn)

        inv_btn = QPushButton("Create Invoice")
        inv_btn.clicked.connect(lambda: self.open_action("create_invoice"))
        bar.addWidget(inv_btn)

        pmt_btn = QPushButton("Receive Payment")
        pmt_btn.clicked.connect(lambda: self.open_action("receive_payments"))
        bar.addWidget(pmt_btn)
        layout.addLayout(bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.cust_table = QTableWidget(0, 4)
        self.cust_table.setHorizontalHeaderLabels(["Customer", "Ccy", "Open Balance", "Home Equiv."])
        style_table_defaults(self.cust_table)
        self.cust_table.itemSelectionChanged.connect(self._on_customer_selected)
        splitter.addWidget(self.cust_table)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 0, 0, 0)
        rtop = QHBoxLayout()
        self.detail_lbl = QLabel("Select a customer to view transactions")
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
        self._customers = self.engine.list_customers()
        self.cust_table.setRowCount(len(self._customers))
        for idx, c in enumerate(self._customers):
            self.cust_table.setItem(idx, 0, make_readonly_item(c["name"]))
            self.cust_table.setItem(idx, 1, make_readonly_item(c["currency_code"]))
            self.cust_table.setItem(
                idx, 2, make_readonly_item(format_money(c["open_balance_foreign"], c["currency_code"]), align_right=True)
            )
            self.cust_table.setItem(
                idx, 3, make_readonly_item(format_money(c["open_balance_home"], home), align_right=True)
            )
        self.cust_table.resizeColumnsToContents()
        self._on_customer_selected()

    def _on_customer_selected(self) -> None:
        home = self.engine.get_home_currency()
        row = self.cust_table.currentRow()
        if row < 0 or row >= len(self._customers):
            self._txns = self.engine.list_transactions(txn_types=("Invoice", "Payment", "SalesReceipt"))
            self.detail_lbl.setText("All Customer Transactions")
        else:
            c = self._customers[row]
            self._txns = self.engine.list_transactions(
                txn_types=("Invoice", "Payment", "SalesReceipt"), entity_id=c["id"]
            )
            self.detail_lbl.setText(f"{c['name']} ({c['currency_code']}) — {c['email']} | {c['terms']}")

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

    def _new_customer(self) -> None:
        dlg = CustomerDialog(self.engine, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.on_data_changed()

    def _edit_customer(self) -> None:
        row = self.cust_table.currentRow()
        if 0 <= row < len(self._customers):
            dlg = CustomerDialog(self.engine, existing=self._customers[row], parent=self)
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


class CreateInvoiceView(QWidget):
    """Create Invoice form with automatic customer currency/rate fill and FIFO inventory COGS."""

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
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        hdr = QLabel("Create Invoice")
        hdr.setStyleSheet("font-size: 16px; font-weight: 700; color: #1a365d;")
        layout.addWidget(hdr)

        top_form = QHBoxLayout()
        left_form = QFormLayout()
        right_form = QFormLayout()

        self.customer_combo = QComboBox()
        self.customer_combo.currentIndexChanged.connect(self._on_customer_changed)
        self.date_edit = QLineEdit(date.today().isoformat())
        self.due_edit = QLineEdit((date.today() + timedelta(days=30)).isoformat())
        self.memo_edit = QLineEdit()

        self.ccy_lbl = QLabel("USD")
        self.ccy_lbl.setStyleSheet("font-weight: 700; color: #2b6cb0;")
        self.rate_edit = QLineEdit("1.000000")
        self.rate_edit.textChanged.connect(self._recalc_totals)

        left_form.addRow("Customer:", self.customer_combo)
        left_form.addRow("Invoice Date:", self.date_edit)
        left_form.addRow("Due Date:", self.due_edit)
        right_form.addRow("Currency:", self.ccy_lbl)
        right_form.addRow("Exchange Rate (to Home):", self.rate_edit)
        right_form.addRow("Memo:", self.memo_edit)

        top_form.addLayout(left_form)
        top_form.addLayout(right_form)
        layout.addLayout(top_form)

        # Lines table
        self.lines_table = QTableWidget(0, 6)
        self.lines_table.setHorizontalHeaderLabels(
            ["Item", "Description", "Qty", "Rate (Foreign)", "Amount (Foreign)", "Amount (Home)"]
        )
        style_table_defaults(self.lines_table)
        layout.addWidget(self.lines_table)

        line_btns = QHBoxLayout()
        add_line_btn = QPushButton("+ Add Line")
        add_line_btn.clicked.connect(self._add_row)
        del_line_btn = QPushButton("Remove Selected Line")
        del_line_btn.clicked.connect(self._remove_row)
        line_btns.addWidget(add_line_btn)
        line_btns.addWidget(del_line_btn)
        line_btns.addStretch()

        self.total_lbl = QLabel("Total: $0.00")
        self.total_lbl.setStyleSheet("font-size: 14px; font-weight: 700; color: #1a365d;")
        line_btns.addWidget(self.total_lbl)
        layout.addLayout(line_btns)

        footer = QHBoxLayout()
        footer.addStretch()
        save_btn = QPushButton("Save & Post Invoice")
        save_btn.setProperty("class", "SuccessButton")
        save_btn.clicked.connect(self._save_invoice)
        footer.addWidget(save_btn)
        layout.addLayout(footer)

    def refresh(self) -> None:
        self._items_cache = self.engine.list_items()
        self.customer_combo.blockSignals(True)
        self.customer_combo.clear()
        for c in self.engine.list_customers():
            self.customer_combo.addItem(f"{c['name']} ({c['currency_code']})", c)
        self.customer_combo.blockSignals(False)
        self._on_customer_changed()
        if self.lines_table.rowCount() == 0:
            self._add_row()

    def _on_customer_changed(self) -> None:
        cust = self.customer_combo.currentData()
        if not cust:
            return
        ccy = cust["currency_code"]
        self.ccy_lbl.setText(ccy)
        rate = self.engine.get_exchange_rate(ccy)
        self.rate_edit.setText(format_rate(rate))
        self.rate_edit.setReadOnly(ccy == self.engine.get_home_currency())
        self._recalc_totals()

    def _add_row(self) -> None:
        r = self.lines_table.rowCount()
        self.lines_table.insertRow(r)

        item_combo = QComboBox()
        item_combo.addItem("(Select Item)", None)
        for it in self._items_cache:
            qty_info = f" [Qty: {it['qty_on_hand']}]" if it["type"] == "Inventory" else ""
            item_combo.addItem(f"{it['sku']} - {it['name']}{qty_info}", it)

        desc_edit = QLineEdit()
        qty_spin = QSpinBox()
        qty_spin.setRange(1, 1_000_000)
        qty_spin.setValue(1)
        rate_edit = QLineEdit("0.00")

        def on_item_pick(_idx: int, row_i: int = r) -> None:
            it = item_combo.currentData()
            if it:
                desc_edit.setText(it["description"] or it["name"])
                rate_edit.setText(format_money(it["sales_price"], show_symbol=False))
                self._recalc_totals()

        item_combo.currentIndexChanged.connect(on_item_pick)
        qty_spin.valueChanged.connect(self._recalc_totals)
        rate_edit.textChanged.connect(self._recalc_totals)

        self.lines_table.setCellWidget(r, 0, item_combo)
        self.lines_table.setCellWidget(r, 1, desc_edit)
        self.lines_table.setCellWidget(r, 2, qty_spin)
        self.lines_table.setCellWidget(r, 3, rate_edit)
        self.lines_table.setItem(r, 4, make_readonly_item("0.00", align_right=True))
        self.lines_table.setItem(r, 5, make_readonly_item("0.00", align_right=True))
        self.lines_table.resizeColumnsToContents()

    def _remove_row(self) -> None:
        r = self.lines_table.currentRow()
        if r >= 0:
            self.lines_table.removeRow(r)
            self._recalc_totals()

    def _recalc_totals(self) -> None:
        ccy = self.ccy_lbl.text() or "USD"
        home = self.engine.get_home_currency()
        try:
            rate_micros = parse_rate_to_micros(self.rate_edit.text())
        except Exception:
            rate_micros = 1_000_000

        tot_f = 0
        tot_h = 0
        for r in range(self.lines_table.rowCount()):
            qty_w = self.lines_table.cellWidget(r, 2)
            rate_w = self.lines_table.cellWidget(r, 3)
            if not isinstance(qty_w, QSpinBox) or not isinstance(rate_w, QLineEdit):
                continue
            try:
                unit_f = parse_money_to_cents(rate_w.text())
            except Exception:
                unit_f = 0
            amt_f = qty_w.value() * unit_f
            amt_h = convert_to_home(amt_f, rate_micros)
            tot_f += amt_f
            tot_h += amt_h
            self.lines_table.setItem(r, 4, make_readonly_item(format_money(amt_f, ccy), align_right=True))
            self.lines_table.setItem(r, 5, make_readonly_item(format_money(amt_h, home), align_right=True))

        self.total_lbl.setText(
            f"Total: {format_money(tot_f, ccy)}  |  Home Equiv: {format_money(tot_h, home)}"
        )

    def _save_invoice(self) -> None:
        cust = self.customer_combo.currentData()
        if not cust:
            show_error(self, "Missing Customer", "Please create and select a customer first.")
            return
        try:
            ccy = cust["currency_code"]
            rate_micros = parse_rate_to_micros(self.rate_edit.text())
            lines: list[InvoiceLineInput] = []
            for r in range(self.lines_table.rowCount()):
                item_w = self.lines_table.cellWidget(r, 0)
                desc_w = self.lines_table.cellWidget(r, 1)
                qty_w = self.lines_table.cellWidget(r, 2)
                rate_w = self.lines_table.cellWidget(r, 3)
                it = item_w.currentData() if isinstance(item_w, QComboBox) else None
                rate_cents = parse_money_to_cents(rate_w.text()) if isinstance(rate_w, QLineEdit) else 0
                if rate_cents <= 0 and it is None:
                    continue
                lines.append(
                    InvoiceLineInput(
                        item_id=it["id"] if it else None,
                        description=desc_w.text().strip() if isinstance(desc_w, QLineEdit) else "",
                        quantity=qty_w.value() if isinstance(qty_w, QSpinBox) else 1,
                        rate_foreign=rate_cents,
                    )
                )
            if not lines:
                raise AccountingError("Add at least one valid line item.")

            txn_id = self.engine.create_invoice(
                customer_id=cust["id"],
                txn_date=self.date_edit.text().strip(),
                due_date=self.due_edit.text().strip(),
                lines=lines,
                currency_code=ccy,
                exchange_rate_micros=rate_micros,
                memo=self.memo_edit.text().strip(),
            )
            show_info(self, "Invoice Posted", f"Invoice #{txn_id} posted to ledger (including FIFO COGS if applicable).")
            self.lines_table.setRowCount(0)
            self._add_row()
            self.memo_edit.clear()
            self.refresh()
            self.on_data_changed()
        except Exception as exc:
            show_error(self, "Invoice Error", str(exc))


class ReceivePaymentsView(QWidget):
    """Receive Customer Payments with full/partial application and automatic Realized FX Gain/Loss."""

    def __init__(
        self,
        engine: AccountingEngine,
        on_data_changed: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.on_data_changed = on_data_changed
        self._open_invoices: list[dict[str, Any]] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        hdr = QLabel("Receive Customer Payments")
        hdr.setStyleSheet("font-size: 16px; font-weight: 700; color: #1a365d;")
        layout.addWidget(hdr)

        top = QHBoxLayout()
        f1 = QFormLayout()
        f2 = QFormLayout()

        self.customer_combo = QComboBox()
        self.customer_combo.currentIndexChanged.connect(self._load_open_invoices)
        self.date_edit = QLineEdit(date.today().isoformat())
        self.deposit_combo = QComboBox()

        self.ccy_lbl = QLabel("USD")
        self.ccy_lbl.setStyleSheet("font-weight: 700; color: #2b6cb0;")
        self.rate_edit = QLineEdit("1.000000")
        self.rate_edit.textChanged.connect(self._update_preview)
        self.memo_edit = QLineEdit()

        f1.addRow("Customer:", self.customer_combo)
        f1.addRow("Payment Date:", self.date_edit)
        f1.addRow("Deposit To:", self.deposit_combo)
        f2.addRow("Currency:", self.ccy_lbl)
        f2.addRow("Payment Exchange Rate:", self.rate_edit)
        f2.addRow("Memo:", self.memo_edit)

        top.addLayout(f1)
        top.addLayout(f2)
        layout.addLayout(top)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Pay", "Invoice #", "Date", "Due Date", "Orig Rate", "Open Balance", "Payment Amount"]
        )
        style_table_defaults(self.table)
        layout.addWidget(self.table)

        footer = QHBoxLayout()
        self.preview_lbl = QLabel("Total Payment: $0.00 | Realized FX Gain/Loss: $0.00")
        self.preview_lbl.setStyleSheet("font-weight: 700; color: #1a365d;")
        footer.addWidget(self.preview_lbl)
        footer.addStretch()

        save_btn = QPushButton("Record Payment")
        save_btn.setProperty("class", "SuccessButton")
        save_btn.clicked.connect(self._save_payment)
        footer.addWidget(save_btn)
        layout.addLayout(footer)

    def refresh(self) -> None:
        self.deposit_combo.clear()
        for a in self.engine.list_accounts(account_types=("Asset",)):
            if a["detail_type"] in ("Undeposited Funds", "Checking", "Savings"):
                self.deposit_combo.addItem(f"{a['account_number']} {a['name']}", a["id"])
        # Default to Undeposited Funds
        for i in range(self.deposit_combo.count()):
            if "Undeposited Funds" in self.deposit_combo.itemText(i):
                self.deposit_combo.setCurrentIndex(i)
                break

        self.customer_combo.blockSignals(True)
        self.customer_combo.clear()
        for c in self.engine.list_customers():
            self.customer_combo.addItem(f"{c['name']} ({c['currency_code']})", c)
        self.customer_combo.blockSignals(False)
        self._load_open_invoices()

    def _load_open_invoices(self) -> None:
        cust = self.customer_combo.currentData()
        if not cust:
            self.table.setRowCount(0)
            return
        ccy = cust["currency_code"]
        self.ccy_lbl.setText(ccy)
        self.rate_edit.setText(format_rate(self.engine.get_exchange_rate(ccy)))
        self.rate_edit.setReadOnly(ccy == self.engine.get_home_currency())

        self._open_invoices = self.engine.get_open_invoices(cust["id"])
        self.table.setRowCount(len(self._open_invoices))
        for idx, inv in enumerate(self._open_invoices):
            chk = QCheckBox()
            chk.stateChanged.connect(self._update_preview)
            amt_edit = QLineEdit(format_money(inv["open_foreign"], show_symbol=False))
            amt_edit.textChanged.connect(self._update_preview)

            self.table.setCellWidget(idx, 0, chk)
            self.table.setItem(idx, 1, make_readonly_item(inv["txn_number"]))
            self.table.setItem(idx, 2, make_readonly_item(inv["txn_date"]))
            self.table.setItem(idx, 3, make_readonly_item(inv["due_date"] or ""))
            self.table.setItem(idx, 4, make_readonly_item(format_rate(inv["exchange_rate_micros"]), align_right=True))
            self.table.setItem(
                idx, 5, make_readonly_item(format_money(inv["open_foreign"], inv["currency_code"]), align_right=True)
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
        tot_fx_gain = 0
        for idx, inv in enumerate(self._open_invoices):
            chk = self.table.cellWidget(idx, 0)
            amt_w = self.table.cellWidget(idx, 6)
            if not isinstance(chk, QCheckBox) or not chk.isChecked() or not isinstance(amt_w, QLineEdit):
                continue
            try:
                app_f = parse_money_to_cents(amt_w.text())
            except Exception:
                continue
            open_f = int(inv["open_foreign"])
            open_h = int(inv["open_home"])
            if app_f <= 0 or open_f <= 0:
                continue
            orig_h = open_h if app_f == open_f else (open_h * app_f + open_f // 2) // open_f
            cash_h = convert_to_home(app_f, rate)
            tot_f += app_f
            tot_cash_h += cash_h
            tot_fx_gain += cash_h - orig_h

        fx_label = f"Gain {format_money(tot_fx_gain, home)}" if tot_fx_gain >= 0 else f"Loss {format_money(-tot_fx_gain, home)}"
        self.preview_lbl.setText(
            f"Payment: {format_money(tot_f, ccy)} ({format_money(tot_cash_h, home)})  |  Realized FX: {fx_label}"
        )

    def _save_payment(self) -> None:
        cust = self.customer_combo.currentData()
        if not cust:
            return
        try:
            rate = parse_rate_to_micros(self.rate_edit.text())
            apps: list[tuple[int, int]] = []
            for idx, inv in enumerate(self._open_invoices):
                chk = self.table.cellWidget(idx, 0)
                amt_w = self.table.cellWidget(idx, 6)
                if isinstance(chk, QCheckBox) and chk.isChecked() and isinstance(amt_w, QLineEdit):
                    app_f = parse_money_to_cents(amt_w.text())
                    apps.append((inv["id"], app_f))
            if not apps:
                raise AccountingError("Check at least one open invoice to apply payment.")

            self.engine.receive_payment(
                customer_id=cust["id"],
                txn_date=self.date_edit.text().strip(),
                applications=apps,
                currency_code=cust["currency_code"],
                exchange_rate_micros=rate,
                deposit_account_id=self.deposit_combo.currentData(),
                memo=self.memo_edit.text().strip(),
            )
            show_info(self, "Payment Recorded", "Customer payment posted to ledger.")
            self.memo_edit.clear()
            self._load_open_invoices()
            self.on_data_changed()
        except Exception as exc:
            show_error(self, "Payment Error", str(exc))
