"""Main Desktop Application Window: Top Menu Bar, Left Icon Nav Bar, Tabbed MDI Workspace."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from accounting.db import DEFAULT_DB_FILENAME, Database
from accounting.engine import AccountingEngine
from accounting.sample_data import load_sample_company
from ui.banking_views import BankReconciliationView, MakeDepositsView
from ui.chart_of_accounts import AccountRegisterView, ChartOfAccountsView
from ui.currency_manager import CurrencyManagerView
from ui.customer_center import (
    CreateInvoiceView,
    CustomerCenterView,
    CustomerInvoicesView,
    ReceivePaymentsView,
)
from ui.home_dashboard import HomeDashboardView
from ui.inventory_center import InventoryCenterView
from ui.journal_entry_view import JournalEntryView
from ui.reports_view import FinancialReportsView
from ui.styles import APP_STYLESHEET, show_error, show_info
from ui.vendor_center import EnterBillsView, PayBillsView, VendorCenterView, WriteChecksView


class CompanySettingsDialog(QDialog):
    def __init__(self, engine: AccountingEngine, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Company Settings")
        self.setMinimumWidth(400)
        s = engine.get_company_settings()

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(s["company_name"])
        self.addr_edit = QLineEdit(s["address"])
        self.fy_edit = QLineEdit(s["fiscal_year_start"])
        self.ccy_edit = QLineEdit(s["home_currency_code"])
        form.addRow("Company Name:", self.name_edit)
        form.addRow("Address:", self.addr_edit)
        form.addRow("Fiscal Year Start (MM-DD):", self.fy_edit)
        form.addRow("Home Currency Code:", self.ccy_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        try:
            self.engine.update_company_settings(
                company_name=self.name_edit.text(),
                address=self.addr_edit.text(),
                fiscal_year_start=self.fy_edit.text().strip() or "01-01",
                home_currency_code=self.ccy_edit.text().strip().upper() or "USD",
            )
            self.accept()
        except Exception as exc:
            show_error(self, "Settings Error", str(exc))


class MainWindow(QMainWindow):
    """QuickBooks Desktop-inspired main window with Top Menu, Left Icon Bar, and Tabbed Workspace."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        super().__init__()
        self.db_path = Path(db_path) if db_path else Path.cwd() / DEFAULT_DB_FILENAME
        self.db = Database(self.db_path)
        self.engine = AccountingEngine(self.db)

        self.setWindowTitle("QuickBooks Desktop Clone — Local-First Double-Entry Accounting")
        self.resize(1360, 860)
        self.setStyleSheet(APP_STYLESHEET)

        self._tab_registry: dict[str, QWidget] = {}
        self._build_menu_bar()
        self._build_body()
        self._build_status_bar()
        self.open_workspace("home")

    def _build_menu_bar(self) -> None:
        mb = self.menuBar()

        # File menu
        file_m = mb.addMenu("&File")
        self._add_menu_action(file_m, "New Company File...", self._new_company_file)
        self._add_menu_action(file_m, "Open Company File...", self._open_company_file)
        self._add_menu_action(file_m, "Backup Company File...", self._backup_company_file)
        file_m.addSeparator()
        self._add_menu_action(file_m, "Load Sample Company", self._load_sample_company)
        file_m.addSeparator()
        self._add_menu_action(file_m, "Exit", self.close)

        # Company menu
        comp_m = mb.addMenu("&Company")
        self._add_menu_action(comp_m, "Home Page", lambda: self.open_workspace("home"))
        self._add_menu_action(comp_m, "Chart of Accounts", lambda: self.open_workspace("coa"))
        self._add_menu_action(comp_m, "Make General Journal Entries", lambda: self.open_workspace("journal_entry"))
        comp_m.addSeparator()
        self._add_menu_action(comp_m, "Company Settings...", self._edit_company_settings)

        # Lists menu
        lists_m = mb.addMenu("&Lists")
        self._add_menu_action(lists_m, "Chart of Accounts", lambda: self.open_workspace("coa"))
        self._add_menu_action(lists_m, "Item & Inventory List", lambda: self.open_workspace("inventory"))
        self._add_menu_action(lists_m, "Currency List", lambda: self.open_workspace("currencies"))

        # Customers menu
        cust_m = mb.addMenu("C&ustomers")
        self._add_menu_action(cust_m, "Customer Center", lambda: self.open_workspace("customers"))
        self._add_menu_action(cust_m, "Customer Invoices List", lambda: self.open_workspace("customer_invoices"))
        self._add_menu_action(cust_m, "Create Invoices", lambda: self.open_workspace("create_invoice"))
        self._add_menu_action(cust_m, "Receive Payments", lambda: self.open_workspace("receive_payments"))

        # Vendors menu
        vend_m = mb.addMenu("&Vendors")
        self._add_menu_action(vend_m, "Vendor Center", lambda: self.open_workspace("vendors"))
        self._add_menu_action(vend_m, "Enter Bills", lambda: self.open_workspace("enter_bills"))
        self._add_menu_action(vend_m, "Pay Bills", lambda: self.open_workspace("pay_bills"))

        # Banking menu
        bank_m = mb.addMenu("&Banking")
        self._add_menu_action(bank_m, "Write Checks / Record Expense", lambda: self.open_workspace("write_checks"))
        self._add_menu_action(bank_m, "Make Deposits", lambda: self.open_workspace("make_deposits"))
        self._add_menu_action(bank_m, "Reconcile...", lambda: self.open_workspace("reconcile"))

        # Reports menu
        rep_m = mb.addMenu("&Reports")
        self._add_menu_action(rep_m, "Profit & Loss Statement", lambda: self.open_workspace("report_pl"))
        self._add_menu_action(rep_m, "Balance Sheet", lambda: self.open_workspace("report_bs"))
        self._add_menu_action(rep_m, "Trial Balance", lambda: self.open_workspace("report_tb"))
        rep_m.addSeparator()
        self._add_menu_action(rep_m, "A/R Aging Summary", lambda: self.open_workspace("report_ar"))
        self._add_menu_action(rep_m, "A/P Aging Summary", lambda: self.open_workspace("report_ap"))
        self._add_menu_action(rep_m, "Inventory Valuation Summary (FIFO)", lambda: self.open_workspace("report_inv"))

    def _add_menu_action(self, menu: Any, label: str, slot: Any) -> None:
        act = QAction(label, self)
        act.triggered.connect(slot)
        menu.addAction(act)

    def _build_body(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Left Icon Navigation Bar
        nav = QWidget()
        nav.setObjectName("LeftNavBar")
        nav.setFixedWidth(195)
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(8, 12, 8, 12)
        nav_layout.setSpacing(4)

        brand = QLabel("QB DESKTOP")
        brand.setStyleSheet("color: #90cdf4; font-size: 14px; font-weight: 800; padding: 6px 10px;")
        nav_layout.addWidget(brand)

        nav_items = [
            ("🏠  Home", "home"),
            ("📚  Chart of Accounts", "coa"),
            ("📦  Items & Inventory", "inventory"),
            ("👥  Customers", "customers"),
            ("📋  Customer Invoices", "customer_invoices"),
            ("🧾  Create Invoice", "create_invoice"),
            ("💰  Receive Payments", "receive_payments"),
            ("🏢  Vendors", "vendors"),
            ("📄  Enter Bills", "enter_bills"),
            ("💸  Pay Bills", "pay_bills"),
            ("✍️  Write Checks", "write_checks"),
            ("🏦  Make Deposits", "make_deposits"),
            ("✅  Reconcile Bank", "reconcile"),
            ("⚖️  Journal Entry", "journal_entry"),
            ("💱  Currency List", "currencies"),
            ("📊  Financial Reports", "report_pl"),
        ]
        for label, key in nav_items:
            btn = QPushButton(label)
            btn.setProperty("class", "NavButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _chk=False, k=key: self.open_workspace(k))
            nav_layout.addWidget(btn)

        nav_layout.addStretch()
        root.addWidget(nav)

        # Tabbed Multi-Document Workspace
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        root.addWidget(self.tabs, stretch=1)

    def _build_status_bar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._update_status_bar()

    def _update_status_bar(self) -> None:
        s = self.engine.get_company_settings()
        self.statusBar().showMessage(
            f"Company: {s['company_name']}  |  Home Currency: {s['home_currency_code']}  |  File: {self.db_path}"
        )

    def _close_tab(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if isinstance(widget, HomeDashboardView):
            return  # Keep Home tab persistent
        for key, w in list(self._tab_registry.items()):
            if w is widget:
                del self._tab_registry[key]
                break
        self.tabs.removeTab(index)
        widget.deleteLater()

    def _broadcast_refresh(self) -> None:
        """Refresh all open workspace tabs after any financial mutation."""
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if hasattr(w, "refresh"):
                w.refresh()
        self._update_status_bar()

    def open_account_register(self, account_id: int, tab_title: str) -> None:
        key = f"register_{account_id}"
        if key in self._tab_registry:
            self.tabs.setCurrentWidget(self._tab_registry[key])
            self._tab_registry[key].refresh()  # type: ignore[attr-defined]
            return
        view = AccountRegisterView(self.engine, account_id)
        self._tab_registry[key] = view
        self.tabs.addTab(view, tab_title)
        self.tabs.setCurrentWidget(view)

    def open_workspace(self, key: str) -> None:
        if key in self._tab_registry:
            w = self._tab_registry[key]
            self.tabs.setCurrentWidget(w)
            if hasattr(w, "refresh"):
                w.refresh()
            return

        factories: dict[str, tuple[str, Any]] = {
            "home": ("Home", lambda: HomeDashboardView(self.engine, self.open_workspace, self._load_sample_company)),
            "coa": ("Chart of Accounts", lambda: ChartOfAccountsView(self.engine, self.open_account_register, self._broadcast_refresh)),
            "currencies": ("Currency List", lambda: CurrencyManagerView(self.engine, self._broadcast_refresh)),
            "inventory": ("Items & Inventory", lambda: InventoryCenterView(self.engine, self._broadcast_refresh)),
            "customers": ("Customer Center", lambda: CustomerCenterView(self.engine, self.open_workspace, self._broadcast_refresh)),
            "customer_invoices": ("Customer Invoices", lambda: CustomerInvoicesView(self.engine, self.open_workspace, self._broadcast_refresh)),
            "create_invoice": ("Create Invoice", lambda: CreateInvoiceView(self.engine, self._broadcast_refresh)),
            "receive_payments": ("Receive Payments", lambda: ReceivePaymentsView(self.engine, self._broadcast_refresh)),
            "vendors": ("Vendor Center", lambda: VendorCenterView(self.engine, self.open_workspace, self._broadcast_refresh)),
            "enter_bills": ("Enter Bills", lambda: EnterBillsView(self.engine, self._broadcast_refresh)),
            "pay_bills": ("Pay Bills", lambda: PayBillsView(self.engine, self._broadcast_refresh)),
            "write_checks": ("Write Checks", lambda: WriteChecksView(self.engine, self._broadcast_refresh)),
            "make_deposits": ("Make Deposits", lambda: MakeDepositsView(self.engine, self._broadcast_refresh)),
            "reconcile": ("Bank Reconciliation", lambda: BankReconciliationView(self.engine, self._broadcast_refresh)),
            "journal_entry": ("Journal Entry", lambda: JournalEntryView(self.engine, self._broadcast_refresh)),
            "report_pl": ("Profit & Loss", lambda: FinancialReportsView(self.engine, "pl")),
            "report_bs": ("Balance Sheet", lambda: FinancialReportsView(self.engine, "bs")),
            "report_tb": ("Trial Balance", lambda: FinancialReportsView(self.engine, "tb")),
            "report_ar": ("A/R Aging", lambda: FinancialReportsView(self.engine, "ar_aging")),
            "report_ap": ("A/P Aging", lambda: FinancialReportsView(self.engine, "ap_aging")),
            "report_inv": ("Inventory Valuation", lambda: FinancialReportsView(self.engine, "inv_val")),
        }

        if key not in factories:
            return
        title, builder = factories[key]
        view = builder()
        self._tab_registry[key] = view
        self.tabs.addTab(view, title)
        self.tabs.setCurrentWidget(view)

    # ------------------------------------------------------------------
    # Company File Operations & Sample Company
    # ------------------------------------------------------------------

    def _switch_database(self, new_path: Path) -> None:
        self.db.close()
        self.db_path = new_path
        self.db = Database(self.db_path)
        self.engine = AccountingEngine(self.db)

        # Close all tabs and reopen Home
        self.tabs.clear()
        self._tab_registry.clear()
        self.open_workspace("home")
        self._update_status_bar()

    def _new_company_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Create New Company File", "new_company.db", "Company Files (*.db *.qbw)"
        )
        if path:
            p = Path(path)
            if p.exists():
                p.unlink()
            self._switch_database(p)
            show_info(self, "Company Created", f"New company file initialized at:\n{p}")

    def _open_company_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Company File", "", "Company Files (*.db *.qbw);;All Files (*)"
        )
        if path:
            self._switch_database(Path(path))

    def _backup_company_file(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"backup_{self.db_path.stem}_{stamp}.db"
        path, _ = QFileDialog.getSaveFileName(
            self, "Backup Company File", default_name, "SQLite Backup (*.db)"
        )
        if path:
            try:
                self.db.backup_to(path)
                show_info(self, "Backup Complete", f"Consistent SQLite backup saved to:\n{path}")
            except Exception as exc:
                show_error(self, "Backup Error", str(exc))

    def _load_sample_company(self) -> None:
        reply = QMessageBox.question(
            self,
            "Load Sample Company",
            "This will reset the current company file and populate Acme Global Trading Co. "
            "with domestic & foreign customers/vendors, multi-batch FIFO inventory, invoices, "
            "and FX bill/invoice payments.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            load_sample_company(self.engine)
            self._broadcast_refresh()
            show_info(
                self,
                "Sample Company Loaded",
                "Acme Global Trading Co. has been loaded!\n\n"
                "Verify in Reports -> Balance Sheet (Assets == Liabilities + Equity) "
                "and Reports -> Inventory Valuation Summary (FIFO Layers == Inventory Asset GL).",
            )
        except Exception as exc:
            show_error(self, "Sample Data Error", str(exc))

    def _edit_company_settings(self) -> None:
        dlg = CompanySettingsDialog(self.engine, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._broadcast_refresh()

    def closeEvent(self, event: Any) -> None:
        self.db.close()
        super().closeEvent(event)
