"""Financial Reports Engine & Export Utilities (CSV, Excel, ReportLab PDF).

Generates:
1. Profit & Loss (Income Statement)
2. Balance Sheet (Assets == Liabilities + Equity, with dynamic Net Income)
3. Trial Balance (Home Debits == Home Credits)
4. A/R Aging Summary (Current, 1-30, 31-60, 61-90, 90+)
5. A/P Aging Summary (Current, 1-30, 31-60, 61-90, 90+)
6. Inventory Valuation Summary (FIFO layers reconciled to Inventory Asset GL)
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any, Optional

from accounting.engine import AccountingEngine
from accounting.models import DEBIT_NORMAL_TYPES, compute_normal_balance
from accounting.money import format_money


class FinancialReports:
    """Computes financial statements directly from posted double-entry ledger lines."""

    def __init__(self, engine: AccountingEngine) -> None:
        self.engine = engine
        self.conn = engine.conn

    # ------------------------------------------------------------------
    # 1. Profit & Loss (Income Statement)
    # ------------------------------------------------------------------

    def profit_and_loss(self, start_date: str, end_date: str) -> dict[str, Any]:
        home_ccy = self.engine.get_home_currency()
        rows = self.conn.execute(
            """
            SELECT
                a.id, a.account_number, a.name, a.account_type, a.detail_type,
                COALESCE(SUM(jel.debit_home), 0) AS dh,
                COALESCE(SUM(jel.credit_home), 0) AS ch
            FROM accounts a
            JOIN journal_entry_lines jel ON jel.account_id = a.id
            JOIN journal_entries je ON je.id = jel.journal_entry_id
            WHERE je.voided = 0
              AND a.account_type IN ('Income', 'Expense')
              AND je.entry_date >= ? AND je.entry_date <= ?
            GROUP BY a.id
            ORDER BY a.account_number, a.name
            """,
            (start_date, end_date),
        ).fetchall()

        income_lines: list[dict[str, Any]] = []
        cogs_lines: list[dict[str, Any]] = []
        opex_lines: list[dict[str, Any]] = []
        fx_lines: list[dict[str, Any]] = []

        for r in rows:
            d = dict(r)
            bal = compute_normal_balance(d["account_type"], int(d["dh"]), int(d["ch"]))
            if bal == 0:
                continue
            item = {
                "account_number": d["account_number"],
                "name": d["name"],
                "detail_type": d["detail_type"],
                "amount_home": bal,
            }
            if d["account_type"] == "Income":
                income_lines.append(item)
            elif d["detail_type"] == "Cost of Goods Sold":
                cogs_lines.append(item)
            elif d["detail_type"] == "Exchange Gain/Loss":
                # Expense normal balance: positive = net FX loss, negative = net FX gain
                fx_lines.append(item)
            else:
                opex_lines.append(item)

        total_income = sum(i["amount_home"] for i in income_lines)
        total_cogs = sum(i["amount_home"] for i in cogs_lines)
        gross_profit = total_income - total_cogs
        total_opex = sum(i["amount_home"] for i in opex_lines)
        total_fx_expense = sum(i["amount_home"] for i in fx_lines)
        net_income = gross_profit - total_opex - total_fx_expense

        table_rows: list[list[str]] = []
        table_rows.append(["INCOME", "", ""])
        for x in income_lines:
            table_rows.append([x["account_number"], x["name"], format_money(x["amount_home"], home_ccy)])
        table_rows.append(["", "Total Income", format_money(total_income, home_ccy)])

        table_rows.append(["COST OF GOODS SOLD", "", ""])
        for x in cogs_lines:
            table_rows.append([x["account_number"], x["name"], format_money(x["amount_home"], home_ccy)])
        table_rows.append(["", "Total Cost of Goods Sold", format_money(total_cogs, home_ccy)])
        table_rows.append(["", "GROSS PROFIT", format_money(gross_profit, home_ccy)])

        table_rows.append(["OPERATING EXPENSES", "", ""])
        for x in opex_lines:
            table_rows.append([x["account_number"], x["name"], format_money(x["amount_home"], home_ccy)])
        table_rows.append(["", "Total Operating Expenses", format_money(total_opex, home_ccy)])

        if fx_lines:
            table_rows.append(["EXCHANGE GAIN / LOSS (Expense + / Gain -)", "", ""])
            for x in fx_lines:
                table_rows.append([x["account_number"], x["name"], format_money(x["amount_home"], home_ccy)])
            table_rows.append(["", "Net Exchange Loss / (Gain)", format_money(total_fx_expense, home_ccy)])

        table_rows.append(["", "NET INCOME", format_money(net_income, home_ccy)])

        return {
            "title": "Profit & Loss Statement",
            "subtitle": f"{start_date} through {end_date}",
            "home_currency": home_ccy,
            "headers": ["Account #", "Account Name", f"Amount ({home_ccy})"],
            "rows": table_rows,
            "income_lines": income_lines,
            "cogs_lines": cogs_lines,
            "opex_lines": opex_lines,
            "fx_lines": fx_lines,
            "total_income_home": total_income,
            "total_cogs_home": total_cogs,
            "gross_profit_home": gross_profit,
            "total_opex_home": total_opex,
            "total_fx_expense_home": total_fx_expense,
            "net_income_home": net_income,
        }

    # ------------------------------------------------------------------
    # 2. Balance Sheet
    # ------------------------------------------------------------------

    def balance_sheet(self, as_of_date: str) -> dict[str, Any]:
        home_ccy = self.engine.get_home_currency()
        accounts = self.engine.list_accounts(include_inactive=True, as_of_date=as_of_date)

        assets: list[dict[str, Any]] = []
        liabilities: list[dict[str, Any]] = []
        equity: list[dict[str, Any]] = []
        total_income = 0
        total_expense = 0

        for a in accounts:
            bal = int(a["balance_home"])
            if a["account_type"] == "Asset" and bal != 0:
                assets.append(a)
            elif a["account_type"] == "Liability" and bal != 0:
                liabilities.append(a)
            elif a["account_type"] == "Equity" and bal != 0:
                equity.append(a)
            elif a["account_type"] == "Income":
                total_income += bal
            elif a["account_type"] == "Expense":
                total_expense += bal

        net_income = total_income - total_expense
        total_assets = sum(int(a["balance_home"]) for a in assets)
        total_liabilities = sum(int(a["balance_home"]) for a in liabilities)
        total_equity_accounts = sum(int(a["balance_home"]) for a in equity)
        total_equity = total_equity_accounts + net_income
        total_liabilities_and_equity = total_liabilities + total_equity
        is_balanced = total_assets == total_liabilities_and_equity

        table_rows: list[list[str]] = []
        table_rows.append(["ASSETS", "", ""])
        for a in assets:
            table_rows.append([a["account_number"], a["name"], format_money(a["balance_home"], home_ccy)])
        table_rows.append(["", "TOTAL ASSETS", format_money(total_assets, home_ccy)])

        table_rows.append(["LIABILITIES", "", ""])
        for a in liabilities:
            table_rows.append([a["account_number"], a["name"], format_money(a["balance_home"], home_ccy)])
        table_rows.append(["", "Total Liabilities", format_money(total_liabilities, home_ccy)])

        table_rows.append(["EQUITY", "", ""])
        for a in equity:
            table_rows.append([a["account_number"], a["name"], format_money(a["balance_home"], home_ccy)])
        table_rows.append(["", "Net Income (Current Period)", format_money(net_income, home_ccy)])
        table_rows.append(["", "Total Equity", format_money(total_equity, home_ccy)])
        table_rows.append(
            ["", "TOTAL LIABILITIES & EQUITY", format_money(total_liabilities_and_equity, home_ccy)]
        )

        return {
            "title": "Balance Sheet",
            "subtitle": f"As of {as_of_date}",
            "home_currency": home_ccy,
            "headers": ["Account #", "Account Name", f"Balance ({home_ccy})"],
            "rows": table_rows,
            "assets": assets,
            "liabilities": liabilities,
            "equity": equity,
            "net_income_home": net_income,
            "total_assets_home": total_assets,
            "total_liabilities_home": total_liabilities,
            "total_equity_home": total_equity,
            "total_liabilities_and_equity_home": total_liabilities_and_equity,
            "is_balanced": is_balanced,
        }

    # ------------------------------------------------------------------
    # 3. Trial Balance
    # ------------------------------------------------------------------

    def trial_balance(self, as_of_date: str) -> dict[str, Any]:
        home_ccy = self.engine.get_home_currency()
        accounts = self.engine.list_accounts(include_inactive=True, as_of_date=as_of_date)

        lines: list[dict[str, Any]] = []
        total_debit = 0
        total_credit = 0

        for a in accounts:
            dh = int(a["sum_debit_home"])
            ch = int(a["sum_credit_home"])
            net = dh - ch
            if net == 0:
                continue
            debit_bal = net if net > 0 else 0
            credit_bal = -net if net < 0 else 0
            total_debit += debit_bal
            total_credit += credit_bal
            lines.append(
                {
                    "account_number": a["account_number"],
                    "name": a["name"],
                    "account_type": a["account_type"],
                    "debit_home": debit_bal,
                    "credit_home": credit_bal,
                }
            )

        table_rows = [
            [
                x["account_number"],
                x["name"],
                x["account_type"],
                format_money(x["debit_home"], home_ccy) if x["debit_home"] else "",
                format_money(x["credit_home"], home_ccy) if x["credit_home"] else "",
            ]
            for x in lines
        ]
        table_rows.append(
            [
                "",
                "TOTAL",
                "",
                format_money(total_debit, home_ccy),
                format_money(total_credit, home_ccy),
            ]
        )

        return {
            "title": "Trial Balance",
            "subtitle": f"As of {as_of_date}",
            "home_currency": home_ccy,
            "headers": ["Account #", "Account Name", "Type", f"Debit ({home_ccy})", f"Credit ({home_ccy})"],
            "rows": table_rows,
            "lines": lines,
            "total_debit_home": total_debit,
            "total_credit_home": total_credit,
            "is_balanced": total_debit == total_credit,
        }

    # ------------------------------------------------------------------
    # 4 & 5. A/R Aging Summary & A/P Aging Summary
    # ------------------------------------------------------------------

    def _aging_summary(self, kind: str, as_of_date: str) -> dict[str, Any]:
        home_ccy = self.engine.get_home_currency()
        as_of = date.fromisoformat(as_of_date)

        if kind == "AR":
            title = "A/R Aging Summary"
            entity_col = "Customer"
            open_txns = self.engine.get_open_invoices()
            name_key = "customer_name"
        else:
            title = "A/P Aging Summary"
            entity_col = "Vendor"
            open_txns = self.engine.get_open_bills()
            name_key = "vendor_name"

        buckets: dict[str, dict[str, int]] = {}
        for t in open_txns:
            if t["txn_date"] > as_of_date:
                continue
            name = t[name_key]
            if name not in buckets:
                buckets[name] = {"current": 0, "d1_30": 0, "d31_60": 0, "d61_90": 0, "d90p": 0, "total": 0}
            due = date.fromisoformat(t["due_date"] or t["txn_date"])
            days_past = (as_of - due).days
            amt = int(t["open_home"])
            if days_past <= 0:
                buckets[name]["current"] += amt
            elif days_past <= 30:
                buckets[name]["d1_30"] += amt
            elif days_past <= 60:
                buckets[name]["d31_60"] += amt
            elif days_past <= 90:
                buckets[name]["d61_90"] += amt
            else:
                buckets[name]["d90p"] += amt
            buckets[name]["total"] += amt

        totals = {"current": 0, "d1_30": 0, "d31_60": 0, "d61_90": 0, "d90p": 0, "total": 0}
        table_rows: list[list[str]] = []
        for name in sorted(buckets):
            b = buckets[name]
            for k in totals:
                totals[k] += b[k]
            table_rows.append(
                [
                    name,
                    format_money(b["current"], home_ccy),
                    format_money(b["d1_30"], home_ccy),
                    format_money(b["d31_60"], home_ccy),
                    format_money(b["d61_90"], home_ccy),
                    format_money(b["d90p"], home_ccy),
                    format_money(b["total"], home_ccy),
                ]
            )

        table_rows.append(
            [
                "TOTAL",
                format_money(totals["current"], home_ccy),
                format_money(totals["d1_30"], home_ccy),
                format_money(totals["d31_60"], home_ccy),
                format_money(totals["d61_90"], home_ccy),
                format_money(totals["d90p"], home_ccy),
                format_money(totals["total"], home_ccy),
            ]
        )

        return {
            "title": title,
            "subtitle": f"As of {as_of_date} (Home Currency: {home_ccy})",
            "home_currency": home_ccy,
            "headers": [entity_col, "Current", "1 - 30", "31 - 60", "61 - 90", "90+", "Total"],
            "rows": table_rows,
            "buckets": buckets,
            "totals": totals,
        }

    def ar_aging_summary(self, as_of_date: str) -> dict[str, Any]:
        return self._aging_summary("AR", as_of_date)

    def ap_aging_summary(self, as_of_date: str) -> dict[str, Any]:
        return self._aging_summary("AP", as_of_date)

    # ------------------------------------------------------------------
    # 6. Inventory Valuation Summary (FIFO Reconciled to GL)
    # ------------------------------------------------------------------

    def inventory_valuation_summary(self) -> dict[str, Any]:
        home_ccy = self.engine.get_home_currency()
        items = [i for i in self.engine.list_items() if i["type"] == "Inventory"]
        inv_acct = self.engine.get_account_by_detail_type("Inventory Asset")
        _, gl_balance_home = self.engine.get_account_balance(inv_acct["id"])

        total_qty = 0
        total_fifo_value = 0
        table_rows: list[list[str]] = []

        for item in items:
            qty = int(item["qty_on_hand"])
            val = int(item["total_asset_value_home"])
            avg_cost = int(item["avg_fifo_cost_home"])
            total_qty += qty
            total_fifo_value += val
            table_rows.append(
                [
                    item["sku"],
                    item["name"],
                    str(qty),
                    format_money(avg_cost, home_ccy),
                    format_money(val, home_ccy),
                ]
            )

        table_rows.append(
            ["", "TOTAL FIFO VALUATION", str(total_qty), "", format_money(total_fifo_value, home_ccy)]
        )
        table_rows.append(
            ["", "Inventory Asset GL Balance", "", "", format_money(gl_balance_home, home_ccy)]
        )
        diff = total_fifo_value - gl_balance_home
        table_rows.append(
            ["", "Reconciliation Variance", "", "", format_money(diff, home_ccy)]
        )

        return {
            "title": "Inventory Valuation Summary (FIFO)",
            "subtitle": f"Reconciled to GL Account {inv_acct['account_number']} {inv_acct['name']}",
            "home_currency": home_ccy,
            "headers": ["SKU", "Item Name", "Qty On Hand", f"Avg FIFO Cost ({home_ccy})", f"Asset Value ({home_ccy})"],
            "rows": table_rows,
            "items": items,
            "total_qty": total_qty,
            "total_fifo_value_home": total_fifo_value,
            "gl_inventory_balance_home": gl_balance_home,
            "is_reconciled": total_fifo_value == gl_balance_home,
        }

    # ------------------------------------------------------------------
    # Exporters (CSV, Excel, PDF)
    # ------------------------------------------------------------------

    @staticmethod
    def export_to_csv(report: dict[str, Any], file_path: str | Path) -> Path:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([report["title"]])
            writer.writerow([report["subtitle"]])
            writer.writerow([])
            writer.writerow(report["headers"])
            writer.writerows(report["rows"])
        return path

    @staticmethod
    def export_to_excel(report: dict[str, Any], file_path: str | Path) -> Path:
        import pandas as pd

        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(report["rows"], columns=report["headers"])
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Report", startrow=2)
            ws = writer.sheets["Report"]
            ws["A1"] = report["title"]
            ws["A2"] = report["subtitle"]
        return path

    @staticmethod
    def export_to_pdf(
        report: dict[str, Any], file_path: str | Path, company_name: str = "My Company"
    ) -> Path:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(str(path), pagesize=letter, topMargin=36, bottomMargin=36)
        styles = getSampleStyleSheet()
        elements = [
            Paragraph(f"<b>{company_name}</b>", styles["Title"]),
            Paragraph(f"<b>{report['title']}</b> — {report['subtitle']}", styles["Heading3"]),
            Spacer(1, 12),
        ]

        data = [report["headers"]] + report["rows"]
        table = Table(data, repeatRows=1)
        style_cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f4e79")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fc")]),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
        # Bold section headers and totals
        for idx, row in enumerate(report["rows"], start=1):
            joined = " ".join(str(c) for c in row).upper()
            if "TOTAL" in joined or "NET INCOME" in joined or "GROSS PROFIT" in joined:
                style_cmds.append(("FONTNAME", (0, idx), (-1, idx), "Helvetica-Bold"))
                style_cmds.append(("BACKGROUND", (0, idx), (-1, idx), colors.HexColor("#e8eef5")))

        table.setStyle(TableStyle(style_cmds))
        elements.append(table)
        doc.build(elements)
        return path
