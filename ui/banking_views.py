"""Banking Modules: Make Deposits (Undeposited Funds -> Checking) & Bank Reconciliation."""

from __future__ import annotations

from datetime import date
from typing import Any, Callable

from PySide6.QtWidgets import (
    QCheckBox,
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
from accounting.money import format_money, parse_money_to_cents
from ui.styles import make_readonly_item, show_error, show_info, style_table_defaults


class MakeDepositsView(QWidget):
    """Move funds from Undeposited Funds into Checking / Savings."""

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

        hdr = QLabel("Make Bank Deposit (Undeposited Funds → Checking)")
        hdr.setStyleSheet("font-size: 16px; font-weight: 700; color: #1a365d;")
        layout.addWidget(hdr)

        self.avail_lbl = QLabel("Undeposited Funds Available: $0.00")
        self.avail_lbl.setStyleSheet("font-size: 14px; font-weight: 700; color: #2b6cb0;")
        layout.addWidget(self.avail_lbl)

        form = QFormLayout()
        self.target_combo = QComboBox()
        self.date_edit = QLineEdit(date.today().isoformat())
        self.amount_edit = QLineEdit("0.00")
        self.memo_edit = QLineEdit("Bank deposit from Undeposited Funds")

        form.addRow("Deposit To Account:", self.target_combo)
        form.addRow("Deposit Date:", self.date_edit)
        form.addRow("Deposit Amount (Home):", self.amount_edit)
        form.addRow("Memo:", self.memo_edit)
        layout.addLayout(form)

        row = QHBoxLayout()
        fill_btn = QPushButton("Fill Full Undeposited Balance")
        fill_btn.clicked.connect(self._fill_full)
        row.addWidget(fill_btn)
        row.addStretch()

        save_btn = QPushButton("Record Deposit")
        save_btn.setProperty("class", "SuccessButton")
        save_btn.clicked.connect(self._save)
        row.addWidget(save_btn)
        layout.addLayout(row)
        layout.addStretch()

    def refresh(self) -> None:
        home = self.engine.get_home_currency()
        uf = self.engine.get_account_by_detail_type("Undeposited Funds")
        _, self._avail_home = self.engine.get_account_balance(uf["id"])
        self.avail_lbl.setText(f"Undeposited Funds Available: {format_money(self._avail_home, home)}")

        self.target_combo.clear()
        for a in self.engine.list_accounts(account_types=("Asset",)):
            if a["detail_type"] in ("Checking", "Savings"):
                self.target_combo.addItem(f"{a['account_number']} {a['name']}", a["id"])

    def _fill_full(self) -> None:
        self.amount_edit.setText(format_money(self._avail_home, show_symbol=False))

    def _save(self) -> None:
        try:
            amt = parse_money_to_cents(self.amount_edit.text())
            self.engine.make_deposit(
                target_bank_account_id=self.target_combo.currentData(),
                txn_date=self.date_edit.text().strip(),
                amount_home=amt,
                memo=self.memo_edit.text().strip(),
            )
            show_info(self, "Deposit Recorded", "Deposit posted to ledger.")
            self.amount_edit.setText("0.00")
            self.refresh()
            self.on_data_changed()
        except Exception as exc:
            show_error(self, "Deposit Error", str(exc))


class BankReconciliationView(QWidget):
    """Bank Reconciliation tool: clear items until Difference == $0.00, then commit."""

    def __init__(
        self,
        engine: AccountingEngine,
        on_data_changed: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.on_data_changed = on_data_changed
        self._state: dict[str, Any] = {}
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        hdr = QLabel("Bank Reconciliation")
        hdr.setStyleSheet("font-size: 16px; font-weight: 700; color: #1a365d;")
        layout.addWidget(hdr)

        top = QHBoxLayout()
        f1 = QFormLayout()
        f2 = QFormLayout()

        self.acct_combo = QComboBox()
        self.acct_combo.currentIndexChanged.connect(self._load_account_state)
        self.stmt_date_edit = QLineEdit(date.today().isoformat())
        self.stmt_bal_edit = QLineEdit("0.00")
        self.stmt_bal_edit.textChanged.connect(self._recalc_diff)
        self.beg_bal_lbl = QLabel("$0.00")
        self.beg_bal_lbl.setStyleSheet("font-weight: 700;")

        f1.addRow("Account to Reconcile:", self.acct_combo)
        f1.addRow("Beginning Reconciled Balance:", self.beg_bal_lbl)
        f2.addRow("Statement Ending Date:", self.stmt_date_edit)
        f2.addRow("Statement Ending Balance:", self.stmt_bal_edit)
        top.addLayout(f1)
        top.addLayout(f2)
        layout.addLayout(top)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Cleared", "Date", "Ref #", "Type", "Memo", "Deposit (+)", "Payment (-)"]
        )
        style_table_defaults(self.table)
        layout.addWidget(self.table)

        footer = QHBoxLayout()
        self.summary_lbl = QLabel("Cleared Balance: $0.00  |  Difference: $0.00")
        self.summary_lbl.setStyleSheet("font-size: 14px; font-weight: 700;")
        footer.addWidget(self.summary_lbl)
        footer.addStretch()

        self.reconcile_btn = QPushButton("Reconcile Now")
        self.reconcile_btn.setProperty("class", "SuccessButton")
        self.reconcile_btn.setEnabled(False)
        self.reconcile_btn.clicked.connect(self._commit_reconciliation)
        footer.addWidget(self.reconcile_btn)
        layout.addLayout(footer)

    def refresh(self) -> None:
        self.acct_combo.blockSignals(True)
        prev_id = self.acct_combo.currentData()
        self.acct_combo.clear()
        for a in self.engine.list_accounts():
            if a["detail_type"] in ("Checking", "Savings", "Credit Card"):
                self.acct_combo.addItem(f"{a['account_number']} {a['name']}", a["id"])
        if prev_id is not None:
            idx = self.acct_combo.findData(prev_id)
            if idx >= 0:
                self.acct_combo.setCurrentIndex(idx)
        self.acct_combo.blockSignals(False)
        self._load_account_state()

    def _load_account_state(self) -> None:
        acct_id = self.acct_combo.currentData()
        if acct_id is None:
            return
        home = self.engine.get_home_currency()
        self._state = self.engine.get_reconciliation_state(acct_id)
        beg = int(self._state["beginning_balance_home"])
        self.beg_bal_lbl.setText(format_money(beg, home))

        lines = self._state["uncleared_lines"]
        self.table.setRowCount(len(lines))
        for idx, l in enumerate(lines):
            chk = QCheckBox()
            chk.stateChanged.connect(self._recalc_diff)
            self.table.setCellWidget(idx, 0, chk)
            self.table.setItem(idx, 1, make_readonly_item(l["entry_date"]))
            self.table.setItem(idx, 2, make_readonly_item(l["reference_no"]))
            self.table.setItem(idx, 3, make_readonly_item(l["txn_type"]))
            self.table.setItem(idx, 4, make_readonly_item(l["memo"] or ""))
            self.table.setItem(
                idx,
                5,
                make_readonly_item(format_money(l["debit_home"], home) if l["debit_home"] else "", align_right=True),
            )
            self.table.setItem(
                idx,
                6,
                make_readonly_item(format_money(l["credit_home"], home) if l["credit_home"] else "", align_right=True),
            )
        self.table.resizeColumnsToContents()
        self._recalc_diff()

    def _recalc_diff(self) -> None:
        if not self._state:
            return
        home = self.engine.get_home_currency()
        beg = int(self._state["beginning_balance_home"])
        lines = self._state["uncleared_lines"]

        try:
            target = parse_money_to_cents(self.stmt_bal_edit.text())
        except Exception:
            self.reconcile_btn.setEnabled(False)
            return

        cleared_sum = 0
        for idx, l in enumerate(lines):
            chk = self.table.cellWidget(idx, 0)
            if isinstance(chk, QCheckBox) and chk.isChecked():
                cleared_sum += int(l["signed_home"])

        cleared_bal = beg + cleared_sum
        diff = target - cleared_bal
        ok = diff == 0
        self.summary_lbl.setText(
            f"Cleared Balance: {format_money(cleared_bal, home)}  |  Difference: {format_money(diff, home)}"
        )
        self.summary_lbl.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {'#2f855a' if ok else '#c53030'};"
        )
        self.reconcile_btn.setEnabled(ok)

    def _commit_reconciliation(self) -> None:
        acct_id = self.acct_combo.currentData()
        if acct_id is None:
            return
        try:
            target = parse_money_to_cents(self.stmt_bal_edit.text())
            cleared_ids = []
            for idx, l in enumerate(self._state["uncleared_lines"]):
                chk = self.table.cellWidget(idx, 0)
                if isinstance(chk, QCheckBox) and chk.isChecked():
                    cleared_ids.append(int(l["jel_id"]))
            self.engine.reconcile_bank_account(
                account_id=acct_id,
                statement_date=self.stmt_date_edit.text().strip(),
                statement_ending_balance_home=target,
                cleared_jel_ids=cleared_ids,
            )
            show_info(self, "Reconciled", f"Account reconciled cleanly ({len(cleared_ids)} items marked cleared).")
            self._load_account_state()
            self.on_data_changed()
        except Exception as exc:
            show_error(self, "Reconciliation Error", str(exc))
