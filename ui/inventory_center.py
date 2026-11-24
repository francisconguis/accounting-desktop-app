"""Items & Inventory Center with Live FIFO Valuation & Layer Breakdown."""

from __future__ import annotations

from datetime import date
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
    QSpinBox,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from accounting.engine import AccountingEngine
from accounting.models import ITEM_TYPES
from accounting.money import format_money, parse_money_to_cents
from ui.styles import make_readonly_item, show_error, style_table_defaults


class ItemDialog(QDialog):
    def __init__(
        self,
        engine: AccountingEngine,
        existing: Optional[dict[str, Any]] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.existing = existing
        self.setWindowTitle("Edit Item" if existing else "New Item")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.sku_edit = QLineEdit()
        self.name_edit = QLineEdit()
        self.type_combo = QComboBox()
        self.type_combo.addItems(ITEM_TYPES)
        self.desc_edit = QLineEdit()
        self.sales_price_edit = QLineEdit("0.00")
        self.purchase_cost_edit = QLineEdit("0.00")

        self.income_combo = QComboBox()
        self.expense_combo = QComboBox()
        self.asset_combo = QComboBox()
        self.asset_combo.addItem("(None)", None)

        for a in engine.list_accounts():
            label = f"{a['account_number']} {a['name']}"
            if a["account_type"] == "Income":
                self.income_combo.addItem(label, a["id"])
            elif a["account_type"] == "Expense":
                self.expense_combo.addItem(label, a["id"])
            elif a["account_type"] == "Asset":
                self.asset_combo.addItem(label, a["id"])

        if existing:
            self.sku_edit.setText(existing["sku"])
            self.name_edit.setText(existing["name"])
            self.type_combo.setCurrentText(existing["type"])
            self.desc_edit.setText(existing["description"])
            self.sales_price_edit.setText(format_money(existing["sales_price"], show_symbol=False))
            self.purchase_cost_edit.setText(format_money(existing["purchase_cost"], show_symbol=False))
            for combo, key in (
                (self.income_combo, "income_account_id"),
                (self.expense_combo, "expense_account_id"),
                (self.asset_combo, "asset_account_id"),
            ):
                idx = combo.findData(existing[key])
                if idx >= 0:
                    combo.setCurrentIndex(idx)
        else:
            # Default to Inventory Asset for inventory items
            try:
                inv_id = engine.get_account_by_detail_type("Inventory Asset")["id"]
                idx = self.asset_combo.findData(inv_id)
                if idx >= 0:
                    self.asset_combo.setCurrentIndex(idx)
            except Exception:
                pass

        form.addRow("SKU:", self.sku_edit)
        form.addRow("Item Name:", self.name_edit)
        form.addRow("Item Type:", self.type_combo)
        form.addRow("Description:", self.desc_edit)
        form.addRow("Sales Price (Home):", self.sales_price_edit)
        form.addRow("Purchase Cost (Home):", self.purchase_cost_edit)
        form.addRow("Income Account:", self.income_combo)
        form.addRow("COGS / Expense Account:", self.expense_combo)
        form.addRow("Inventory Asset Account:", self.asset_combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        try:
            sp = parse_money_to_cents(self.sales_price_edit.text())
            pc = parse_money_to_cents(self.purchase_cost_edit.text())
            item_type = self.type_combo.currentText()
            asset_id = self.asset_combo.currentData() if item_type == "Inventory" else None
            if self.existing:
                self.engine.update_item(
                    item_id=self.existing["id"],
                    sku=self.sku_edit.text(),
                    name=self.name_edit.text(),
                    item_type=item_type,
                    description=self.desc_edit.text(),
                    sales_price=sp,
                    purchase_cost=pc,
                    income_account_id=self.income_combo.currentData(),
                    expense_account_id=self.expense_combo.currentData(),
                    asset_account_id=asset_id,
                )
            else:
                self.engine.create_item(
                    sku=self.sku_edit.text(),
                    name=self.name_edit.text(),
                    item_type=item_type,
                    description=self.desc_edit.text(),
                    sales_price=sp,
                    purchase_cost=pc,
                    income_account_id=self.income_combo.currentData(),
                    expense_account_id=self.expense_combo.currentData(),
                    asset_account_id=asset_id,
                )
            self.accept()
        except Exception as exc:
            show_error(self, "Item Error", str(exc))


class InventoryAdjustDialog(QDialog):
    """Dialog to record a positive or negative FIFO inventory quantity adjustment."""

    def __init__(self, engine: AccountingEngine, item: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.item = item
        self.setWindowTitle(f"Adjust Inventory: {item['sku']} - {item['name']}")
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.date_edit = QLineEdit(date.today().isoformat())
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(-100_000, 100_000)
        self.qty_spin.setValue(1)
        self.cost_edit = QLineEdit(format_money(item["purchase_cost"], show_symbol=False))
        self.memo_edit = QLineEdit("Manual inventory count adjustment")

        form.addRow("Date (YYYY-MM-DD):", self.date_edit)
        form.addRow("Qty Delta (+ add / - remove):", self.qty_spin)
        form.addRow("Unit Cost (Home, for + delta):", self.cost_edit)
        form.addRow("Memo:", self.memo_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        try:
            delta = self.qty_spin.value()
            cost = parse_money_to_cents(self.cost_edit.text()) if delta > 0 else None
            self.engine.adjust_inventory(
                item_id=self.item["id"],
                txn_date=self.date_edit.text().strip(),
                qty_delta=delta,
                unit_cost_home=cost,
                memo=self.memo_edit.text().strip(),
            )
            self.accept()
        except Exception as exc:
            show_error(self, "Adjustment Error", str(exc))


class InventoryCenterView(QWidget):
    """Items & Inventory Center with live Qty on Hand, Avg FIFO Cost, Asset Value, and FIFO Layer breakdown."""

    def __init__(
        self,
        engine: AccountingEngine,
        on_data_changed: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.on_data_changed = on_data_changed
        self._items: list[dict[str, Any]] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        title = QLabel("Items & FIFO Inventory Center")
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #1a365d;")
        top.addWidget(title)
        top.addStretch()

        self.reconcile_lbl = QLabel("")
        self.reconcile_lbl.setStyleSheet("font-weight: 600; color: #2f855a;")
        top.addWidget(self.reconcile_lbl)

        new_btn = QPushButton("+ New Item")
        new_btn.setProperty("class", "PrimaryButton")
        new_btn.clicked.connect(self._add_item)
        top.addWidget(new_btn)

        edit_btn = QPushButton("Edit Selected")
        edit_btn.clicked.connect(self._edit_item)
        top.addWidget(edit_btn)

        adj_btn = QPushButton("Adjust Inventory Qty")
        adj_btn.clicked.connect(self._adjust_selected)
        top.addWidget(adj_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        top.addWidget(refresh_btn)
        layout.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # Top table: Items list
        self.items_table = QTableWidget(0, 7)
        self.items_table.setHorizontalHeaderLabels(
            ["SKU", "Name", "Type", "Sales Price", "Qty On Hand", "Avg FIFO Cost", "Total Asset Value"]
        )
        style_table_defaults(self.items_table)
        self.items_table.itemSelectionChanged.connect(self._refresh_layers)
        splitter.addWidget(self.items_table)

        # Bottom panel: FIFO Layers for selected item (or all active layers)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 6, 0, 0)
        self.layers_lbl = QLabel("Active FIFO Cost Layers (Ordered Oldest -> Newest)")
        self.layers_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #1a365d;")
        bl.addWidget(self.layers_lbl)

        self.layers_table = QTableWidget(0, 7)
        self.layers_table.setHorizontalHeaderLabels(
            ["Layer ID", "SKU", "Received Date", "Source Txn", "Initial Qty", "Qty Remaining", "Unit Cost / Remaining Value"]
        )
        style_table_defaults(self.layers_table)
        bl.addWidget(self.layers_table)
        splitter.addWidget(bottom)

        splitter.setSizes([340, 220])
        layout.addWidget(splitter)

    def refresh(self) -> None:
        home = self.engine.get_home_currency()
        self._items = self.engine.list_items()
        self.items_table.setRowCount(len(self._items))
        total_fifo = 0
        for idx, it in enumerate(self._items):
            val = int(it["total_asset_value_home"])
            total_fifo += val
            self.items_table.setItem(idx, 0, make_readonly_item(it["sku"]))
            self.items_table.setItem(idx, 1, make_readonly_item(it["name"]))
            self.items_table.setItem(idx, 2, make_readonly_item(it["type"]))
            self.items_table.setItem(idx, 3, make_readonly_item(format_money(it["sales_price"], home), align_right=True))
            self.items_table.setItem(
                idx, 4, make_readonly_item(str(it["qty_on_hand"]) if it["type"] == "Inventory" else "-", align_right=True)
            )
            self.items_table.setItem(
                idx,
                5,
                make_readonly_item(
                    format_money(it["avg_fifo_cost_home"], home) if it["type"] == "Inventory" else "-",
                    align_right=True,
                ),
            )
            self.items_table.setItem(
                idx,
                6,
                make_readonly_item(
                    format_money(val, home) if it["type"] == "Inventory" else "-",
                    align_right=True,
                ),
            )
        self.items_table.resizeColumnsToContents()

        inv_acct = self.engine.get_account_by_detail_type("Inventory Asset")
        _, gl_val = self.engine.get_account_balance(inv_acct["id"])
        status_ok = total_fifo == gl_val
        self.reconcile_lbl.setText(
            f"FIFO Total: {format_money(total_fifo, home)} | GL Asset: {format_money(gl_val, home)} "
            f"({'✔ Reconciled' if status_ok else '⚠ Mismatch'})"
        )
        self.reconcile_lbl.setStyleSheet(
            f"font-weight: 600; color: {'#2f855a' if status_ok else '#c53030'};"
        )
        self._refresh_layers()

    def _refresh_layers(self) -> None:
        home = self.engine.get_home_currency()
        row = self.items_table.currentRow()
        item_id = None
        if 0 <= row < len(self._items) and self._items[row]["type"] == "Inventory":
            item_id = self._items[row]["id"]
            self.layers_lbl.setText(
                f"Active FIFO Cost Layers for {self._items[row]['sku']} - {self._items[row]['name']}"
            )
        else:
            self.layers_lbl.setText("All Active FIFO Cost Layers (Ordered Oldest -> Newest)")

        layers = self.engine.get_item_fifo_layers(item_id=item_id, only_remaining=True)
        self.layers_table.setRowCount(len(layers))
        for idx, l in enumerate(layers):
            self.layers_table.setItem(idx, 0, make_readonly_item(str(l["id"])))
            self.layers_table.setItem(idx, 1, make_readonly_item(l["sku"]))
            self.layers_table.setItem(idx, 2, make_readonly_item(l["received_date"]))
            src = f"{l['purchase_txn_type'] or ''} {l['purchase_txn_number'] or ''}".strip()
            self.layers_table.setItem(idx, 3, make_readonly_item(src))
            self.layers_table.setItem(idx, 4, make_readonly_item(str(l["initial_qty"]), align_right=True))
            self.layers_table.setItem(idx, 5, make_readonly_item(str(l["qty_remaining"]), align_right=True))
            val_str = f"{format_money(l['unit_cost_home'], home)} ea  →  {format_money(l['remaining_value_home'], home)}"
            self.layers_table.setItem(idx, 6, make_readonly_item(val_str, align_right=True))
        self.layers_table.resizeColumnsToContents()

    def _add_item(self) -> None:
        dlg = ItemDialog(self.engine, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.on_data_changed()

    def _edit_item(self) -> None:
        row = self.items_table.currentRow()
        if 0 <= row < len(self._items):
            dlg = ItemDialog(self.engine, existing=self._items[row], parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.refresh()
                self.on_data_changed()

    def _adjust_selected(self) -> None:
        row = self.items_table.currentRow()
        if row < 0 or row >= len(self._items):
            show_error(self, "Select Item", "Please select an Inventory item first.")
            return
        item = self._items[row]
        if item["type"] != "Inventory":
            show_error(self, "Not Inventory", "Only Inventory items have FIFO layers to adjust.")
            return
        dlg = InventoryAdjustDialog(self.engine, item, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.on_data_changed()
