"""Home Dashboard with Classic QuickBooks Desktop Visual Workflow Flowchart & KPI Sidebar."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from accounting.engine import AccountingEngine
from accounting.money import format_money


class HomeDashboardView(QWidget):
    """Interactive visual workflow map (Vendors, Customers, Banking & Inventory) + KPI sidebar."""

    def __init__(
        self,
        engine: AccountingEngine,
        open_action: Callable[[str], None],
        on_load_sample: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.open_action = open_action
        self.on_load_sample = on_load_sample
        self._build_ui()
        self.refresh()

    def _flow_btn(self, label: str, action_key: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setProperty("class", "FlowNode")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda: self.open_action(action_key))
        return btn

    def _arrow(self) -> QLabel:
        lbl = QLabel("  ➔  ")
        lbl.setStyleSheet("font-size: 18px; color: #2b6cb0; font-weight: bold;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lbl

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(16)

        # Left: Workflow Flowchart inside a ScrollArea
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        flow_container = QWidget()
        flow_layout = QVBoxLayout(flow_container)
        flow_layout.setSpacing(14)

        # Header + Load Sample Company CTA
        header_row = QHBoxLayout()
        self.company_title = QLabel("Company Home")
        self.company_title.setStyleSheet("font-size: 20px; font-weight: 700; color: #1a365d;")
        header_row.addWidget(self.company_title)
        header_row.addStretch()

        sample_btn = QPushButton("Load Sample Company")
        sample_btn.setProperty("class", "SuccessButton")
        sample_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sample_btn.clicked.connect(self.on_load_sample)
        header_row.addWidget(sample_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        header_row.addWidget(refresh_btn)
        flow_layout.addLayout(header_row)

        # 1. VENDORS FLOW
        vendors_box = QGroupBox("VENDORS (Accounts Payable & Purchasing)")
        v_row = QHBoxLayout(vendors_box)
        v_row.addWidget(self._flow_btn("🏢\nVendor Center", "vendors"))
        v_row.addWidget(self._arrow())
        v_row.addWidget(self._flow_btn("📄\nEnter Bills", "enter_bills"))
        v_row.addWidget(self._arrow())
        v_row.addWidget(self._flow_btn("💸\nPay Bills", "pay_bills"))
        v_row.addStretch()
        flow_layout.addWidget(vendors_box)

        # 2. CUSTOMERS FLOW
        customers_box = QGroupBox("CUSTOMERS (Accounts Receivable & Sales)")
        c_row = QHBoxLayout(customers_box)
        c_row.addWidget(self._flow_btn("👥\nCustomer Center", "customers"))
        c_row.addWidget(self._arrow())
        c_row.addWidget(self._flow_btn("🧾\nCreate Invoices", "create_invoice"))
        c_row.addWidget(self._arrow())
        c_row.addWidget(self._flow_btn("💰\nReceive Payments", "receive_payments"))
        c_row.addWidget(self._arrow())
        c_row.addWidget(self._flow_btn("🏦\nRecord Deposits", "make_deposits"))
        c_row.addStretch()
        flow_layout.addWidget(customers_box)

        # 3. INVENTORY & COMPANY FLOW
        inv_box = QGroupBox("INVENTORY & COMPANY ACTIVITIES")
        i_row = QHBoxLayout(inv_box)
        i_row.addWidget(self._flow_btn("📦\nItems & Inventory", "inventory"))
        i_row.addWidget(self._flow_btn("📚\nChart of Accounts", "coa"))
        i_row.addWidget(self._flow_btn("💱\nCurrency List", "currencies"))
        i_row.addWidget(self._flow_btn("⚖️\nJournal Entries", "journal_entry"))
        i_row.addStretch()
        flow_layout.addWidget(inv_box)

        # 4. BANKING & REPORTS FLOW
        bank_box = QGroupBox("BANKING & FINANCIAL REPORTS")
        b_row = QHBoxLayout(bank_box)
        b_row.addWidget(self._flow_btn("✍️\nWrite Checks", "write_checks"))
        b_row.addWidget(self._flow_btn("✅\nReconcile Bank", "reconcile"))
        b_row.addWidget(self._flow_btn("📊\nProfit & Loss", "report_pl"))
        b_row.addWidget(self._flow_btn("🏛️\nBalance Sheet", "report_bs"))
        b_row.addStretch()
        flow_layout.addWidget(bank_box)

        flow_layout.addStretch()
        scroll.setWidget(flow_container)
        root.addWidget(scroll, stretch=3)

        # Right Sidebar: Bank Balances & KPIs
        sidebar = QFrame()
        sidebar.setFixedWidth(310)
        sidebar.setStyleSheet("QFrame { background-color: #ffffff; border: 1px solid #cbd5e0; border-radius: 6px; }")
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(14, 14, 14, 14)
        side_layout.setSpacing(12)

        bank_hdr = QLabel("Account Balances")
        bank_hdr.setStyleSheet("font-size: 15px; font-weight: 700; color: #1a365d; border: none;")
        side_layout.addWidget(bank_hdr)

        self.bank_list_layout = QVBoxLayout()
        self.bank_list_layout.setSpacing(6)
        side_layout.addLayout(self.bank_list_layout)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #e2e8f0;")
        side_layout.addWidget(sep)

        kpi_hdr = QLabel("Company Snapshot (KPIs)")
        kpi_hdr.setStyleSheet("font-size: 15px; font-weight: 700; color: #1a365d; border: none;")
        side_layout.addWidget(kpi_hdr)

        self.kpi_open_ar = self._make_kpi_label("Open Invoices (A/R)")
        self.kpi_overdue_ap = self._make_kpi_label("Overdue Bills (A/P)")
        self.kpi_inventory = self._make_kpi_label("Inventory Asset Value")
        self.kpi_net_income = self._make_kpi_label("Net Income (YTD)")

        for card in (self.kpi_open_ar, self.kpi_overdue_ap, self.kpi_inventory, self.kpi_net_income):
            side_layout.addWidget(card)

        side_layout.addStretch()
        root.addWidget(sidebar, stretch=0)

    def _make_kpi_label(self, title: str) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background-color: #f7fafc; border: 1px solid #e2e8f0; border-radius: 5px; padding: 8px; }"
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("font-size: 11px; color: #4a5568; border: none;")
        v = QLabel("$0.00")
        v.setStyleSheet("font-size: 16px; font-weight: 700; color: #1a365d; border: none;")
        lay.addWidget(t)
        lay.addWidget(v)
        frame._value_label = v  # type: ignore[attr-defined]
        return frame

    def refresh(self) -> None:
        settings = self.engine.get_company_settings()
        summary = self.engine.get_dashboard_summary()
        home_ccy = summary["home_currency"]
        self.company_title.setText(f"{settings['company_name']}  ({home_ccy})")

        # Clear bank list
        while self.bank_list_layout.count():
            item = self.bank_list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        for acct in summary["bank_accounts"]:
            row_frame = QFrame()
            row_frame.setStyleSheet("QFrame { border: none; background: transparent; }")
            rl = QHBoxLayout(row_frame)
            rl.setContentsMargins(0, 2, 0, 2)
            name_lbl = QLabel(f"{acct['name']} ({acct['currency_code']})")
            name_lbl.setStyleSheet("font-size: 12px; color: #2d3748; border: none;")
            if acct["currency_code"] != home_ccy:
                bal_text = (
                    f"{format_money(acct['balance_foreign'], acct['currency_code'])}\n"
                    f"({format_money(acct['balance_home'], home_ccy)})"
                )
            else:
                bal_text = format_money(acct["balance_home"], home_ccy)
            bal_lbl = QLabel(bal_text)
            bal_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            bal_lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #1a365d; border: none;")
            rl.addWidget(name_lbl)
            rl.addWidget(bal_lbl)
            self.bank_list_layout.addWidget(row_frame)

        self.kpi_open_ar._value_label.setText(  # type: ignore[attr-defined]
            f"{format_money(summary['open_invoices_home'], home_ccy)} ({summary['open_invoices_count']})"
        )
        self.kpi_overdue_ap._value_label.setText(  # type: ignore[attr-defined]
            format_money(summary["overdue_bills_home"], home_ccy)
        )
        self.kpi_inventory._value_label.setText(  # type: ignore[attr-defined]
            format_money(summary["inventory_value_home"], home_ccy)
        )
        ni = summary["net_income_ytd_home"]
        ni_lbl = self.kpi_net_income._value_label  # type: ignore[attr-defined]
        ni_lbl.setText(format_money(ni, home_ccy))
        ni_lbl.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {'#2f855a' if ni >= 0 else '#c53030'}; border: none;"
        )
