"""Domain constants and lightweight dataclasses for the accounting engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

ACCOUNT_TYPES = ("Asset", "Liability", "Equity", "Income", "Expense")

DEBIT_NORMAL_TYPES = frozenset({"Asset", "Expense"})
CREDIT_NORMAL_TYPES = frozenset({"Liability", "Equity", "Income"})

DETAIL_TYPES_BY_ACCOUNT_TYPE: dict[str, list[str]] = {
    "Asset": [
        "Checking",
        "Savings",
        "Undeposited Funds",
        "Accounts Receivable",
        "Inventory Asset",
        "Other Current Asset",
        "Fixed Asset",
    ],
    "Liability": [
        "Accounts Payable",
        "Credit Card",
        "Sales Tax Payable",
        "Other Current Liability",
        "Long Term Liability",
    ],
    "Equity": [
        "Owner's Equity",
        "Retained Earnings",
        "Opening Balance Equity",
    ],
    "Income": [
        "Sales",
        "Service Income",
        "Other Income",
    ],
    "Expense": [
        "Cost of Goods Sold",
        "Operating Expense",
        "Exchange Gain/Loss",
        "Inventory Adjustment",
        "Other Expense",
    ],
}

ITEM_TYPES = ("Service", "Non-Inventory", "Inventory")

TXN_TYPES = (
    "Invoice",
    "Payment",
    "SalesReceipt",
    "Bill",
    "BillPayment",
    "Check",
    "Expense",
    "Deposit",
    "JournalEntry",
    "InventoryAdjust",
)

TXN_STATUSES = ("Draft", "Open", "Partial", "Paid", "Overdue", "Void")


def normal_balance_sign(account_type: str) -> int:
    """Return +1 for debit-normal accounts (Asset, Expense), -1 for credit-normal."""
    if account_type in DEBIT_NORMAL_TYPES:
        return 1
    if account_type in CREDIT_NORMAL_TYPES:
        return -1
    raise ValueError(f"Unknown account_type: {account_type}")


def compute_normal_balance(account_type: str, total_debit: int, total_credit: int) -> int:
    """Compute signed balance according to the account's normal balance side."""
    if account_type in DEBIT_NORMAL_TYPES:
        return total_debit - total_credit
    return total_credit - total_debit


@dataclass(frozen=True)
class JournalLineInput:
    """Input line for posting a double-entry journal entry."""

    account_id: int
    debit_home: int = 0
    credit_home: int = 0
    debit_foreign: int = 0
    credit_foreign: int = 0
    entity_type: str = "None"  # Customer, Vendor, None
    entity_id: Optional[int] = None


@dataclass(frozen=True)
class InvoiceLineInput:
    """Input line for an Invoice or Sales Receipt."""

    item_id: Optional[int]
    description: str
    quantity: int
    rate_foreign: int  # integer cents in transaction currency
    account_id: Optional[int] = None  # override income account if item_id is None


@dataclass(frozen=True)
class BillLineInput:
    """Input line for a Bill or Check/Expense."""

    description: str
    quantity: int
    rate_foreign: int  # integer cents in transaction currency
    account_id: Optional[int] = None  # expense/asset account for non-item lines
    item_id: Optional[int] = None  # inventory/non-inventory item
