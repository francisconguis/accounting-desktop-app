"""Chart of Accounts (Tree-View Table) + Add/Edit Account Dialog + Checkbook Account Register."""

from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from accounting.engine import AccountingEngine, AccountingError
from accounting.models import ACCOUNT_TYPES, DETAIL_TYPES_BY_ACCOUNT_TYPE
from accounting.money import format_money
from ui.styles import make_readonly_item, show_error, style_table_defaults


class AccountDialog(QDialog):
    """Modal dialog to create or edit a Chart of Accounts entry."""

    def __init__(
        self,
        engine: AccountingEngine,
        account: Optional[dict[str, Any]] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.account = account
        self.setWindowTitle("Edit Account" if account else "New Account")
        self.setMinimumWidth(400)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.num_edit = QLineEdit()
        self.name_edit = QLineEdit()
        self.type_combo = QComboBox()
        self.type_combo.addItems(ACCOUNT_TYPES)
        self.detail_combo = QComboBox()
        self.currency_combo = QComboBox()
        for c in self.engine.list_currencies():
            self.currency_combo.addItem(f"{c['code']} - {c['name']}", c["code"])

        self.parent_combo = QComboBox()
        self.parent_combo.addItem("(None - Top Level)", None)
        for a in self.engine.list_accounts():
            if self.account and a["id"] == self.account["id"]:
                continue
            self.parent_combo.addItem(f"{a['account_number']} {a['name']}", a["id"])

        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        self._on_type_changed(self.type_combo.currentText())

        if self.account:
            self.num_edit.setText(self.account["account_number"])
            self.name_edit.setText(self.account["name"])
            self.type_combo.setCurrentText(self.account["account_type"])
            self._on_type_changed(self.account["account_type"])
            idx_d = self.detail_combo.findText(self.account["detail_type"])
            if idx_d >= 0:
                self.detail_combo.setCurrentIndex(idx_d)
            idx_c = self.currency_combo.findData(self.account["currency_code"])
            if idx_c >= 0:
                self.currency_combo.setCurrentIndex(idx_c)
            idx_p = self.parent_combo.findData(self.account["parent_account_id"])
            if idx_p >= 0:
                self.parent_combo.setCurrentIndex(idx_p)
        else:
            home = self.engine.get_home_currency()
            idx_c = self.currency_combo.findData(home)
            if idx_c >= 0:
                self.currency_combo.setCurrentIndex(idx_c)

        form.addRow("Account Number:", self.num_edit)
        form.addRow("Account Name:", self.name_edit)
        form.addRow("Account Type:", self.type_combo)
        form.addRow("Detail Type:", self.detail_combo)
        form.addRow("Currency:", self.currency_combo)
        form.addRow("Sub-Account Of:", self.parent_combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_type_changed(self, acct_type: str) -> None:
        self.detail_combo.clear()
        self.detail_combo.addItems(DETAIL_TYPES_BY_ACCOUNT_TYPE.get(acct_type, []))

    def _save(self) -> None:
        try:
            num = self.num_edit.text().strip()
            name = self.name_edit.text().strip()
            if not num or not name:
                raise AccountingError("Account Number and Name are required.")
            if self.account:
                self.engine.update_account(
                    account_id=self.account["id"],
                    account_number=num,
                    name=name,
                    account_type=self.type_combo.currentText(),
                    detail_type=self.detail_combo.currentText(),
                    currency_code=self.currency_combo.currentData(),
                    parent_account_id=self.parent_combo.currentData(),
                )
            else:
                self.engine.create_account(
                    account_number=num,
                    name=name,
                    account_type=self.type_combo.currentText(),
                    detail_type=self.detail_combo.currentText(),
                    currency_code=self.currency_combo.currentData(),
                    parent_account_id=self.parent_combo.currentData(),
                )
            self.accept()
        except Exception as exc:
            show_error(self, "Account Error", str(exc))


class AccountRegisterView(QWidget):
    """Checkbook-style Account Register showing running balances."""

    def __init__(self, engine: AccountingEngine, account_id: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.account_id = account_id
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.title_lbl = QLabel("Account Register")
        self.title_lbl.setStyleSheet("font-size: 16px; font-weight: 700; color: #1a365d;")
        top.addWidget(self.title_lbl)
        top.addStretch()
        self.bal_lbl = QLabel("")
        self.bal_lbl.setStyleSheet("font-size: 14px; font-weight: 700; color: #2b6cb0;")
        top.addWidget(self.bal_lbl)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            ["Date", "Ref #", "Type", "Payee / Name", "Memo", "Clr", "Debit", "Credit", "Balance"]
        )
        style_table_defaults(self.table)
        layout.addWidget(self.table)

    def refresh(self) -> None:
        acct = self.engine.get_account(self.account_id)
        home_ccy = self.engine.get_home_currency()
        rows = self.engine.get_account_register(self.account_id)

        self.title_lbl.setText(
            f"Register: {acct['account_number']} {acct['name']} ({acct['account_type']} / {acct['currency_code']})"
        )
        _, bal_home = self.engine.get_account_balance(self.account_id)
        self.bal_lbl.setText(f"Ending Balance: {format_money(bal_home, home_ccy)}")

        self.table.setRowCount(len(rows))
        for r_idx, row in enumerate(rows):
            self.table.setItem(r_idx, 0, make_readonly_item(row["entry_date"]))
            self.table.setItem(r_idx, 1, make_readonly_item(row["reference_no"]))
            self.table.setItem(r_idx, 2, make_readonly_item(row["txn_type"]))
            self.table.setItem(r_idx, 3, make_readonly_item(row["entity_name"] or ""))
            self.table.setItem(r_idx, 4, make_readonly_item(row["memo"] or ""))
            self.table.setItem(r_idx, 5, make_readonly_item("✔" if row["is_reconciled"] else ""))
            self.table.setItem(
                r_idx,
                6,
                make_readonly_item(
                    format_money(row["debit_home"], home_ccy) if row["debit_home"] else "",
                    align_right=True,
                ),
            )
            self.table.setItem(
                r_idx,
                7,
                make_readonly_item(
                    format_money(row["credit_home"], home_ccy) if row["credit_home"] else "",
                    align_right=True,
                ),
            )
            self.table.setItem(
                r_idx,
                8,
                make_readonly_item(format_money(row["running_balance_home"], home_ccy), align_right=True),
            )
        self.table.resizeColumnsToContents()


class ChartOfAccountsView(QWidget):
    """Tree-view Chart of Accounts; double-clicking any Balance Sheet account opens its Register."""

    def __init__(
        self,
        engine: AccountingEngine,
        on_open_register: Callable[[int, str], None],
        on_data_changed: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.on_open_register = on_open_register
        self.on_data_changed = on_data_changed
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        bar = QHBoxLayout()
        title = QLabel("Chart of Accounts")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #1a365d;")
        bar.addWidget(title)
        bar.addStretch()

        hint = QLabel("Double-click any account to open its Register")
        hint.setStyleSheet("color: #718096; font-size: 11px;")
        bar.addWidget(hint)

        new_btn = QPushButton("+ New Account")
        new_btn.setProperty("class", "PrimaryButton")
        new_btn.clicked.connect(self._add_account)
        bar.addWidget(new_btn)

        edit_btn = QPushButton("Edit Selected")
        edit_btn.clicked.connect(self._edit_selected)
        bar.addWidget(edit_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(refresh_btn)
        layout.addLayout(bar)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(
            ["Account #", "Name", "Type", "Detail Type", "Currency", "Foreign Balance", "Home Balance"]
        )
        self.tree.setAlternatingRowColors(True)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self.tree)

    def refresh(self) -> None:
        self.tree.clear()
        home_ccy = self.engine.get_home_currency()
        accounts = self.engine.list_accounts()

        nodes_by_id: dict[int, QTreeWidgetItem] = {}
        pending: list[dict[str, Any]] = []

        def make_node(a: dict[str, Any]) -> QTreeWidgetItem:
            foreign_str = (
                format_money(a["balance_foreign"], a["currency_code"])
                if a["currency_code"] != home_ccy
                else "-"
            )
            home_str = format_money(a["balance_home"], home_ccy)
            item = QTreeWidgetItem(
                [
                    a["account_number"],
                    a["name"],
                    a["account_type"],
                    a["detail_type"],
                    a["currency_code"],
                    foreign_str,
                    home_str,
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, a)
            item.setTextAlignment(5, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setTextAlignment(6, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return item

        for a in accounts:
            if a["parent_account_id"] is None:
                node = make_node(a)
                self.tree.addTopLevelItem(node)
                nodes_by_id[a["id"]] = node
            else:
                pending.append(a)

        for a in pending:
            node = make_node(a)
            parent = nodes_by_id.get(a["parent_account_id"])
            if parent is not None:
                parent.addChild(node)
            else:
                self.tree.addTopLevelItem(node)
            nodes_by_id[a["id"]] = node

        self.tree.expandAll()
        for col in range(7):
            self.tree.resizeColumnToContents(col)

    def _add_account(self) -> None:
        dlg = AccountDialog(self.engine, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.on_data_changed()

    def _edit_selected(self) -> None:
        item = self.tree.currentItem()
        if not item:
            return
        acct = item.data(0, Qt.ItemDataRole.UserRole)
        dlg = AccountDialog(self.engine, account=acct, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.on_data_changed()

    def _on_double_click(self, item: QTreeWidgetItem, _col: int) -> None:
        acct = item.data(0, Qt.ItemDataRole.UserRole)
        if acct:
            self.on_open_register(acct["id"], f"Register: {acct['name']}")
