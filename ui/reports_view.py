"""Financial Reports Viewer with Date Filters and CSV / Excel / ReportLab PDF Export."""

from __future__ import annotations

from datetime import date
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from accounting.engine import AccountingEngine
from accounting.reports import FinancialReports
from ui.styles import make_readonly_item, show_error, show_info, style_table_defaults

REPORT_TYPES = [
    ("pl", "Profit & Loss (Income Statement)"),
    ("bs", "Balance Sheet"),
    ("tb", "Trial Balance"),
    ("ar_aging", "A/R Aging Summary"),
    ("ap_aging", "A/P Aging Summary"),
    ("inv_val", "Inventory Valuation Summary (FIFO)"),
]


class FinancialReportsView(QWidget):
    """Interactive financial statement viewer with CSV, Excel, and PDF export."""

    def __init__(
        self,
        engine: AccountingEngine,
        initial_report: str = "pl",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.reports = FinancialReports(engine)
        self._current_report: dict[str, Any] | None = None
        self._build_ui(initial_report)
        self.refresh()

    def _build_ui(self, initial_report: str) -> None:
        layout = QVBoxLayout(self)

        # Controls bar
        ctrl = QHBoxLayout()
        ctrl.addWidget(QLabel("Report:"))
        self.report_combo = QComboBox()
        for key, label in REPORT_TYPES:
            self.report_combo.addItem(label, key)
        idx = self.report_combo.findData(initial_report)
        if idx >= 0:
            self.report_combo.setCurrentIndex(idx)
        self.report_combo.currentIndexChanged.connect(self._on_report_type_changed)
        ctrl.addWidget(self.report_combo)

        today = date.today()
        self.start_lbl = QLabel("From:")
        self.start_edit = QLineEdit(f"{today.year}-01-01")
        self.start_edit.setFixedWidth(100)
        self.end_lbl = QLabel("To / As of:")
        self.end_edit = QLineEdit(today.isoformat())
        self.end_edit.setFixedWidth(100)

        ctrl.addWidget(self.start_lbl)
        ctrl.addWidget(self.start_edit)
        ctrl.addWidget(self.end_lbl)
        ctrl.addWidget(self.end_edit)

        run_btn = QPushButton("Run Report")
        run_btn.setProperty("class", "PrimaryButton")
        run_btn.clicked.connect(self.refresh)
        ctrl.addWidget(run_btn)
        ctrl.addStretch()

        csv_btn = QPushButton("Export CSV")
        csv_btn.clicked.connect(self._export_csv)
        ctrl.addWidget(csv_btn)

        xlsx_btn = QPushButton("Export Excel")
        xlsx_btn.clicked.connect(self._export_excel)
        ctrl.addWidget(xlsx_btn)

        pdf_btn = QPushButton("Print / Save PDF")
        pdf_btn.setProperty("class", "SuccessButton")
        pdf_btn.clicked.connect(self._export_pdf)
        ctrl.addWidget(pdf_btn)

        layout.addLayout(ctrl)

        # Report Header & Verification Badge
        hdr_row = QHBoxLayout()
        self.title_lbl = QLabel("Financial Report")
        self.title_lbl.setStyleSheet("font-size: 16px; font-weight: 700; color: #1a365d;")
        hdr_row.addWidget(self.title_lbl)
        hdr_row.addStretch()
        self.badge_lbl = QLabel("")
        self.badge_lbl.setStyleSheet("font-weight: 700; font-size: 13px;")
        hdr_row.addWidget(self.badge_lbl)
        layout.addLayout(hdr_row)

        self.table = QTableWidget(0, 3)
        style_table_defaults(self.table)
        layout.addWidget(self.table)

        self._on_report_type_changed()

    def _on_report_type_changed(self) -> None:
        key = self.report_combo.currentData()
        show_start = key == "pl"
        show_end = key != "inv_val"
        self.start_lbl.setVisible(show_start)
        self.start_edit.setVisible(show_start)
        self.end_lbl.setVisible(show_end)
        self.end_edit.setVisible(show_end)
        self.refresh()

    def refresh(self) -> None:
        key = self.report_combo.currentData()
        start_d = self.start_edit.text().strip()
        end_d = self.end_edit.text().strip()
        try:
            if key == "pl":
                rep = self.reports.profit_and_loss(start_d, end_d)
                self.badge_lbl.setText("")
            elif key == "bs":
                rep = self.reports.balance_sheet(end_d)
                ok = rep["is_balanced"]
                self.badge_lbl.setText(
                    "✔ Assets == Liabilities + Equity" if ok else "⚠ OUT OF BALANCE"
                )
                self.badge_lbl.setStyleSheet(
                    f"font-weight: 700; color: {'#2f855a' if ok else '#c53030'};"
                )
            elif key == "tb":
                rep = self.reports.trial_balance(end_d)
                ok = rep["is_balanced"]
                self.badge_lbl.setText("✔ Debits == Credits" if ok else "⚠ UNBALANCED")
                self.badge_lbl.setStyleSheet(
                    f"font-weight: 700; color: {'#2f855a' if ok else '#c53030'};"
                )
            elif key == "ar_aging":
                rep = self.reports.ar_aging_summary(end_d)
                self.badge_lbl.setText("")
            elif key == "ap_aging":
                rep = self.reports.ap_aging_summary(end_d)
                self.badge_lbl.setText("")
            else:
                rep = self.reports.inventory_valuation_summary()
                ok = rep["is_reconciled"]
                self.badge_lbl.setText(
                    "✔ FIFO Layers == Inventory Asset GL" if ok else "⚠ VARIANCE DETECTED"
                )
                self.badge_lbl.setStyleSheet(
                    f"font-weight: 700; color: {'#2f855a' if ok else '#c53030'};"
                )
        except Exception as exc:
            show_error(self, "Report Error", str(exc))
            return

        self._current_report = rep
        self.title_lbl.setText(f"{rep['title']} — {rep['subtitle']}")

        headers = rep["headers"]
        rows = rep["rows"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))

        bold_font = QFont()
        bold_font.setBold(True)

        for r_idx, row in enumerate(rows):
            joined = " ".join(str(c) for c in row).upper()
            is_highlight = any(
                k in joined for k in ("TOTAL", "NET INCOME", "GROSS PROFIT", "RECONCILIATION")
            )
            is_section = len(row) >= 2 and row[0] and not row[1] and not row[-1]

            for c_idx, val in enumerate(row):
                align_right = c_idx >= len(headers) - 1 or (
                    key in ("tb", "ar_aging", "ap_aging", "inv_val") and c_idx >= 2
                )
                item = make_readonly_item(str(val), align_right=align_right)
                if is_highlight or is_section:
                    item.setFont(bold_font)
                if is_highlight:
                    item.setBackground(QColor("#e8eef5"))
                elif is_section:
                    item.setBackground(QColor("#f1f5f9"))
                self.table.setItem(r_idx, c_idx, item)
        self.table.resizeColumnsToContents()

    def _export_csv(self) -> None:
        if not self._current_report:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Report to CSV", "report.csv", "CSV Files (*.csv)")
        if path:
            try:
                FinancialReports.export_to_csv(self._current_report, path)
                show_info(self, "Exported", f"Report exported to:\n{path}")
            except Exception as exc:
                show_error(self, "Export Error", str(exc))

    def _export_excel(self) -> None:
        if not self._current_report:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Report to Excel", "report.xlsx", "Excel Files (*.xlsx)")
        if path:
            try:
                FinancialReports.export_to_excel(self._current_report, path)
                show_info(self, "Exported", f"Report exported to:\n{path}")
            except Exception as exc:
                show_error(self, "Export Error", str(exc))

    def _export_pdf(self) -> None:
        if not self._current_report:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Report as PDF", "report.pdf", "PDF Files (*.pdf)")
        if path:
            try:
                company = self.engine.get_company_settings()["company_name"]
                FinancialReports.export_to_pdf(self._current_report, path, company_name=company)
                show_info(self, "PDF Saved", f"PDF report saved to:\n{path}")
            except Exception as exc:
                show_error(self, "PDF Error", str(exc))
