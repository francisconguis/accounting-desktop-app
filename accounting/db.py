"""SQLite database management, schema initialization, WAL mode, backups, and seeding."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from accounting.money import RATE_SCALE

DEFAULT_DB_FILENAME = "company_file.db"

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS company_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    company_name TEXT NOT NULL DEFAULT 'My Company',
    address TEXT NOT NULL DEFAULT '',
    fiscal_year_start TEXT NOT NULL DEFAULT '01-01',
    home_currency_code TEXT NOT NULL DEFAULT 'USD',
    tax_rate_default_bps INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS currencies (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exchange_rate_micros INTEGER NOT NULL CHECK (exchange_rate_micros > 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_number TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    account_type TEXT NOT NULL CHECK (account_type IN ('Asset','Liability','Equity','Income','Expense')),
    detail_type TEXT NOT NULL,
    currency_code TEXT NOT NULL REFERENCES currencies(code),
    parent_account_id INTEGER REFERENCES accounts(id),
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    company_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    billing_address TEXT NOT NULL DEFAULT '',
    currency_code TEXT NOT NULL REFERENCES currencies(code),
    terms TEXT NOT NULL DEFAULT 'Net 30',
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    company_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    currency_code TEXT NOT NULL REFERENCES currencies(code),
    terms TEXT NOT NULL DEFAULT 'Net 30',
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('Service','Non-Inventory','Inventory')),
    description TEXT NOT NULL DEFAULT '',
    sales_price INTEGER NOT NULL DEFAULT 0,
    purchase_cost INTEGER NOT NULL DEFAULT 0,
    income_account_id INTEGER REFERENCES accounts(id),
    expense_account_id INTEGER REFERENCES accounts(id),
    asset_account_id INTEGER REFERENCES accounts(id),
    qty_on_hand INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_type TEXT NOT NULL CHECK (txn_type IN (
        'Invoice','Payment','SalesReceipt','Bill','BillPayment',
        'Check','Expense','Deposit','JournalEntry','InventoryAdjust'
    )),
    txn_number TEXT NOT NULL,
    txn_date TEXT NOT NULL,
    due_date TEXT,
    entity_id INTEGER,
    currency_code TEXT NOT NULL REFERENCES currencies(code),
    exchange_rate_micros INTEGER NOT NULL CHECK (exchange_rate_micros > 0),
    memo TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Open' CHECK (status IN ('Draft','Open','Partial','Paid','Overdue','Void')),
    total_foreign INTEGER NOT NULL DEFAULT 0,
    total_home INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    voided INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transaction_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    item_id INTEGER REFERENCES items(id),
    description TEXT NOT NULL DEFAULT '',
    quantity INTEGER NOT NULL DEFAULT 1,
    rate_foreign INTEGER NOT NULL DEFAULT 0,
    amount_foreign INTEGER NOT NULL DEFAULT 0,
    amount_home INTEGER NOT NULL DEFAULT 0,
    account_id INTEGER REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS inventory_fifo_layers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    purchase_txn_id INTEGER REFERENCES transactions(id),
    received_date TEXT NOT NULL,
    initial_qty INTEGER NOT NULL CHECK (initial_qty > 0),
    qty_remaining INTEGER NOT NULL CHECK (qty_remaining >= 0),
    unit_cost_home INTEGER NOT NULL CHECK (unit_cost_home >= 0)
);

CREATE TABLE IF NOT EXISTS fifo_consumptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sale_txn_id INTEGER NOT NULL REFERENCES transactions(id),
    layer_id INTEGER NOT NULL REFERENCES inventory_fifo_layers(id),
    qty_consumed INTEGER NOT NULL CHECK (qty_consumed > 0),
    unit_cost_home INTEGER NOT NULL,
    total_cost_home INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS journal_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER REFERENCES transactions(id),
    entry_date TEXT NOT NULL,
    reference_no TEXT NOT NULL DEFAULT '',
    currency_code TEXT NOT NULL REFERENCES currencies(code),
    exchange_rate_micros INTEGER NOT NULL CHECK (exchange_rate_micros > 0),
    memo TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    voided INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS journal_entry_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    journal_entry_id INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    entity_type TEXT NOT NULL DEFAULT 'None' CHECK (entity_type IN ('Customer','Vendor','None')),
    entity_id INTEGER,
    debit_foreign INTEGER NOT NULL DEFAULT 0 CHECK (debit_foreign >= 0),
    credit_foreign INTEGER NOT NULL DEFAULT 0 CHECK (credit_foreign >= 0),
    debit_home INTEGER NOT NULL DEFAULT 0 CHECK (debit_home >= 0),
    credit_home INTEGER NOT NULL DEFAULT 0 CHECK (credit_home >= 0),
    is_reconciled INTEGER NOT NULL DEFAULT 0,
    reconciled_date TEXT
);

CREATE TABLE IF NOT EXISTS payment_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_txn_id INTEGER NOT NULL REFERENCES transactions(id),
    target_txn_id INTEGER NOT NULL REFERENCES transactions(id),
    applied_foreign INTEGER NOT NULL CHECK (applied_foreign > 0),
    target_home_amount INTEGER NOT NULL,
    payment_home_amount INTEGER NOT NULL,
    fx_gain_loss_home INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bank_reconciliations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    statement_date TEXT NOT NULL,
    statement_ending_balance_home INTEGER NOT NULL,
    beginning_balance_home INTEGER NOT NULL,
    reconciled_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jel_account ON journal_entry_lines(account_id);
CREATE INDEX IF NOT EXISTS idx_jel_je ON journal_entry_lines(journal_entry_id);
CREATE INDEX IF NOT EXISTS idx_je_date ON journal_entries(entry_date);
CREATE INDEX IF NOT EXISTS idx_txn_type_status ON transactions(txn_type, status);
CREATE INDEX IF NOT EXISTS idx_txn_date ON transactions(txn_date);
CREATE INDEX IF NOT EXISTS idx_fifo_item_date ON inventory_fifo_layers(item_id, received_date, id);
CREATE INDEX IF NOT EXISTS idx_pay_app_target ON payment_applications(target_txn_id);
"""

