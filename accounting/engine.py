"""Core Double-Entry Accounting Engine with Multi-Currency FX & FIFO Inventory.

All financial events post to `journal_entries` and `journal_entry_lines`.
Zero-sum home-currency equality (`SUM(debit_home) == SUM(credit_home)`) is
strictly enforced on every commit. All money is stored as integer cents.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any, Optional

from accounting.db import Database, utc_now_iso
from accounting.models import (
    ACCOUNT_TYPES,
    DEBIT_NORMAL_TYPES,
    BillLineInput,
    InvoiceLineInput,
    JournalLineInput,
    compute_normal_balance,
)
from accounting.money import (
    RATE_SCALE,
    apply_tax_bps,
    calculate_line_amount,
    convert_to_home,
)


class AccountingError(ValueError):
    """Raised when a double-entry, FIFO, FX, or business rule is violated."""


class AccountingEngine:
    """Single-user local double-entry accounting engine backed by SQLite."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.conn = db.conn

    # ------------------------------------------------------------------
    # Company Settings & Currencies
    # ------------------------------------------------------------------

    def get_company_settings(self) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM company_settings WHERE id = 1").fetchone()
        return dict(row)

    def get_home_currency(self) -> str:
        row = self.conn.execute(
            "SELECT home_currency_code FROM company_settings WHERE id = 1"
        ).fetchone()
        return row["home_currency_code"]

    def update_company_settings(
        self,
        company_name: str,
        address: str = "",
        fiscal_year_start: str = "01-01",
        home_currency_code: Optional[str] = None,
        tax_rate_default_bps: int = 0,
    ) -> None:
        current = self.get_company_settings()
        home_ccy = (home_currency_code or current["home_currency_code"]).upper()
        with self.conn:
            if home_ccy != current["home_currency_code"]:
                # Ensure home currency exists and has rate 1.000000
                self.conn.execute(
                    """
                    UPDATE currencies SET exchange_rate_micros = ?, updated_at = ?
                    WHERE code = ?
                    """,
                    (RATE_SCALE, utc_now_iso(), home_ccy),
                )
            self.conn.execute(
                """
                UPDATE company_settings
                SET company_name = ?, address = ?, fiscal_year_start = ?,
                    home_currency_code = ?, tax_rate_default_bps = ?
                WHERE id = 1
                """,
                (company_name.strip(), address.strip(), fiscal_year_start, home_ccy, int(tax_rate_default_bps)),
            )

    def list_currencies(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT code, name, symbol, exchange_rate_micros, updated_at FROM currencies ORDER BY code"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_exchange_rate(self, currency_code: str) -> int:
        home = self.get_home_currency()
        if currency_code == home:
            return RATE_SCALE
        row = self.conn.execute(
            "SELECT exchange_rate_micros FROM currencies WHERE code = ?", (currency_code,)
        ).fetchone()
        if row is None:
            raise AccountingError(f"Unknown currency: {currency_code}")
        return int(row["exchange_rate_micros"])

    def upsert_currency(
        self, code: str, name: str, symbol: str, exchange_rate_micros: int
    ) -> None:
        code = code.strip().upper()
        if not code or len(code) > 6:
            raise AccountingError("Currency code must be 1-6 characters.")
        if not isinstance(exchange_rate_micros, int) or exchange_rate_micros <= 0:
            raise AccountingError("Exchange rate must be a positive integer in micro-units.")
        home = self.get_home_currency()
        if code == home:
            exchange_rate_micros = RATE_SCALE
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO currencies (code, name, symbol, exchange_rate_micros, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name = excluded.name,
                    symbol = excluded.symbol,
                    exchange_rate_micros = excluded.exchange_rate_micros,
                    updated_at = excluded.updated_at
                """,
                (code, name.strip(), symbol.strip(), exchange_rate_micros, utc_now_iso()),
            )

    # ------------------------------------------------------------------
    # Chart of Accounts & Account Register
    # ------------------------------------------------------------------

    def get_account(self, account_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            raise AccountingError(f"Account ID {account_id} not found.")
        return dict(row)

    def get_account_by_detail_type(
        self, detail_type: str, currency_code: Optional[str] = None
    ) -> dict[str, Any]:
        if currency_code:
            row = self.conn.execute(
                """
                SELECT * FROM accounts
                WHERE detail_type = ? AND currency_code = ? AND is_active = 1
                ORDER BY account_number LIMIT 1
                """,
                (detail_type, currency_code),
            ).fetchone()
            if row is not None:
                return dict(row)
        row = self.conn.execute(
            """
            SELECT * FROM accounts
            WHERE detail_type = ? AND is_active = 1
            ORDER BY account_number LIMIT 1
            """,
            (detail_type,),
        ).fetchone()
        if row is None:
            raise AccountingError(f"No active account with detail_type '{detail_type}' found.")
        return dict(row)

    def get_account_by_number(self, account_number: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM accounts WHERE account_number = ?", (account_number,)
        ).fetchone()
        if row is None:
            raise AccountingError(f"Account number '{account_number}' not found.")
        return dict(row)

    def create_account(
        self,
        account_number: str,
        name: str,
        account_type: str,
        detail_type: str,
        currency_code: Optional[str] = None,
        parent_account_id: Optional[int] = None,
    ) -> int:
        if account_type not in ACCOUNT_TYPES:
            raise AccountingError(f"Invalid account_type: {account_type}")
        ccy = (currency_code or self.get_home_currency()).upper()
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO accounts (account_number, name, account_type, detail_type, currency_code, parent_account_id, is_active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (account_number.strip(), name.strip(), account_type, detail_type.strip(), ccy, parent_account_id),
            )
            return int(cur.lastrowid)

    def update_account(
        self,
        account_id: int,
        account_number: str,
        name: str,
        account_type: str,
        detail_type: str,
        currency_code: str,
        parent_account_id: Optional[int] = None,
        is_active: bool = True,
    ) -> None:
        if account_type not in ACCOUNT_TYPES:
            raise AccountingError(f"Invalid account_type: {account_type}")
        with self.conn:
            self.conn.execute(
                """
                UPDATE accounts
                SET account_number = ?, name = ?, account_type = ?, detail_type = ?,
                    currency_code = ?, parent_account_id = ?, is_active = ?
                WHERE id = ?
                """,
                (
                    account_number.strip(),
                    name.strip(),
                    account_type,
                    detail_type.strip(),
                    currency_code.upper(),
                    parent_account_id,
                    1 if is_active else 0,
                    account_id,
                ),
            )

    def list_accounts(
        self,
        include_inactive: bool = False,
        account_types: Optional[tuple[str, ...]] = None,
        as_of_date: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """List accounts with dynamically calculated foreign and home balances."""
        where_clauses = []
        params: list[Any] = []
        if not include_inactive:
            where_clauses.append("a.is_active = 1")
        if account_types:
            placeholders = ",".join("?" for _ in account_types)
            where_clauses.append(f"a.account_type IN ({placeholders})")
            params.extend(account_types)

        date_filter = ""
        date_params: list[Any] = []
        if as_of_date:
            date_filter = "AND je.entry_date <= ?"
            date_params.append(as_of_date)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        sql = f"""
            SELECT
                a.id, a.account_number, a.name, a.account_type, a.detail_type,
                a.currency_code, a.parent_account_id, a.is_active,
                COALESCE(SUM(CASE WHEN je.id IS NOT NULL THEN jel.debit_foreign ELSE 0 END), 0) AS sum_debit_foreign,
                COALESCE(SUM(CASE WHEN je.id IS NOT NULL THEN jel.credit_foreign ELSE 0 END), 0) AS sum_credit_foreign,
                COALESCE(SUM(CASE WHEN je.id IS NOT NULL THEN jel.debit_home ELSE 0 END), 0) AS sum_debit_home,
                COALESCE(SUM(CASE WHEN je.id IS NOT NULL THEN jel.credit_home ELSE 0 END), 0) AS sum_credit_home
            FROM accounts a
            LEFT JOIN journal_entry_lines jel ON jel.account_id = a.id
            LEFT JOIN journal_entries je ON je.id = jel.journal_entry_id AND je.voided = 0 {date_filter}
            {where_sql}
            GROUP BY a.id
            ORDER BY a.account_number, a.name
        """
        rows = self.conn.execute(sql, date_params + params).fetchall()
        results: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["balance_foreign"] = compute_normal_balance(
                d["account_type"], int(d["sum_debit_foreign"]), int(d["sum_credit_foreign"])
            )
            d["balance_home"] = compute_normal_balance(
                d["account_type"], int(d["sum_debit_home"]), int(d["sum_credit_home"])
            )
            results.append(d)
        return results

    def get_account_balance(
        self, account_id: int, as_of_date: Optional[str] = None
    ) -> tuple[int, int]:
        """Return (balance_foreign, balance_home) dynamically from posted ledger lines."""
        acct = self.get_account(account_id)
        date_sql = "AND je.entry_date <= ?" if as_of_date else ""
        params: list[Any] = [account_id]
        if as_of_date:
            params.append(as_of_date)
        row = self.conn.execute(
            f"""
            SELECT
                COALESCE(SUM(jel.debit_foreign), 0) AS df,
                COALESCE(SUM(jel.credit_foreign), 0) AS cf,
                COALESCE(SUM(jel.debit_home), 0) AS dh,
                COALESCE(SUM(jel.credit_home), 0) AS ch
            FROM journal_entry_lines jel
            JOIN journal_entries je ON je.id = jel.journal_entry_id
            WHERE jel.account_id = ? AND je.voided = 0 {date_sql}
            """,
            params,
        ).fetchone()
        bal_f = compute_normal_balance(acct["account_type"], int(row["df"]), int(row["cf"]))
        bal_h = compute_normal_balance(acct["account_type"], int(row["dh"]), int(row["ch"]))
        return bal_f, bal_h

    def get_account_register(self, account_id: int) -> list[dict[str, Any]]:
        """Checkbook-style register for an account with running balances."""
        acct = self.get_account(account_id)
        rows = self.conn.execute(
            """
            SELECT
                jel.id AS jel_id,
                je.id AS journal_entry_id,
                je.transaction_id,
                je.entry_date,
                je.reference_no,
                je.currency_code,
                je.exchange_rate_micros,
                je.memo,
                COALESCE(t.txn_type, 'JournalEntry') AS txn_type,
                jel.entity_type,
                jel.entity_id,
                jel.debit_foreign,
                jel.credit_foreign,
                jel.debit_home,
                jel.credit_home,
                jel.is_reconciled,
                jel.reconciled_date,
                CASE
                    WHEN jel.entity_type = 'Customer' THEN (SELECT name FROM customers WHERE id = jel.entity_id)
                    WHEN jel.entity_type = 'Vendor' THEN (SELECT name FROM vendors WHERE id = jel.entity_id)
                    ELSE ''
                END AS entity_name
            FROM journal_entry_lines jel
            JOIN journal_entries je ON je.id = jel.journal_entry_id
            LEFT JOIN transactions t ON t.id = je.transaction_id
            WHERE jel.account_id = ? AND je.voided = 0
            ORDER BY je.entry_date ASC, je.id ASC, jel.id ASC
            """,
            (account_id,),
        ).fetchall()

        running_home = 0
        running_foreign = 0
        is_debit_normal = acct["account_type"] in DEBIT_NORMAL_TYPES
        register: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            dh, ch = int(d["debit_home"]), int(d["credit_home"])
            df, cf = int(d["debit_foreign"]), int(d["credit_foreign"])
            delta_home = (dh - ch) if is_debit_normal else (ch - dh)
            delta_foreign = (df - cf) if is_debit_normal else (cf - df)
            running_home += delta_home
            running_foreign += delta_foreign
            d["running_balance_home"] = running_home
            d["running_balance_foreign"] = running_foreign
            register.append(d)
        return register

    # ------------------------------------------------------------------
    # Customers & Vendors
    # ------------------------------------------------------------------

    def create_customer(
        self,
        name: str,
        company_name: str = "",
        email: str = "",
        phone: str = "",
        billing_address: str = "",
        currency_code: Optional[str] = None,
        terms: str = "Net 30",
    ) -> int:
        ccy = (currency_code or self.get_home_currency()).upper()
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO customers (name, company_name, email, phone, billing_address, currency_code, terms, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (name.strip(), company_name.strip(), email.strip(), phone.strip(), billing_address.strip(), ccy, terms.strip()),
            )
            return int(cur.lastrowid)

    def update_customer(
        self,
        customer_id: int,
        name: str,
        company_name: str = "",
        email: str = "",
        phone: str = "",
        billing_address: str = "",
        currency_code: Optional[str] = None,
        terms: str = "Net 30",
        is_active: bool = True,
    ) -> None:
        ccy = (currency_code or self.get_home_currency()).upper()
        with self.conn:
            self.conn.execute(
                """
                UPDATE customers
                SET name = ?, company_name = ?, email = ?, phone = ?, billing_address = ?,
                    currency_code = ?, terms = ?, is_active = ?
                WHERE id = ?
                """,
                (
                    name.strip(),
                    company_name.strip(),
                    email.strip(),
                    phone.strip(),
                    billing_address.strip(),
                    ccy,
                    terms.strip(),
                    1 if is_active else 0,
                    customer_id,
                ),
            )

    def get_customer(self, customer_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if row is None:
            raise AccountingError(f"Customer ID {customer_id} not found.")
        return dict(row)

    def list_customers(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        where = "" if include_inactive else "WHERE c.is_active = 1"
        rows = self.conn.execute(
            f"""
            SELECT c.*,
                COALESCE((
                    SELECT SUM(jel.debit_foreign - jel.credit_foreign)
                    FROM journal_entry_lines jel
                    JOIN journal_entries je ON je.id = jel.journal_entry_id
                    JOIN accounts a ON a.id = jel.account_id
                    WHERE je.voided = 0 AND a.detail_type = 'Accounts Receivable'
                      AND jel.entity_type = 'Customer' AND jel.entity_id = c.id
                ), 0) AS open_balance_foreign,
                COALESCE((
                    SELECT SUM(jel.debit_home - jel.credit_home)
                    FROM journal_entry_lines jel
                    JOIN journal_entries je ON je.id = jel.journal_entry_id
                    JOIN accounts a ON a.id = jel.account_id
                    WHERE je.voided = 0 AND a.detail_type = 'Accounts Receivable'
                      AND jel.entity_type = 'Customer' AND jel.entity_id = c.id
                ), 0) AS open_balance_home
            FROM customers c
            {where}
            ORDER BY c.name
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def create_vendor(
        self,
        name: str,
        company_name: str = "",
        email: str = "",
        phone: str = "",
        address: str = "",
        currency_code: Optional[str] = None,
        terms: str = "Net 30",
    ) -> int:
        ccy = (currency_code or self.get_home_currency()).upper()
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO vendors (name, company_name, email, phone, address, currency_code, terms, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (name.strip(), company_name.strip(), email.strip(), phone.strip(), address.strip(), ccy, terms.strip()),
            )
            return int(cur.lastrowid)

    def update_vendor(
        self,
        vendor_id: int,
        name: str,
        company_name: str = "",
        email: str = "",
        phone: str = "",
        address: str = "",
        currency_code: Optional[str] = None,
        terms: str = "Net 30",
        is_active: bool = True,
    ) -> None:
        ccy = (currency_code or self.get_home_currency()).upper()
        with self.conn:
            self.conn.execute(
                """
                UPDATE vendors
                SET name = ?, company_name = ?, email = ?, phone = ?, address = ?,
                    currency_code = ?, terms = ?, is_active = ?
                WHERE id = ?
                """,
                (
                    name.strip(),
                    company_name.strip(),
                    email.strip(),
                    phone.strip(),
                    address.strip(),
                    ccy,
                    terms.strip(),
                    1 if is_active else 0,
                    vendor_id,
                ),
            )

    def get_vendor(self, vendor_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
        if row is None:
            raise AccountingError(f"Vendor ID {vendor_id} not found.")
        return dict(row)

    def list_vendors(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        where = "" if include_inactive else "WHERE v.is_active = 1"
        rows = self.conn.execute(
            f"""
            SELECT v.*,
                COALESCE((
                    SELECT SUM(jel.credit_foreign - jel.debit_foreign)
                    FROM journal_entry_lines jel
                    JOIN journal_entries je ON je.id = jel.journal_entry_id
                    JOIN accounts a ON a.id = jel.account_id
                    WHERE je.voided = 0 AND a.detail_type = 'Accounts Payable'
                      AND jel.entity_type = 'Vendor' AND jel.entity_id = v.id
                ), 0) AS open_balance_foreign,
                COALESCE((
                    SELECT SUM(jel.credit_home - jel.debit_home)
                    FROM journal_entry_lines jel
                    JOIN journal_entries je ON je.id = jel.journal_entry_id
                    JOIN accounts a ON a.id = jel.account_id
                    WHERE je.voided = 0 AND a.detail_type = 'Accounts Payable'
                      AND jel.entity_type = 'Vendor' AND jel.entity_id = v.id
                ), 0) AS open_balance_home
            FROM vendors v
            {where}
            ORDER BY v.name
            """
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Items & FIFO Inventory Layers
    # ------------------------------------------------------------------

    def create_item(
        self,
        sku: str,
        name: str,
        item_type: str,
        description: str = "",
        sales_price: int = 0,
        purchase_cost: int = 0,
        income_account_id: Optional[int] = None,
        expense_account_id: Optional[int] = None,
        asset_account_id: Optional[int] = None,
    ) -> int:
        if item_type not in ("Service", "Non-Inventory", "Inventory"):
            raise AccountingError(f"Invalid item type: {item_type}")

        if income_account_id is None:
            detail = "Service Income" if item_type == "Service" else "Sales"
            income_account_id = self.get_account_by_detail_type(detail)["id"]
        if expense_account_id is None:
            detail = "Cost of Goods Sold" if item_type == "Inventory" else "Operating Expense"
            expense_account_id = self.get_account_by_detail_type(detail)["id"]
        if item_type == "Inventory" and asset_account_id is None:
            asset_account_id = self.get_account_by_detail_type("Inventory Asset")["id"]

        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO items (
                    sku, name, type, description, sales_price, purchase_cost,
                    income_account_id, expense_account_id, asset_account_id, qty_on_hand
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    sku.strip(),
                    name.strip(),
                    item_type,
                    description.strip(),
                    int(sales_price),
                    int(purchase_cost),
                    income_account_id,
                    expense_account_id,
                    asset_account_id,
                ),
            )
            return int(cur.lastrowid)

    def update_item(
        self,
        item_id: int,
        sku: str,
        name: str,
        item_type: str,
        description: str = "",
        sales_price: int = 0,
        purchase_cost: int = 0,
        income_account_id: Optional[int] = None,
        expense_account_id: Optional[int] = None,
        asset_account_id: Optional[int] = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE items
                SET sku = ?, name = ?, type = ?, description = ?, sales_price = ?,
                    purchase_cost = ?, income_account_id = ?, expense_account_id = ?,
                    asset_account_id = ?
                WHERE id = ?
                """,
                (
                    sku.strip(),
                    name.strip(),
                    item_type,
                    description.strip(),
                    int(sales_price),
                    int(purchase_cost),
                    income_account_id,
                    expense_account_id,
                    asset_account_id,
                    item_id,
                ),
            )

    def get_item(self, item_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise AccountingError(f"Item ID {item_id} not found.")
        return dict(row)

    def list_items(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT i.*,
                COALESCE((
                    SELECT SUM(l.qty_remaining) FROM inventory_fifo_layers l WHERE l.item_id = i.id
                ), 0) AS fifo_qty,
                COALESCE((
                    SELECT SUM(l.qty_remaining * l.unit_cost_home) FROM inventory_fifo_layers l WHERE l.item_id = i.id
                ), 0) AS total_asset_value_home
            FROM items i
            ORDER BY i.sku, i.name
            """
        ).fetchall()
        results: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            qty = int(d["fifo_qty"]) if d["type"] == "Inventory" else 0
            val = int(d["total_asset_value_home"])
            d["qty_on_hand"] = qty
            d["avg_fifo_cost_home"] = (val + qty // 2) // qty if qty > 0 else 0
            results.append(d)
        return results

    def get_item_fifo_layers(
        self, item_id: Optional[int] = None, only_remaining: bool = True
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if item_id is not None:
            clauses.append("l.item_id = ?")
            params.append(item_id)
        if only_remaining:
            clauses.append("l.qty_remaining > 0")
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT l.*, i.sku, i.name AS item_name,
                   t.txn_number AS purchase_txn_number, t.txn_type AS purchase_txn_type
            FROM inventory_fifo_layers l
            JOIN items i ON i.id = l.item_id
            LEFT JOIN transactions t ON t.id = l.purchase_txn_id
            {where_sql}
            ORDER BY l.item_id ASC, l.received_date ASC, l.id ASC
            """,
            params,
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["remaining_value_home"] = int(d["qty_remaining"]) * int(d["unit_cost_home"])
            out.append(d)
        return out

    def _add_fifo_layer(
        self,
        cur: sqlite3.Cursor,
        item_id: int,
        purchase_txn_id: int,
        received_date: str,
        qty: int,
        unit_cost_home: int,
    ) -> int:
        if qty <= 0:
            raise AccountingError("FIFO layer quantity must be > 0.")
        if unit_cost_home < 0:
            raise AccountingError("FIFO layer unit cost cannot be negative.")
        cur.execute(
            """
            INSERT INTO inventory_fifo_layers (item_id, purchase_txn_id, received_date, initial_qty, qty_remaining, unit_cost_home)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (item_id, purchase_txn_id, received_date, qty, qty, unit_cost_home),
        )
        layer_id = int(cur.lastrowid)
        cur.execute(
            "UPDATE items SET qty_on_hand = qty_on_hand + ? WHERE id = ?",
            (qty, item_id),
        )
        return layer_id

    def _consume_fifo_layers(
        self,
        cur: sqlite3.Cursor,
        item_id: int,
        sale_txn_id: int,
        qty_to_sell: int,
    ) -> int:
        """Consume oldest available FIFO layers and return exact integer COGS (home cents)."""
        if qty_to_sell <= 0:
            raise AccountingError("Sale quantity must be > 0.")

        layers = cur.execute(
            """
            SELECT id, qty_remaining, unit_cost_home
            FROM inventory_fifo_layers
            WHERE item_id = ? AND qty_remaining > 0
            ORDER BY received_date ASC, id ASC
            """,
            (item_id,),
        ).fetchall()

        available = sum(int(l["qty_remaining"]) for l in layers)
        if available < qty_to_sell:
            item_row = cur.execute("SELECT sku, name FROM items WHERE id = ?", (item_id,)).fetchone()
            label = f"{item_row['sku']} ({item_row['name']})" if item_row else str(item_id)
            raise AccountingError(
                f"Insufficient inventory for {label}: requested {qty_to_sell}, available {available}."
            )

        remaining_to_consume = qty_to_sell
        total_cogs_home = 0

        for layer in layers:
            if remaining_to_consume <= 0:
                break
            layer_id = int(layer["id"])
            layer_rem = int(layer["qty_remaining"])
            unit_cost = int(layer["unit_cost_home"])
            take = min(layer_rem, remaining_to_consume)
            layer_cost = take * unit_cost

            cur.execute(
                "UPDATE inventory_fifo_layers SET qty_remaining = qty_remaining - ? WHERE id = ?",
                (take, layer_id),
            )
            cur.execute(
                """
                INSERT INTO fifo_consumptions (sale_txn_id, layer_id, qty_consumed, unit_cost_home, total_cost_home)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sale_txn_id, layer_id, take, unit_cost, layer_cost),
            )
            total_cogs_home += layer_cost
            remaining_to_consume -= take

        cur.execute(
            "UPDATE items SET qty_on_hand = qty_on_hand - ? WHERE id = ?",
            (qty_to_sell, item_id),
        )
        return total_cogs_home

    # ------------------------------------------------------------------
    # Core Double-Entry Journal Posting (STRICT ZERO-SUM ENFORCEMENT)
    # ------------------------------------------------------------------

    def _next_txn_number(self, cur: sqlite3.Cursor, prefix: str, txn_type: str) -> str:
        row = cur.execute(
            "SELECT COUNT(*) AS c FROM transactions WHERE txn_type = ?", (txn_type,)
        ).fetchone()
        seq = int(row["c"]) + 1
        return f"{prefix}-{seq:04d}"

    def _post_journal_entry_in_tx(
        self,
        cur: sqlite3.Cursor,
        entry_date: str,
        reference_no: str,
        currency_code: str,
        exchange_rate_micros: int,
        memo: str,
        lines: list[JournalLineInput],
        transaction_id: Optional[int] = None,
    ) -> int:
        if len(lines) < 2:
            raise AccountingError("Journal entry must have at least 2 lines.")

        total_debit_home = 0
        total_credit_home = 0

        for idx, line in enumerate(lines, start=1):
            for field_name, val in (
                ("debit_home", line.debit_home),
                ("credit_home", line.credit_home),
                ("debit_foreign", line.debit_foreign),
                ("credit_foreign", line.credit_foreign),
            ):
                if not isinstance(val, int) or isinstance(val, bool) or val < 0:
                    raise AccountingError(f"Line {idx}: {field_name} must be a non-negative integer.")
            if line.debit_home > 0 and line.credit_home > 0:
                raise AccountingError(f"Line {idx}: cannot have both debit and credit.")
            if line.debit_home == 0 and line.credit_home == 0:
                raise AccountingError(f"Line {idx}: must have a non-zero home debit or credit.")
            total_debit_home += line.debit_home
            total_credit_home += line.credit_home

        if total_debit_home <= 0:
            raise AccountingError("Journal entry total must be greater than zero.")
        if total_debit_home != total_credit_home:
            raise AccountingError(
                f"Unbalanced journal entry rejected: "
                f"Debits ({total_debit_home}) != Credits ({total_credit_home})."
            )

        now = utc_now_iso()
        cur.execute(
            """
            INSERT INTO journal_entries (
                transaction_id, entry_date, reference_no, currency_code,
                exchange_rate_micros, memo, created_at, updated_at, voided
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                transaction_id,
                entry_date,
                reference_no,
                currency_code,
                exchange_rate_micros,
                memo,
                now,
                now,
            ),
        )
        je_id = int(cur.lastrowid)

        for line in lines:
            cur.execute(
                """
                INSERT INTO journal_entry_lines (
                    journal_entry_id, account_id, entity_type, entity_id,
                    debit_foreign, credit_foreign, debit_home, credit_home,
                    is_reconciled, reconciled_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
                """,
                (
                    je_id,
                    line.account_id,
                    line.entity_type,
                    line.entity_id,
                    line.debit_foreign,
                    line.credit_foreign,
                    line.debit_home,
                    line.credit_home,
                ),
            )
        return je_id

    def create_manual_journal_entry(
        self,
        entry_date: str,
        lines: list[JournalLineInput],
        currency_code: Optional[str] = None,
        exchange_rate_micros: Optional[int] = None,
        memo: str = "",
        reference_no: Optional[str] = None,
    ) -> int:
        ccy = (currency_code or self.get_home_currency()).upper()
        rate = exchange_rate_micros if exchange_rate_micros is not None else self.get_exchange_rate(ccy)
        now = utc_now_iso()
        total_home = sum(l.debit_home for l in lines)
        total_foreign = sum(l.debit_foreign for l in lines) or total_home

        with self.conn:
            cur = self.conn.cursor()
            ref = reference_no or self._next_txn_number(cur, "JE", "JournalEntry")
            cur.execute(
                """
                INSERT INTO transactions (
                    txn_type, txn_number, txn_date, due_date, entity_id, currency_code,
                    exchange_rate_micros, memo, status, total_foreign, total_home,
                    created_at, updated_at, voided
                ) VALUES ('JournalEntry', ?, ?, NULL, NULL, ?, ?, ?, 'Paid', ?, ?, ?, ?, 0)
                """,
                (ref, entry_date, ccy, rate, memo, total_foreign, total_home, now, now),
            )
            txn_id = int(cur.lastrowid)
            self._post_journal_entry_in_tx(
                cur,
                entry_date=entry_date,
                reference_no=ref,
                currency_code=ccy,
                exchange_rate_micros=rate,
                memo=memo,
                lines=lines,
                transaction_id=txn_id,
            )
            return txn_id

    # ------------------------------------------------------------------
    # Accounts Receivable: Invoices, Sales Receipts, Receive Payments
    # ------------------------------------------------------------------

    def create_invoice(
        self,
        customer_id: int,
        txn_date: str,
        due_date: str,
        lines: list[InvoiceLineInput],
        currency_code: Optional[str] = None,
        exchange_rate_micros: Optional[int] = None,
        memo: str = "",
        txn_number: Optional[str] = None,
        tax_bps: int = 0,
    ) -> int:
        if not lines:
            raise AccountingError("Invoice must contain at least one line item.")
        customer = self.get_customer(customer_id)
        ccy = (currency_code or customer["currency_code"]).upper()
        rate = exchange_rate_micros if exchange_rate_micros is not None else self.get_exchange_rate(ccy)
        ar_account = self.get_account_by_detail_type("Accounts Receivable", ccy)
        now = utc_now_iso()

        with self.conn:
            cur = self.conn.cursor()
            ref = txn_number or self._next_txn_number(cur, "INV", "Invoice")
            cur.execute(
                """
                INSERT INTO transactions (
                    txn_type, txn_number, txn_date, due_date, entity_id, currency_code,
                    exchange_rate_micros, memo, status, total_foreign, total_home,
                    created_at, updated_at, voided
                ) VALUES ('Invoice', ?, ?, ?, ?, ?, ?, ?, 'Open', 0, 0, ?, ?, 0)
                """,
                (ref, txn_date, due_date, customer_id, ccy, rate, memo, now, now),
            )
            txn_id = int(cur.lastrowid)

            je_lines: list[JournalLineInput] = []
            subtotal_foreign = 0
            subtotal_home = 0

            for line in lines:
                if line.quantity <= 0:
                    raise AccountingError("Invoice line quantity must be > 0.")
                if line.rate_foreign < 0:
                    raise AccountingError("Invoice line rate cannot be negative.")
                amt_foreign = calculate_line_amount(line.quantity, line.rate_foreign)
                amt_home = convert_to_home(amt_foreign, rate)
                subtotal_foreign += amt_foreign
                subtotal_home += amt_home

                item = self.get_item(line.item_id) if line.item_id is not None else None
                income_acct_id = (
                    line.account_id
                    or (item["income_account_id"] if item else None)
                    or self.get_account_by_detail_type("Sales")["id"]
                )

                cur.execute(
                    """
                    INSERT INTO transaction_lines (
                        transaction_id, item_id, description, quantity,
                        rate_foreign, amount_foreign, amount_home, account_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        txn_id,
                        line.item_id,
                        line.description or (item["name"] if item else ""),
                        line.quantity,
                        line.rate_foreign,
                        amt_foreign,
                        amt_home,
                        income_acct_id,
                    ),
                )

                if amt_home > 0:
                    je_lines.append(
                        JournalLineInput(
                            account_id=income_acct_id,
                            credit_foreign=amt_foreign,
                            credit_home=amt_home,
                            entity_type="Customer",
                            entity_id=customer_id,
                        )
                    )

                # FIFO COGS + Inventory Asset consumption for Inventory items
                if item and item["type"] == "Inventory":
                    cogs_home = self._consume_fifo_layers(cur, item["id"], txn_id, line.quantity)
                    if cogs_home > 0:
                        cogs_acct_id = (
                            item["expense_account_id"]
                            or self.get_account_by_detail_type("Cost of Goods Sold")["id"]
                        )
                        inv_acct_id = (
                            item["asset_account_id"]
                            or self.get_account_by_detail_type("Inventory Asset")["id"]
                        )
                        je_lines.append(
                            JournalLineInput(
                                account_id=cogs_acct_id,
                                debit_foreign=cogs_home,
                                debit_home=cogs_home,
                                entity_type="Customer",
                                entity_id=customer_id,
                            )
                        )
                        je_lines.append(
                            JournalLineInput(
                                account_id=inv_acct_id,
                                credit_foreign=cogs_home,
                                credit_home=cogs_home,
                                entity_type="Customer",
                                entity_id=customer_id,
                            )
                        )

            tax_foreign = apply_tax_bps(subtotal_foreign, tax_bps)
            tax_home = convert_to_home(tax_foreign, rate) if tax_foreign > 0 else 0
            if tax_home > 0:
                tax_acct = self.get_account_by_detail_type("Sales Tax Payable")
                je_lines.append(
                    JournalLineInput(
                        account_id=tax_acct["id"],
                        credit_foreign=tax_foreign,
                        credit_home=tax_home,
                        entity_type="Customer",
                        entity_id=customer_id,
                    )
                )

            total_foreign = subtotal_foreign + tax_foreign
            total_home = subtotal_home + tax_home

            if total_home <= 0:
                raise AccountingError("Invoice total must be greater than zero.")

            # Debit Accounts Receivable
            je_lines.insert(
                0,
                JournalLineInput(
                    account_id=ar_account["id"],
                    debit_foreign=total_foreign,
                    debit_home=total_home,
                    entity_type="Customer",
                    entity_id=customer_id,
                ),
            )

            cur.execute(
                "UPDATE transactions SET total_foreign = ?, total_home = ? WHERE id = ?",
                (total_foreign, total_home, txn_id),
            )

            self._post_journal_entry_in_tx(
                cur,
                entry_date=txn_date,
                reference_no=ref,
                currency_code=ccy,
                exchange_rate_micros=rate,
                memo=memo or f"Invoice {ref} - {customer['name']}",
                lines=je_lines,
                transaction_id=txn_id,
            )
            return txn_id

    def create_sales_receipt(
        self,
        customer_id: int,
        txn_date: str,
        lines: list[InvoiceLineInput],
        deposit_account_id: Optional[int] = None,
        currency_code: Optional[str] = None,
        exchange_rate_micros: Optional[int] = None,
        memo: str = "",
        txn_number: Optional[str] = None,
        tax_bps: int = 0,
    ) -> int:
        if not lines:
            raise AccountingError("Sales Receipt must contain at least one line item.")
        customer = self.get_customer(customer_id)
        ccy = (currency_code or customer["currency_code"]).upper()
        rate = exchange_rate_micros if exchange_rate_micros is not None else self.get_exchange_rate(ccy)
        dep_acct = (
            self.get_account(deposit_account_id)
            if deposit_account_id
            else self.get_account_by_detail_type("Undeposited Funds")
        )
        now = utc_now_iso()

        with self.conn:
            cur = self.conn.cursor()
            ref = txn_number or self._next_txn_number(cur, "SR", "SalesReceipt")
            cur.execute(
                """
                INSERT INTO transactions (
                    txn_type, txn_number, txn_date, due_date, entity_id, currency_code,
                    exchange_rate_micros, memo, status, total_foreign, total_home,
                    created_at, updated_at, voided
                ) VALUES ('SalesReceipt', ?, ?, ?, ?, ?, ?, ?, 'Paid', 0, 0, ?, ?, 0)
                """,
                (ref, txn_date, txn_date, customer_id, ccy, rate, memo, now, now),
            )
            txn_id = int(cur.lastrowid)

            je_lines: list[JournalLineInput] = []
            subtotal_foreign = 0
            subtotal_home = 0

            for line in lines:
                if line.quantity <= 0:
                    raise AccountingError("Line quantity must be > 0.")
                amt_foreign = calculate_line_amount(line.quantity, line.rate_foreign)
                amt_home = convert_to_home(amt_foreign, rate)
                subtotal_foreign += amt_foreign
                subtotal_home += amt_home

                item = self.get_item(line.item_id) if line.item_id is not None else None
                income_acct_id = (
                    line.account_id
                    or (item["income_account_id"] if item else None)
                    or self.get_account_by_detail_type("Sales")["id"]
                )

                cur.execute(
                    """
                    INSERT INTO transaction_lines (
                        transaction_id, item_id, description, quantity,
                        rate_foreign, amount_foreign, amount_home, account_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        txn_id,
                        line.item_id,
                        line.description or (item["name"] if item else ""),
                        line.quantity,
                        line.rate_foreign,
                        amt_foreign,
                        amt_home,
                        income_acct_id,
                    ),
                )

                if amt_home > 0:
                    je_lines.append(
                        JournalLineInput(
                            account_id=income_acct_id,
                            credit_foreign=amt_foreign,
                            credit_home=amt_home,
                            entity_type="Customer",
                            entity_id=customer_id,
                        )
                    )

                if item and item["type"] == "Inventory":
                    cogs_home = self._consume_fifo_layers(cur, item["id"], txn_id, line.quantity)
                    if cogs_home > 0:
                        cogs_acct_id = (
                            item["expense_account_id"]
                            or self.get_account_by_detail_type("Cost of Goods Sold")["id"]
                        )
                        inv_acct_id = (
                            item["asset_account_id"]
                            or self.get_account_by_detail_type("Inventory Asset")["id"]
                        )
                        je_lines.append(
                            JournalLineInput(
                                account_id=cogs_acct_id,
                                debit_foreign=cogs_home,
                                debit_home=cogs_home,
                                entity_type="Customer",
                                entity_id=customer_id,
                            )
                        )
                        je_lines.append(
                            JournalLineInput(
                                account_id=inv_acct_id,
                                credit_foreign=cogs_home,
                                credit_home=cogs_home,
                                entity_type="Customer",
                                entity_id=customer_id,
                            )
                        )

            tax_foreign = apply_tax_bps(subtotal_foreign, tax_bps)
            tax_home = convert_to_home(tax_foreign, rate) if tax_foreign > 0 else 0
            if tax_home > 0:
                tax_acct = self.get_account_by_detail_type("Sales Tax Payable")
                je_lines.append(
                    JournalLineInput(
                        account_id=tax_acct["id"],
                        credit_foreign=tax_foreign,
                        credit_home=tax_home,
                        entity_type="Customer",
                        entity_id=customer_id,
                    )
                )

            total_foreign = subtotal_foreign + tax_foreign
            total_home = subtotal_home + tax_home
            if total_home <= 0:
                raise AccountingError("Sales Receipt total must be greater than zero.")

            dep_debit_foreign = total_foreign if dep_acct["currency_code"] == ccy else total_home
            je_lines.insert(
                0,
                JournalLineInput(
                    account_id=dep_acct["id"],
                    debit_foreign=dep_debit_foreign,
                    debit_home=total_home,
                    entity_type="Customer",
                    entity_id=customer_id,
                ),
            )
            cur.execute(
                "UPDATE transactions SET total_foreign = ?, total_home = ? WHERE id = ?",
                (total_foreign, total_home, txn_id),
            )
            self._post_journal_entry_in_tx(
                cur,
                entry_date=txn_date,
                reference_no=ref,
                currency_code=ccy,
                exchange_rate_micros=rate,
                memo=memo or f"Sales Receipt {ref} - {customer['name']}",
                lines=je_lines,
                transaction_id=txn_id,
            )
            return txn_id

    def get_open_invoices(self, customer_id: Optional[int] = None) -> list[dict[str, Any]]:
        clause = "AND t.entity_id = ?" if customer_id is not None else ""
        params = [customer_id] if customer_id is not None else []
        rows = self.conn.execute(
            f"""
            SELECT
                t.*,
                c.name AS customer_name,
                COALESCE((
                    SELECT SUM(pa.applied_foreign)
                    FROM payment_applications pa
                    JOIN transactions pt ON pt.id = pa.payment_txn_id
                    WHERE pa.target_txn_id = t.id AND pt.voided = 0
                ), 0) AS paid_foreign,
                COALESCE((
                    SELECT SUM(pa.target_home_amount)
                    FROM payment_applications pa
                    JOIN transactions pt ON pt.id = pa.payment_txn_id
                    WHERE pa.target_txn_id = t.id AND pt.voided = 0
                ), 0) AS cleared_home
            FROM transactions t
            JOIN customers c ON c.id = t.entity_id
            WHERE t.txn_type = 'Invoice' AND t.voided = 0 AND t.status IN ('Open', 'Partial', 'Overdue')
            {clause}
            ORDER BY t.due_date ASC, t.txn_date ASC, t.id ASC
            """,
            params,
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["open_foreign"] = int(d["total_foreign"]) - int(d["paid_foreign"])
            d["open_home"] = int(d["total_home"]) - int(d["cleared_home"])
            if d["open_foreign"] > 0:
                out.append(d)
        return out

    def list_customer_invoices(
        self,
        customer_id: Optional[int] = None,
        status_filter: Optional[str] = None,
        include_voided: bool = False,
    ) -> list[dict[str, Any]]:
        """Return all invoices for a specific customer (or all customers) with paid/open amounts."""
        clauses = ["t.txn_type = 'Invoice'"]
        params: list[Any] = []
        if not include_voided:
            clauses.append("t.voided = 0")
        if customer_id is not None:
            clauses.append("t.entity_id = ?")
            params.append(customer_id)
        if status_filter and status_filter != "All":
            clauses.append("t.status = ?")
            params.append(status_filter)
        where_sql = " AND ".join(clauses)

        rows = self.conn.execute(
            f"""
            SELECT
                t.*,
                c.name AS customer_name,
                c.email AS customer_email,
                COALESCE((
                    SELECT SUM(pa.applied_foreign)
                    FROM payment_applications pa
                    JOIN transactions pt ON pt.id = pa.payment_txn_id
                    WHERE pa.target_txn_id = t.id AND pt.voided = 0
                ), 0) AS paid_foreign,
                COALESCE((
                    SELECT SUM(pa.target_home_amount)
                    FROM payment_applications pa
                    JOIN transactions pt ON pt.id = pa.payment_txn_id
                    WHERE pa.target_txn_id = t.id AND pt.voided = 0
                ), 0) AS cleared_home,
                (SELECT COUNT(*) FROM transaction_lines tl WHERE tl.transaction_id = t.id) AS line_count
            FROM transactions t
            JOIN customers c ON c.id = t.entity_id
            WHERE {where_sql}
            ORDER BY t.txn_date DESC, t.id DESC
            """,
            params,
        ).fetchall()

        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            if d["voided"]:
                d["open_foreign"] = 0
                d["open_home"] = 0
            else:
                d["open_foreign"] = int(d["total_foreign"]) - int(d["paid_foreign"])
                d["open_home"] = int(d["total_home"]) - int(d["cleared_home"])
            out.append(d)
        return out

    def get_invoice_details(self, invoice_txn_id: int) -> dict[str, Any]:
        """Return full invoice header, customer info, line items, applied payments, and FIFO COGS."""
        inv_row = self.conn.execute(
            """
            SELECT t.*, c.name AS customer_name, c.company_name AS customer_company,
                   c.email AS customer_email, c.billing_address AS customer_address,
                   c.terms AS customer_terms
            FROM transactions t
            JOIN customers c ON c.id = t.entity_id
            WHERE t.id = ? AND t.txn_type = 'Invoice'
            """,
            (invoice_txn_id,),
        ).fetchone()
        if inv_row is None:
            raise AccountingError(f"Invoice ID {invoice_txn_id} not found.")
        inv = dict(inv_row)

        line_rows = self.conn.execute(
            """
            SELECT tl.*, i.sku AS item_sku, i.name AS item_name, i.type AS item_type
            FROM transaction_lines tl
            LEFT JOIN items i ON i.id = tl.item_id
            WHERE tl.transaction_id = ?
            ORDER BY tl.id ASC
            """,
            (invoice_txn_id,),
        ).fetchall()

        pay_rows = self.conn.execute(
            """
            SELECT pa.*, pt.txn_number AS payment_number, pt.txn_date AS payment_date,
                   pt.exchange_rate_micros AS payment_rate_micros, pt.memo AS payment_memo
            FROM payment_applications pa
            JOIN transactions pt ON pt.id = pa.payment_txn_id
            WHERE pa.target_txn_id = ? AND pt.voided = 0
            ORDER BY pt.txn_date ASC, pt.id ASC
            """,
            (invoice_txn_id,),
        ).fetchall()

        cogs_row = self.conn.execute(
            "SELECT COALESCE(SUM(total_cost_home), 0) AS cogs FROM fifo_consumptions WHERE sale_txn_id = ?",
            (invoice_txn_id,),
        ).fetchone()

        paid_foreign = sum(int(p["applied_foreign"]) for p in pay_rows)
        cleared_home = sum(int(p["target_home_amount"]) for p in pay_rows)
        inv["paid_foreign"] = paid_foreign
        inv["cleared_home"] = cleared_home
        inv["open_foreign"] = 0 if inv["voided"] else int(inv["total_foreign"]) - paid_foreign
        inv["open_home"] = 0 if inv["voided"] else int(inv["total_home"]) - cleared_home
        inv["lines"] = [dict(r) for r in line_rows]
        inv["payments"] = [dict(r) for r in pay_rows]
        inv["fifo_cogs_home"] = int(cogs_row["cogs"])
        return inv

    def receive_payment(
        self,
        customer_id: int,
        txn_date: str,
        applications: list[tuple[int, int]],  # [(invoice_txn_id, applied_foreign_cents)]
        currency_code: Optional[str] = None,
        exchange_rate_micros: Optional[int] = None,
        deposit_account_id: Optional[int] = None,
        memo: str = "",
        txn_number: Optional[str] = None,
    ) -> int:
        if not applications:
            raise AccountingError("Select at least one invoice to apply payment.")
        customer = self.get_customer(customer_id)
        ccy = (currency_code or customer["currency_code"]).upper()
        rate = exchange_rate_micros if exchange_rate_micros is not None else self.get_exchange_rate(ccy)
        dep_acct = (
            self.get_account(deposit_account_id)
            if deposit_account_id
            else self.get_account_by_detail_type("Undeposited Funds")
        )
        ar_acct = self.get_account_by_detail_type("Accounts Receivable", ccy)
        fx_acct = self.get_account_by_detail_type("Exchange Gain/Loss")
        now = utc_now_iso()

        open_map = {inv["id"]: inv for inv in self.get_open_invoices(customer_id)}

        with self.conn:
            cur = self.conn.cursor()
            ref = txn_number or self._next_txn_number(cur, "PMT", "Payment")
            cur.execute(
                """
                INSERT INTO transactions (
                    txn_type, txn_number, txn_date, due_date, entity_id, currency_code,
                    exchange_rate_micros, memo, status, total_foreign, total_home,
                    created_at, updated_at, voided
                ) VALUES ('Payment', ?, ?, NULL, ?, ?, ?, ?, 'Paid', 0, 0, ?, ?, 0)
                """,
                (ref, txn_date, customer_id, ccy, rate, memo, now, now),
            )
            pay_txn_id = int(cur.lastrowid)

            total_applied_foreign = 0
            total_cash_home = 0
            net_fx_gain_home = 0  # positive = FX gain (Credit), negative = FX loss (Debit)
            je_lines: list[JournalLineInput] = []

            for inv_id, applied_foreign in applications:
                if applied_foreign <= 0:
                    raise AccountingError("Applied payment amount must be > 0.")
                inv = open_map.get(inv_id)
                if inv is None:
                    raise AccountingError(f"Invoice ID {inv_id} is not open for this customer.")
                if inv["currency_code"] != ccy:
                    raise AccountingError(
                        f"Payment currency ({ccy}) must match Invoice {inv['txn_number']} currency ({inv['currency_code']})."
                    )
                open_foreign = int(inv["open_foreign"])
                open_home = int(inv["open_home"])
                if applied_foreign > open_foreign:
                    raise AccountingError(
                        f"Applied amount ({applied_foreign}) exceeds open balance ({open_foreign}) on {inv['txn_number']}."
                    )

                # Clear exact remaining home balance on full settlement so AR hits $0.00 cleanly
                if applied_foreign == open_foreign:
                    orig_home_to_clear = open_home
                    new_status = "Paid"
                else:
                    orig_home_to_clear = (open_home * applied_foreign + open_foreign // 2) // open_foreign
                    new_status = "Partial"

                cash_home = convert_to_home(applied_foreign, rate)
                fx_gain = cash_home - orig_home_to_clear

                total_applied_foreign += applied_foreign
                total_cash_home += cash_home
                net_fx_gain_home += fx_gain

                cur.execute(
                    """
                    INSERT INTO payment_applications (
                        payment_txn_id, target_txn_id, applied_foreign,
                        target_home_amount, payment_home_amount, fx_gain_loss_home
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (pay_txn_id, inv_id, applied_foreign, orig_home_to_clear, cash_home, fx_gain),
                )
                cur.execute(
                    "UPDATE transactions SET status = ?, updated_at = ? WHERE id = ?",
                    (new_status, now, inv_id),
                )

                # Credit AR for the invoice's original home amount (and foreign amount)
                je_lines.append(
                    JournalLineInput(
                        account_id=ar_acct["id"],
                        credit_foreign=applied_foreign,
                        credit_home=orig_home_to_clear,
                        entity_type="Customer",
                        entity_id=customer_id,
                    )
                )

            # Debit Deposit / Undeposited Funds account
            dep_debit_foreign = total_applied_foreign if dep_acct["currency_code"] == ccy else total_cash_home
            je_lines.insert(
                0,
                JournalLineInput(
                    account_id=dep_acct["id"],
                    debit_foreign=dep_debit_foreign,
                    debit_home=total_cash_home,
                    entity_type="Customer",
                    entity_id=customer_id,
                ),
            )

            # Realized Exchange Gain or Loss
            if net_fx_gain_home > 0:
                je_lines.append(
                    JournalLineInput(
                        account_id=fx_acct["id"],
                        credit_foreign=net_fx_gain_home,
                        credit_home=net_fx_gain_home,
                        entity_type="Customer",
                        entity_id=customer_id,
                    )
                )
            elif net_fx_gain_home < 0:
                je_lines.append(
                    JournalLineInput(
                        account_id=fx_acct["id"],
                        debit_foreign=-net_fx_gain_home,
                        debit_home=-net_fx_gain_home,
                        entity_type="Customer",
                        entity_id=customer_id,
                    )
                )

            cur.execute(
                "UPDATE transactions SET total_foreign = ?, total_home = ? WHERE id = ?",
                (total_applied_foreign, total_cash_home, pay_txn_id),
            )
            self._post_journal_entry_in_tx(
                cur,
                entry_date=txn_date,
                reference_no=ref,
                currency_code=ccy,
                exchange_rate_micros=rate,
                memo=memo or f"Customer Payment {ref} - {customer['name']}",
                lines=je_lines,
                transaction_id=pay_txn_id,
            )
            return pay_txn_id

    # ------------------------------------------------------------------
    # Accounts Payable: Enter Bills, Pay Bills, Write Checks / Expenses
    # ------------------------------------------------------------------

    def create_bill(
        self,
        vendor_id: int,
        txn_date: str,
        due_date: str,
        lines: list[BillLineInput],
        currency_code: Optional[str] = None,
        exchange_rate_micros: Optional[int] = None,
        memo: str = "",
        txn_number: Optional[str] = None,
    ) -> int:
        if not lines:
            raise AccountingError("Bill must contain at least one line.")
        vendor = self.get_vendor(vendor_id)
        ccy = (currency_code or vendor["currency_code"]).upper()
        rate = exchange_rate_micros if exchange_rate_micros is not None else self.get_exchange_rate(ccy)
        ap_acct = self.get_account_by_detail_type("Accounts Payable", ccy)
        now = utc_now_iso()

        with self.conn:
            cur = self.conn.cursor()
            ref = txn_number or self._next_txn_number(cur, "BILL", "Bill")
            cur.execute(
                """
                INSERT INTO transactions (
                    txn_type, txn_number, txn_date, due_date, entity_id, currency_code,
                    exchange_rate_micros, memo, status, total_foreign, total_home,
                    created_at, updated_at, voided
                ) VALUES ('Bill', ?, ?, ?, ?, ?, ?, ?, 'Open', 0, 0, ?, ?, 0)
                """,
                (ref, txn_date, due_date, vendor_id, ccy, rate, memo, now, now),
            )
            txn_id = int(cur.lastrowid)

            je_lines: list[JournalLineInput] = []
            total_foreign = 0
            total_home = 0

            for line in lines:
                if line.quantity <= 0:
                    raise AccountingError("Bill line quantity must be > 0.")
                if line.rate_foreign < 0:
                    raise AccountingError("Bill line rate cannot be negative.")
                amt_foreign = calculate_line_amount(line.quantity, line.rate_foreign)

                item = self.get_item(line.item_id) if line.item_id is not None else None
                if item and item["type"] == "Inventory":
                    unit_cost_home = convert_to_home(line.rate_foreign, rate)
                    amt_home = line.quantity * unit_cost_home
                    debit_acct_id = (
                        item["asset_account_id"]
                        or self.get_account_by_detail_type("Inventory Asset")["id"]
                    )
                    self._add_fifo_layer(cur, item["id"], txn_id, txn_date, line.quantity, unit_cost_home)
                else:
                    amt_home = convert_to_home(amt_foreign, rate)
                    debit_acct_id = (
                        line.account_id
                        or (item["expense_account_id"] if item else None)
                        or self.get_account_by_detail_type("Operating Expense")["id"]
                    )

                total_foreign += amt_foreign
                total_home += amt_home

                cur.execute(
                    """
                    INSERT INTO transaction_lines (
                        transaction_id, item_id, description, quantity,
                        rate_foreign, amount_foreign, amount_home, account_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        txn_id,
                        line.item_id,
                        line.description or (item["name"] if item else ""),
                        line.quantity,
                        line.rate_foreign,
                        amt_foreign,
                        amt_home,
                        debit_acct_id,
                    ),
                )

                if amt_home > 0:
                    je_lines.append(
                        JournalLineInput(
                            account_id=debit_acct_id,
                            debit_foreign=amt_home if (item and item["type"] == "Inventory") else amt_foreign,
                            debit_home=amt_home,
                            entity_type="Vendor",
                            entity_id=vendor_id,
                        )
                    )

            if total_home <= 0:
                raise AccountingError("Bill total must be greater than zero.")

            je_lines.append(
                JournalLineInput(
                    account_id=ap_acct["id"],
                    credit_foreign=total_foreign,
                    credit_home=total_home,
                    entity_type="Vendor",
                    entity_id=vendor_id,
                )
            )

            cur.execute(
                "UPDATE transactions SET total_foreign = ?, total_home = ? WHERE id = ?",
                (total_foreign, total_home, txn_id),
            )
            self._post_journal_entry_in_tx(
                cur,
                entry_date=txn_date,
                reference_no=ref,
                currency_code=ccy,
                exchange_rate_micros=rate,
                memo=memo or f"Bill {ref} - {vendor['name']}",
                lines=je_lines,
                transaction_id=txn_id,
            )
            return txn_id

    def get_open_bills(self, vendor_id: Optional[int] = None) -> list[dict[str, Any]]:
        clause = "AND t.entity_id = ?" if vendor_id is not None else ""
        params = [vendor_id] if vendor_id is not None else []
        rows = self.conn.execute(
            f"""
            SELECT
                t.*,
                v.name AS vendor_name,
                COALESCE((
                    SELECT SUM(pa.applied_foreign)
                    FROM payment_applications pa
                    JOIN transactions pt ON pt.id = pa.payment_txn_id
                    WHERE pa.target_txn_id = t.id AND pt.voided = 0
                ), 0) AS paid_foreign,
                COALESCE((
                    SELECT SUM(pa.target_home_amount)
                    FROM payment_applications pa
                    JOIN transactions pt ON pt.id = pa.payment_txn_id
                    WHERE pa.target_txn_id = t.id AND pt.voided = 0
                ), 0) AS cleared_home
            FROM transactions t
            JOIN vendors v ON v.id = t.entity_id
            WHERE t.txn_type = 'Bill' AND t.voided = 0 AND t.status IN ('Open', 'Partial', 'Overdue')
            {clause}
            ORDER BY t.due_date ASC, t.txn_date ASC, t.id ASC
            """,
            params,
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["open_foreign"] = int(d["total_foreign"]) - int(d["paid_foreign"])
            d["open_home"] = int(d["total_home"]) - int(d["cleared_home"])
            if d["open_foreign"] > 0:
                out.append(d)
        return out

    def pay_bills(
        self,
        vendor_id: int,
        txn_date: str,
        applications: list[tuple[int, int]],  # [(bill_txn_id, applied_foreign_cents)]
        bank_account_id: Optional[int] = None,
        currency_code: Optional[str] = None,
        exchange_rate_micros: Optional[int] = None,
        memo: str = "",
        txn_number: Optional[str] = None,
    ) -> int:
        if not applications:
            raise AccountingError("Select at least one bill to pay.")
        vendor = self.get_vendor(vendor_id)
        ccy = (currency_code or vendor["currency_code"]).upper()
        rate = exchange_rate_micros if exchange_rate_micros is not None else self.get_exchange_rate(ccy)
        bank_acct = (
            self.get_account(bank_account_id)
            if bank_account_id
            else self.get_account_by_detail_type("Checking")
        )
        ap_acct = self.get_account_by_detail_type("Accounts Payable", ccy)
        fx_acct = self.get_account_by_detail_type("Exchange Gain/Loss")
        now = utc_now_iso()

        open_map = {b["id"]: b for b in self.get_open_bills(vendor_id)}

        with self.conn:
            cur = self.conn.cursor()
            ref = txn_number or self._next_txn_number(cur, "BP", "BillPayment")
            cur.execute(
                """
                INSERT INTO transactions (
                    txn_type, txn_number, txn_date, due_date, entity_id, currency_code,
                    exchange_rate_micros, memo, status, total_foreign, total_home,
                    created_at, updated_at, voided
                ) VALUES ('BillPayment', ?, ?, NULL, ?, ?, ?, ?, 'Paid', 0, 0, ?, ?, 0)
                """,
                (ref, txn_date, vendor_id, ccy, rate, memo, now, now),
            )
            pay_txn_id = int(cur.lastrowid)

            total_applied_foreign = 0
            total_cash_home = 0
            net_fx_loss_home = 0  # positive = paid more home -> FX loss (Debit), negative = FX gain (Credit)
            je_lines: list[JournalLineInput] = []

            for bill_id, applied_foreign in applications:
                if applied_foreign <= 0:
                    raise AccountingError("Applied bill payment must be > 0.")
                bill = open_map.get(bill_id)
                if bill is None:
                    raise AccountingError(f"Bill ID {bill_id} is not open for this vendor.")
                if bill["currency_code"] != ccy:
                    raise AccountingError(
                        f"Payment currency ({ccy}) must match Bill {bill['txn_number']} currency ({bill['currency_code']})."
                    )
                open_foreign = int(bill["open_foreign"])
                open_home = int(bill["open_home"])
                if applied_foreign > open_foreign:
                    raise AccountingError(
                        f"Applied amount ({applied_foreign}) exceeds open balance ({open_foreign}) on {bill['txn_number']}."
                    )

                if applied_foreign == open_foreign:
                    orig_home_to_clear = open_home
                    new_status = "Paid"
                else:
                    orig_home_to_clear = (open_home * applied_foreign + open_foreign // 2) // open_foreign
                    new_status = "Partial"

                cash_home = convert_to_home(applied_foreign, rate)
                fx_loss = cash_home - orig_home_to_clear

                total_applied_foreign += applied_foreign
                total_cash_home += cash_home
                net_fx_loss_home += fx_loss

                cur.execute(
                    """
                    INSERT INTO payment_applications (
                        payment_txn_id, target_txn_id, applied_foreign,
                        target_home_amount, payment_home_amount, fx_gain_loss_home
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (pay_txn_id, bill_id, applied_foreign, orig_home_to_clear, cash_home, -fx_loss),
                )
                cur.execute(
                    "UPDATE transactions SET status = ?, updated_at = ? WHERE id = ?",
                    (new_status, now, bill_id),
                )

                je_lines.append(
                    JournalLineInput(
                        account_id=ap_acct["id"],
                        debit_foreign=applied_foreign,
                        debit_home=orig_home_to_clear,
                        entity_type="Vendor",
                        entity_id=vendor_id,
                    )
                )

            bank_credit_foreign = total_applied_foreign if bank_acct["currency_code"] == ccy else total_cash_home
            je_lines.append(
                JournalLineInput(
                    account_id=bank_acct["id"],
                    credit_foreign=bank_credit_foreign,
                    credit_home=total_cash_home,
                    entity_type="Vendor",
                    entity_id=vendor_id,
                )
            )

            if net_fx_loss_home > 0:
                je_lines.append(
                    JournalLineInput(
                        account_id=fx_acct["id"],
                        debit_foreign=net_fx_loss_home,
                        debit_home=net_fx_loss_home,
                        entity_type="Vendor",
                        entity_id=vendor_id,
                    )
                )
            elif net_fx_loss_home < 0:
                je_lines.append(
                    JournalLineInput(
                        account_id=fx_acct["id"],
                        credit_foreign=-net_fx_loss_home,
                        credit_home=-net_fx_loss_home,
                        entity_type="Vendor",
                        entity_id=vendor_id,
                    )
                )

            cur.execute(
                "UPDATE transactions SET total_foreign = ?, total_home = ? WHERE id = ?",
                (total_applied_foreign, total_cash_home, pay_txn_id),
            )
            self._post_journal_entry_in_tx(
                cur,
                entry_date=txn_date,
                reference_no=ref,
                currency_code=ccy,
                exchange_rate_micros=rate,
                memo=memo or f"Bill Payment {ref} - {vendor['name']}",
                lines=je_lines,
                transaction_id=pay_txn_id,
            )
            return pay_txn_id

    def write_check(
        self,
        txn_date: str,
        lines: list[BillLineInput],
        bank_account_id: Optional[int] = None,
        vendor_id: Optional[int] = None,
        currency_code: Optional[str] = None,
        exchange_rate_micros: Optional[int] = None,
        memo: str = "",
        txn_number: Optional[str] = None,
        txn_type: str = "Check",
    ) -> int:
        if not lines:
            raise AccountingError("Check/Expense must have at least one line.")
        if txn_type not in ("Check", "Expense"):
            raise AccountingError("txn_type must be 'Check' or 'Expense'.")
        bank_acct = (
            self.get_account(bank_account_id)
            if bank_account_id
            else self.get_account_by_detail_type("Checking")
        )
        ccy = (currency_code or bank_acct["currency_code"]).upper()
        rate = exchange_rate_micros if exchange_rate_micros is not None else self.get_exchange_rate(ccy)
        now = utc_now_iso()
        entity_type = "Vendor" if vendor_id is not None else "None"

        with self.conn:
            cur = self.conn.cursor()
            prefix = "CHK" if txn_type == "Check" else "EXP"
            ref = txn_number or self._next_txn_number(cur, prefix, txn_type)
            cur.execute(
                """
                INSERT INTO transactions (
                    txn_type, txn_number, txn_date, due_date, entity_id, currency_code,
                    exchange_rate_micros, memo, status, total_foreign, total_home,
                    created_at, updated_at, voided
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, 'Paid', 0, 0, ?, ?, 0)
                """,
                (txn_type, ref, txn_date, vendor_id, ccy, rate, memo, now, now),
            )
            txn_id = int(cur.lastrowid)

            je_lines: list[JournalLineInput] = []
            total_foreign = 0
            total_home = 0

            for line in lines:
                if line.quantity <= 0:
                    raise AccountingError("Line quantity must be > 0.")
                amt_foreign = calculate_line_amount(line.quantity, line.rate_foreign)
                item = self.get_item(line.item_id) if line.item_id is not None else None

                if item and item["type"] == "Inventory":
                    unit_cost_home = convert_to_home(line.rate_foreign, rate)
                    amt_home = line.quantity * unit_cost_home
                    debit_acct_id = (
                        item["asset_account_id"]
                        or self.get_account_by_detail_type("Inventory Asset")["id"]
                    )
                    self._add_fifo_layer(cur, item["id"], txn_id, txn_date, line.quantity, unit_cost_home)
                else:
                    amt_home = convert_to_home(amt_foreign, rate)
                    debit_acct_id = (
                        line.account_id
                        or (item["expense_account_id"] if item else None)
                        or self.get_account_by_detail_type("Operating Expense")["id"]
                    )

                total_foreign += amt_foreign
                total_home += amt_home

                cur.execute(
                    """
                    INSERT INTO transaction_lines (
                        transaction_id, item_id, description, quantity,
                        rate_foreign, amount_foreign, amount_home, account_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        txn_id,
                        line.item_id,
                        line.description or (item["name"] if item else ""),
                        line.quantity,
                        line.rate_foreign,
                        amt_foreign,
                        amt_home,
                        debit_acct_id,
                    ),
                )
                if amt_home > 0:
                    je_lines.append(
                        JournalLineInput(
                            account_id=debit_acct_id,
                            debit_foreign=amt_home if (item and item["type"] == "Inventory") else amt_foreign,
                            debit_home=amt_home,
                            entity_type=entity_type,
                            entity_id=vendor_id,
                        )
                    )

            if total_home <= 0:
                raise AccountingError("Check/Expense total must be > 0.")

            bank_credit_foreign = total_foreign if bank_acct["currency_code"] == ccy else total_home
            je_lines.append(
                JournalLineInput(
                    account_id=bank_acct["id"],
                    credit_foreign=bank_credit_foreign,
                    credit_home=total_home,
                    entity_type=entity_type,
                    entity_id=vendor_id,
                )
            )
            cur.execute(
                "UPDATE transactions SET total_foreign = ?, total_home = ? WHERE id = ?",
                (total_foreign, total_home, txn_id),
            )
            self._post_journal_entry_in_tx(
                cur,
                entry_date=txn_date,
                reference_no=ref,
                currency_code=ccy,
                exchange_rate_micros=rate,
                memo=memo or f"{txn_type} {ref}",
                lines=je_lines,
                transaction_id=txn_id,
            )
            return txn_id

    # ------------------------------------------------------------------
    # Inventory Adjustments, Banking Deposits & Reconciliation
    # ------------------------------------------------------------------

    def adjust_inventory(
        self,
        item_id: int,
        txn_date: str,
        qty_delta: int,
        unit_cost_home: Optional[int] = None,
        offset_account_id: Optional[int] = None,
        memo: str = "",
        txn_number: Optional[str] = None,
    ) -> int:
        if qty_delta == 0:
            raise AccountingError("Inventory adjustment quantity delta cannot be 0.")
        item = self.get_item(item_id)
        if item["type"] != "Inventory":
            raise AccountingError("Inventory adjustments only apply to Inventory items.")

        home_ccy = self.get_home_currency()
        inv_acct_id = item["asset_account_id"] or self.get_account_by_detail_type("Inventory Asset")["id"]
        offset_acct_id = offset_account_id or self.get_account_by_detail_type("Inventory Adjustment")["id"]
        now = utc_now_iso()

        with self.conn:
            cur = self.conn.cursor()
            ref = txn_number or self._next_txn_number(cur, "INVADJ", "InventoryAdjust")
            cur.execute(
                """
                INSERT INTO transactions (
                    txn_type, txn_number, txn_date, due_date, entity_id, currency_code,
                    exchange_rate_micros, memo, status, total_foreign, total_home,
                    created_at, updated_at, voided
                ) VALUES ('InventoryAdjust', ?, ?, NULL, NULL, ?, ?, ?, 'Paid', 0, 0, ?, ?, 0)
                """,
                (ref, txn_date, home_ccy, RATE_SCALE, memo, now, now),
            )
            txn_id = int(cur.lastrowid)

            if qty_delta > 0:
                cost = int(unit_cost_home if unit_cost_home is not None else item["purchase_cost"])
                if cost <= 0:
                    raise AccountingError("Positive inventory adjustment requires unit_cost_home > 0.")
                total_home = qty_delta * cost
                self._add_fifo_layer(cur, item_id, txn_id, txn_date, qty_delta, cost)
                je_lines = [
                    JournalLineInput(account_id=inv_acct_id, debit_foreign=total_home, debit_home=total_home),
                    JournalLineInput(account_id=offset_acct_id, credit_foreign=total_home, credit_home=total_home),
                ]
            else:
                total_home = self._consume_fifo_layers(cur, item_id, txn_id, -qty_delta)
                je_lines = [
                    JournalLineInput(account_id=offset_acct_id, debit_foreign=total_home, debit_home=total_home),
                    JournalLineInput(account_id=inv_acct_id, credit_foreign=total_home, credit_home=total_home),
                ]

            cur.execute(
                "UPDATE transactions SET total_foreign = ?, total_home = ? WHERE id = ?",
                (total_home, total_home, txn_id),
            )
            self._post_journal_entry_in_tx(
                cur,
                entry_date=txn_date,
                reference_no=ref,
                currency_code=home_ccy,
                exchange_rate_micros=RATE_SCALE,
                memo=memo or f"Inventory Adjustment {ref} - {item['sku']}",
                lines=je_lines,
                transaction_id=txn_id,
            )
            return txn_id

    def make_deposit(
        self,
        target_bank_account_id: int,
        txn_date: str,
        amount_home: int,
        source_account_id: Optional[int] = None,
        memo: str = "",
        txn_number: Optional[str] = None,
    ) -> int:
        if amount_home <= 0:
            raise AccountingError("Deposit amount must be > 0.")
        src_acct = (
            self.get_account(source_account_id)
            if source_account_id
            else self.get_account_by_detail_type("Undeposited Funds")
        )
        dst_acct = self.get_account(target_bank_account_id)
        if src_acct["id"] == dst_acct["id"]:
            raise AccountingError("Source and target accounts must differ.")

        _, available_home = self.get_account_balance(src_acct["id"])
        if src_acct["detail_type"] == "Undeposited Funds" and amount_home > available_home:
            raise AccountingError(
                f"Deposit ({amount_home}) exceeds Undeposited Funds balance ({available_home})."
            )

        home_ccy = self.get_home_currency()
        now = utc_now_iso()
        with self.conn:
            cur = self.conn.cursor()
            ref = txn_number or self._next_txn_number(cur, "DEP", "Deposit")
            cur.execute(
                """
                INSERT INTO transactions (
                    txn_type, txn_number, txn_date, due_date, entity_id, currency_code,
                    exchange_rate_micros, memo, status, total_foreign, total_home,
                    created_at, updated_at, voided
                ) VALUES ('Deposit', ?, ?, NULL, NULL, ?, ?, ?, 'Paid', ?, ?, ?, ?, 0)
                """,
                (ref, txn_date, home_ccy, RATE_SCALE, memo, amount_home, amount_home, now, now),
            )
            txn_id = int(cur.lastrowid)
            je_lines = [
                JournalLineInput(account_id=dst_acct["id"], debit_foreign=amount_home, debit_home=amount_home),
                JournalLineInput(account_id=src_acct["id"], credit_foreign=amount_home, credit_home=amount_home),
            ]
            self._post_journal_entry_in_tx(
                cur,
                entry_date=txn_date,
                reference_no=ref,
                currency_code=home_ccy,
                exchange_rate_micros=RATE_SCALE,
                memo=memo or f"Bank Deposit {ref}",
                lines=je_lines,
                transaction_id=txn_id,
            )
            return txn_id

    def get_reconciliation_state(self, account_id: int) -> dict[str, Any]:
        """Return beginning reconciled balance and list of uncleared register lines."""
        acct = self.get_account(account_id)
        is_debit_normal = acct["account_type"] in DEBIT_NORMAL_TYPES
        rec_row = self.conn.execute(
            """
            SELECT
                COALESCE(SUM(jel.debit_home), 0) AS dh,
                COALESCE(SUM(jel.credit_home), 0) AS ch
            FROM journal_entry_lines jel
            JOIN journal_entries je ON je.id = jel.journal_entry_id
            WHERE jel.account_id = ? AND je.voided = 0 AND jel.is_reconciled = 1
            """,
            (account_id,),
        ).fetchone()
        beg_bal = (
            int(rec_row["dh"]) - int(rec_row["ch"])
            if is_debit_normal
            else int(rec_row["ch"]) - int(rec_row["dh"])
        )

        uncleared_rows = self.conn.execute(
            """
            SELECT
                jel.id AS jel_id,
                je.entry_date,
                je.reference_no,
                je.memo,
                COALESCE(t.txn_type, 'JournalEntry') AS txn_type,
                jel.debit_home,
                jel.credit_home
            FROM journal_entry_lines jel
            JOIN journal_entries je ON je.id = jel.journal_entry_id
            LEFT JOIN transactions t ON t.id = je.transaction_id
            WHERE jel.account_id = ? AND je.voided = 0 AND jel.is_reconciled = 0
            ORDER BY je.entry_date ASC, je.id ASC
            """,
            (account_id,),
        ).fetchall()

        uncleared = []
        for r in uncleared_rows:
            d = dict(r)
            dh, ch = int(d["debit_home"]), int(d["credit_home"])
            d["signed_home"] = (dh - ch) if is_debit_normal else (ch - dh)
            uncleared.append(d)

        return {
            "account": acct,
            "beginning_balance_home": beg_bal,
            "uncleared_lines": uncleared,
        }

    def reconcile_bank_account(
        self,
        account_id: int,
        statement_date: str,
        statement_ending_balance_home: int,
        cleared_jel_ids: list[int],
    ) -> int:
        state = self.get_reconciliation_state(account_id)
        beg_bal = int(state["beginning_balance_home"])
        uncleared_map = {u["jel_id"]: u for u in state["uncleared_lines"]}

        cleared_sum = 0
        for jid in cleared_jel_ids:
            if jid not in uncleared_map:
                raise AccountingError(f"Line {jid} is not an uncleared line for this account.")
            cleared_sum += int(uncleared_map[jid]["signed_home"])

        diff = statement_ending_balance_home - (beg_bal + cleared_sum)
        if diff != 0:
            raise AccountingError(
                f"Cannot reconcile: Difference must be $0.00 (currently {diff} cents)."
            )

        now = utc_now_iso()
        with self.conn:
            cur = self.conn.cursor()
            if cleared_jel_ids:
                placeholders = ",".join("?" for _ in cleared_jel_ids)
                cur.execute(
                    f"""
                    UPDATE journal_entry_lines
                    SET is_reconciled = 1, reconciled_date = ?
                    WHERE id IN ({placeholders})
                    """,
                    [statement_date] + cleared_jel_ids,
                )
            cur.execute(
                """
                INSERT INTO bank_reconciliations (
                    account_id, statement_date, statement_ending_balance_home,
                    beginning_balance_home, reconciled_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (account_id, statement_date, statement_ending_balance_home, beg_bal, now),
            )
            return int(cur.lastrowid)

    # ------------------------------------------------------------------
    # Transaction Listing & Voiding
    # ------------------------------------------------------------------

    def list_transactions(
        self,
        txn_types: Optional[tuple[str, ...]] = None,
        entity_id: Optional[int] = None,
        include_voided: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if not include_voided:
            clauses.append("t.voided = 0")
        if txn_types:
            placeholders = ",".join("?" for _ in txn_types)
            clauses.append(f"t.txn_type IN ({placeholders})")
            params.extend(txn_types)
        if entity_id is not None:
            clauses.append("t.entity_id = ?")
            params.append(entity_id)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT t.*,
                CASE
                    WHEN t.txn_type IN ('Invoice','Payment','SalesReceipt')
                        THEN (SELECT name FROM customers WHERE id = t.entity_id)
                    WHEN t.txn_type IN ('Bill','BillPayment','Check','Expense')
                        THEN (SELECT name FROM vendors WHERE id = t.entity_id)
                    ELSE ''
                END AS entity_name
            FROM transactions t
            {where_sql}
            ORDER BY t.txn_date DESC, t.id DESC
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def void_transaction(self, txn_id: int) -> None:
        """Void a transaction, reversing FIFO consumptions and payment applications."""
        now = utc_now_iso()
        with self.conn:
            cur = self.conn.cursor()
            txn = cur.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
            if txn is None:
                raise AccountingError(f"Transaction {txn_id} not found.")
            if txn["voided"]:
                return

            # Block voiding a purchase if its FIFO layers were already consumed
            consumed_check = cur.execute(
                """
                SELECT COUNT(*) AS c FROM inventory_fifo_layers
                WHERE purchase_txn_id = ? AND qty_remaining < initial_qty
                """,
                (txn_id,),
            ).fetchone()["c"]
            if consumed_check > 0:
                raise AccountingError(
                    "Cannot void purchase transaction because its FIFO inventory layers have already been sold."
                )

            # Remove unconsumed FIFO layers created by this purchase
            layers = cur.execute(
                "SELECT id, item_id, qty_remaining FROM inventory_fifo_layers WHERE purchase_txn_id = ?",
                (txn_id,),
            ).fetchall()
            for l in layers:
                cur.execute(
                    "UPDATE items SET qty_on_hand = qty_on_hand - ? WHERE id = ?",
                    (int(l["qty_remaining"]), int(l["item_id"])),
                )
                cur.execute("DELETE FROM inventory_fifo_layers WHERE id = ?", (int(l["id"]),))

            # Restore FIFO layers consumed by this sale
            consumptions = cur.execute(
                """
                SELECT fc.id, fc.layer_id, fc.qty_consumed, l.item_id
                FROM fifo_consumptions fc
                JOIN inventory_fifo_layers l ON l.id = fc.layer_id
                WHERE fc.sale_txn_id = ?
                """,
                (txn_id,),
            ).fetchall()
            for c in consumptions:
                cur.execute(
                    "UPDATE inventory_fifo_layers SET qty_remaining = qty_remaining + ? WHERE id = ?",
                    (int(c["qty_consumed"]), int(c["layer_id"])),
                )
                cur.execute(
                    "UPDATE items SET qty_on_hand = qty_on_hand + ? WHERE id = ?",
                    (int(c["qty_consumed"]), int(c["item_id"])),
                )
            cur.execute("DELETE FROM fifo_consumptions WHERE sale_txn_id = ?", (txn_id,))

            # Reopen targets if voiding a Payment or BillPayment
            apps = cur.execute(
                "SELECT target_txn_id FROM payment_applications WHERE payment_txn_id = ?",
                (txn_id,),
            ).fetchall()
            cur.execute("DELETE FROM payment_applications WHERE payment_txn_id = ?", (txn_id,))

            cur.execute(
                "UPDATE transactions SET voided = 1, status = 'Void', updated_at = ? WHERE id = ?",
                (now, txn_id),
            )
            cur.execute(
                "UPDATE journal_entries SET voided = 1, updated_at = ? WHERE transaction_id = ?",
                (now, txn_id),
            )

            for a in apps:
                target_id = int(a["target_txn_id"])
                paid_row = cur.execute(
                    """
                    SELECT COALESCE(SUM(pa.applied_foreign), 0) AS p
                    FROM payment_applications pa
                    JOIN transactions pt ON pt.id = pa.payment_txn_id
                    WHERE pa.target_txn_id = ? AND pt.voided = 0
                    """,
                    (target_id,),
                ).fetchone()
                new_status = "Partial" if int(paid_row["p"]) > 0 else "Open"
                cur.execute(
                    "UPDATE transactions SET status = ?, updated_at = ? WHERE id = ?",
                    (new_status, now, target_id),
                )

    # ------------------------------------------------------------------
    # Dashboard KPIs
    # ------------------------------------------------------------------

    def get_dashboard_summary(self, as_of_date: Optional[str] = None) -> dict[str, Any]:
        today_str = as_of_date or date.today().isoformat()
        year_start = f"{today_str[:4]}-01-01"

        bank_accounts = [
            a
            for a in self.list_accounts(account_types=("Asset",), as_of_date=today_str)
            if a["detail_type"] in ("Checking", "Savings", "Undeposited Funds")
        ]

        open_invs = self.get_open_invoices()
        open_inv_home = sum(int(i["open_home"]) for i in open_invs)

        open_bills = self.get_open_bills()
        overdue_bills_home = sum(
            int(b["open_home"]) for b in open_bills if (b["due_date"] or "") < today_str
        )
        open_bills_home = sum(int(b["open_home"]) for b in open_bills)

        inv_acct = self.get_account_by_detail_type("Inventory Asset")
        _, inv_value_home = self.get_account_balance(inv_acct["id"], as_of_date=today_str)

        # Net income YTD
        row = self.conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN a.account_type = 'Income' THEN jel.credit_home - jel.debit_home ELSE 0 END), 0) AS inc,
                COALESCE(SUM(CASE WHEN a.account_type = 'Expense' THEN jel.debit_home - jel.credit_home ELSE 0 END), 0) AS exp
            FROM journal_entry_lines jel
            JOIN journal_entries je ON je.id = jel.journal_entry_id
            JOIN accounts a ON a.id = jel.account_id
            WHERE je.voided = 0 AND je.entry_date >= ? AND je.entry_date <= ?
            """,
            (year_start, today_str),
        ).fetchone()
        net_income_ytd = int(row["inc"]) - int(row["exp"])

        return {
            "home_currency": self.get_home_currency(),
            "bank_accounts": bank_accounts,
            "open_invoices_count": len(open_invs),
            "open_invoices_home": open_inv_home,
            "open_bills_count": len(open_bills),
            "open_bills_home": open_bills_home,
            "overdue_bills_home": overdue_bills_home,
            "inventory_value_home": inv_value_home,
            "net_income_ytd_home": net_income_ytd,
        }