DEFAULT_CURRENCIES = [
    ("USD", "US Dollar", "$", 1_000_000),
    ("EUR", "Euro", "€", 1_080_000),
    ("GBP", "British Pound", "£", 1_270_000),
    ("PHP", "Philippine Peso", "₱", 17_500),
    ("JPY", "Japanese Yen", "¥", 6_500),
    ("CAD", "Canadian Dollar", "C$", 730_000),
]

DEFAULT_COA = [
    # (account_number, name, account_type, detail_type)
    ("1000", "Checking", "Asset", "Checking"),
    ("1050", "Savings", "Asset", "Savings"),
    ("1100", "Undeposited Funds", "Asset", "Undeposited Funds"),
    ("1200", "Accounts Receivable", "Asset", "Accounts Receivable"),
    ("1300", "Inventory Asset", "Asset", "Inventory Asset"),
    ("1400", "Prepaid Expenses", "Asset", "Other Current Asset"),
    ("1500", "Office Equipment", "Asset", "Fixed Asset"),
    ("2000", "Accounts Payable", "Liability", "Accounts Payable"),
    ("2100", "Business Credit Card", "Liability", "Credit Card"),
    ("2200", "Sales Tax Payable", "Liability", "Sales Tax Payable"),
    ("3000", "Opening Balance Equity", "Equity", "Opening Balance Equity"),
    ("3100", "Owner's Equity", "Equity", "Owner's Equity"),
    ("3200", "Retained Earnings", "Equity", "Retained Earnings"),
    ("4000", "Sales Income", "Income", "Sales"),
    ("4100", "Service Income", "Income", "Service Income"),
    ("4900", "Other Income", "Income", "Other Income"),
    ("5000", "Cost of Goods Sold", "Expense", "Cost of Goods Sold"),
    ("6000", "Office Supplies", "Expense", "Operating Expense"),
    ("6100", "Rent Expense", "Expense", "Operating Expense"),
    ("6200", "Utilities", "Expense", "Operating Expense"),
    ("6300", "Bank Fees", "Expense", "Operating Expense"),
    ("6400", "Inventory Adjustment", "Expense", "Inventory Adjustment"),
    ("6900", "Exchange Gain or Loss", "Expense", "Exchange Gain/Loss"),
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    """Manages a local SQLite company file with WAL mode and backup support."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(self.db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._configure_pragmas()
        self.initialize_schema()

    def _configure_pragmas(self) -> None:
        self.conn.execute("PRAGMA foreign_keys = ON;")
        if self.db_path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL;")
            self.conn.execute("PRAGMA synchronous = NORMAL;")

    def initialize_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        now = utc_now_iso()
        cur = self.conn.cursor()

        # Company settings singleton
        row = cur.execute("SELECT id, home_currency_code FROM company_settings WHERE id = 1").fetchone()
        if row is None:
            cur.execute(
                """
                INSERT INTO company_settings (id, company_name, address, fiscal_year_start, home_currency_code, tax_rate_default_bps)
                VALUES (1, 'My Company', '123 Main Street, Suite 100', '01-01', 'USD', 0)
                """
            )
            home_currency = "USD"
        else:
            home_currency = row["home_currency_code"]

        # Default currencies
        for code, name, symbol, rate_micros in DEFAULT_CURRENCIES:
            effective_rate = RATE_SCALE if code == home_currency else rate_micros
            cur.execute(
                """
                INSERT OR IGNORE INTO currencies (code, name, symbol, exchange_rate_micros, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (code, name, symbol, effective_rate, now),
            )

        # Default Chart of Accounts (only if accounts table is empty)
        count = cur.execute("SELECT COUNT(*) AS c FROM accounts").fetchone()["c"]
        if count == 0:
            for acct_num, name, acct_type, detail_type in DEFAULT_COA:
                cur.execute(
                    """
                    INSERT INTO accounts (account_number, name, account_type, detail_type, currency_code, parent_account_id, is_active)
                    VALUES (?, ?, ?, ?, ?, NULL, 1)
                    """,
                    (acct_num, name, acct_type, detail_type, home_currency),
                )

        self.conn.commit()

    def backup_to(self, destination_path: str | Path) -> Path:
        """Create a consistent online SQLite backup copy of the company file."""
        dest = Path(destination_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dst_conn = sqlite3.connect(str(dest))
        try:
            with dst_conn:
                self.conn.backup(dst_conn)
        finally:
            dst_conn.close()
        return dest

    def reset_all_data(self) -> None:
        """Wipe all financial data and re-seed default currencies and COA."""
        cur = self.conn.cursor()
        tables = [
            "bank_reconciliations",
            "payment_applications",
            "fifo_consumptions",
            "journal_entry_lines",
            "journal_entries",
            "inventory_fifo_layers",
            "transaction_lines",
            "transactions",
            "items",
            "customers",
            "vendors",
            "accounts",
            "currencies",
            "company_settings",
        ]
        cur.execute("PRAGMA foreign_keys = OFF;")
        for t in tables:
            cur.execute(f"DELETE FROM {t};")
        cur.execute("DELETE FROM sqlite_sequence;")
        cur.execute("PRAGMA foreign_keys = ON;")
        self.conn.commit()
        self._seed_defaults()

    def close(self) -> None:
        self.conn.close()
