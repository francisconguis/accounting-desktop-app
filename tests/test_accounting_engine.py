"""Automated unit tests verifying double-entry accounting, integer-cent precision,
multi-currency realized FX gain/loss, FIFO inventory consumption, bank reconciliation,
and financial statement equality.
"""

from __future__ import annotations

from datetime import date
import pytest

from accounting.db import Database
from accounting.engine import AccountingEngine, AccountingError
from accounting.models import BillLineInput, InvoiceLineInput, JournalLineInput
from accounting.money import (
    RATE_SCALE,
    convert_to_home,
    format_money,
    parse_money_to_cents,
    parse_rate_to_micros,
)
from accounting.reports import FinancialReports
from accounting.sample_data import load_sample_company


@pytest.fixture
def engine() -> AccountingEngine:
    db = Database(":memory:")
    eng = AccountingEngine(db)
    yield eng
    db.close()


def _assert_all_jes_balanced(engine: AccountingEngine) -> None:
    rows = engine.conn.execute(
        """
        SELECT je.id, SUM(jel.debit_home) AS d, SUM(jel.credit_home) AS c
        FROM journal_entries je
        JOIN journal_entry_lines jel ON jel.journal_entry_id = je.id
        WHERE je.voided = 0
        GROUP BY je.id
        """
    ).fetchall()
    assert len(rows) > 0
    for r in rows:
        assert r["d"] == r["c"], f"JE {r['id']} unbalanced: D={r['d']} C={r['c']}"
        assert r["d"] > 0


def test_integer_cent_precision_and_no_float():
    assert parse_money_to_cents("19.99") + parse_money_to_cents("0.01") == 2000
    assert format_money(2000, "USD") == "$20.00"
    assert parse_rate_to_micros("1.250000") == 1_250_000
    assert convert_to_home(10_000, 1_250_000) == 12_500

    with pytest.raises(TypeError):
        parse_money_to_cents(19.99)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        parse_rate_to_micros(1.25)  # type: ignore[arg-type]


def test_unbalanced_journal_entry_rejected_and_rolled_back(engine: AccountingEngine):
    checking = engine.get_account_by_detail_type("Checking")
    equity = engine.get_account_by_detail_type("Owner's Equity")

    with pytest.raises(AccountingError, match="Unbalanced"):
        engine.create_manual_journal_entry(
            entry_date="2026-01-10",
            lines=[
                JournalLineInput(account_id=checking["id"], debit_home=10_000, debit_foreign=10_000),
                JournalLineInput(account_id=equity["id"], credit_home=9_999, credit_foreign=9_999),
            ],
        )

    # Verify rollback: zero journal entries committed
    count = engine.conn.execute("SELECT COUNT(*) AS c FROM journal_entries").fetchone()["c"]
    assert count == 0


def test_fifo_inventory_purchase_and_sale_cogs(engine: AccountingEngine):
    vend = engine.create_vendor("Supply Co", currency_code="USD")
    cust = engine.create_customer("Buyer Co", currency_code="USD")
    item = engine.create_item(sku="SKU-1", name="Widget", item_type="Inventory", sales_price=10_000, purchase_cost=4_000)

    # Batch 1: 10 units @ $40.00 = $400.00
    engine.create_bill(
        vendor_id=vend,
        txn_date="2026-01-01",
        due_date="2026-01-31",
        lines=[BillLineInput(item_id=item, description="Batch 1", quantity=10, rate_foreign=4_000)],
    )
    # Batch 2: 10 units @ $60.00 = $600.00
    engine.create_bill(
        vendor_id=vend,
        txn_date="2026-01-05",
        due_date="2026-02-04",
        lines=[BillLineInput(item_id=item, description="Batch 2", quantity=10, rate_foreign=6_000)],
    )

    inv_acct = engine.get_account_by_detail_type("Inventory Asset")
    cogs_acct = engine.get_account_by_detail_type("Cost of Goods Sold")
    assert engine.get_account_balance(inv_acct["id"])[1] == 100_000  # $1,000.00

    # Sell 15 units @ $100.00 -> FIFO COGS = 10*$40 + 5*$60 = $700.00; Remaining Inventory = 5*$60 = $300.00
    engine.create_invoice(
        customer_id=cust,
        txn_date="2026-01-10",
        due_date="2026-02-09",
        lines=[InvoiceLineInput(item_id=item, description="Widget Sale", quantity=15, rate_foreign=10_000)],
    )

    assert engine.get_account_balance(cogs_acct["id"])[1] == 70_000
    assert engine.get_account_balance(inv_acct["id"])[1] == 30_000

    # Cannot sell more than remaining 5 units
    with pytest.raises(AccountingError, match="Insufficient inventory"):
        engine.create_invoice(
            customer_id=cust,
            txn_date="2026-01-11",
            due_date="2026-02-10",
            lines=[InvoiceLineInput(item_id=item, description="Oversell", quantity=6, rate_foreign=10_000)],
        )

    reports = FinancialReports(engine)
    inv_report = reports.inventory_valuation_summary()
    assert inv_report["is_reconciled"] is True
    assert inv_report["total_fifo_value_home"] == 30_000
    _assert_all_jes_balanced(engine)


def test_multicurrency_invoice_payment_realized_fx_gain_and_loss(engine: AccountingEngine):
    cust = engine.create_customer("Berlin Client", currency_code="EUR")
    srv = engine.create_item(sku="SRV-1", name="Consulting", item_type="Service", sales_price=100_000)

    # Invoice €1,000.00 @ 1.10 -> $1,100.00 USD AR
    inv_id = engine.create_invoice(
        customer_id=cust,
        txn_date="2026-02-01",
        due_date="2026-03-03",
        currency_code="EUR",
        exchange_rate_micros=1_100_000,
        lines=[InvoiceLineInput(item_id=srv, description="Consulting", quantity=1, rate_foreign=100_000)],
    )

    # Receive full payment @ 1.15 -> $1,150.00 USD cash; AR clears $1,100.00; FX Gain = $50.00 (Credit FX)
    engine.receive_payment(
        customer_id=cust,
        txn_date="2026-02-15",
        currency_code="EUR",
        exchange_rate_micros=1_150_000,
        applications=[(inv_id, 100_000)],
    )

    ar_acct = engine.get_account_by_detail_type("Accounts Receivable")
    uf_acct = engine.get_account_by_detail_type("Undeposited Funds")
    fx_acct = engine.get_account_by_detail_type("Exchange Gain/Loss")

    # AR clears cleanly to $0.00 in BOTH foreign and home currency
    assert engine.get_account_balance(ar_acct["id"]) == (0, 0)
    assert engine.get_account_balance(uf_acct["id"])[1] == 115_000
    # FX account is Expense-normal -> Credit of 5,000 = -5,000 (a gain)
    assert engine.get_account_balance(fx_acct["id"])[1] == -5_000
    _assert_all_jes_balanced(engine)


def test_multicurrency_bill_payment_realized_fx_loss(engine: AccountingEngine):
    vend = engine.create_vendor("Munich Supplier", currency_code="EUR")
    exp_acct = engine.get_account_by_number("6000")
    checking = engine.get_account_by_detail_type("Checking")
    equity = engine.get_account_by_detail_type("Owner's Equity")

    # Fund checking with $5,000
    engine.create_manual_journal_entry(
        entry_date="2026-01-01",
        lines=[
            JournalLineInput(account_id=checking["id"], debit_home=500_000, debit_foreign=500_000),
            JournalLineInput(account_id=equity["id"], credit_home=500_000, credit_foreign=500_000),
        ],
    )

    # Bill €1,000.00 @ 1.10 -> $1,100.00 AP
    bill_id = engine.create_bill(
        vendor_id=vend,
        txn_date="2026-02-01",
        due_date="2026-03-01",
        currency_code="EUR",
        exchange_rate_micros=1_100_000,
        lines=[BillLineInput(account_id=exp_acct["id"], description="Parts", quantity=1, rate_foreign=100_000)],
    )

    # Pay bill @ 1.20 -> $1,200.00 cash; AP clears $1,100.00; FX Loss = $100.00 (Debit FX)
    engine.pay_bills(
        vendor_id=vend,
        txn_date="2026-02-20",
        bank_account_id=checking["id"],
        currency_code="EUR",
        exchange_rate_micros=1_200_000,
        applications=[(bill_id, 100_000)],
    )

    ap_acct = engine.get_account_by_detail_type("Accounts Payable")
    fx_acct = engine.get_account_by_detail_type("Exchange Gain/Loss")
    assert engine.get_account_balance(ap_acct["id"]) == (0, 0)
    assert engine.get_account_balance(fx_acct["id"])[1] == 10_000
    _assert_all_jes_balanced(engine)


def test_sample_company_balance_sheet_and_inventory_reconciliation(engine: AccountingEngine):
    load_sample_company(engine)
    today_str = date.today().isoformat()
    reports = FinancialReports(engine)

    bs = reports.balance_sheet(today_str)
    assert bs["is_balanced"] is True
    assert bs["total_assets_home"] == bs["total_liabilities_and_equity_home"]
    assert bs["total_assets_home"] > 0

    tb = reports.trial_balance(today_str)
    assert tb["is_balanced"] is True
    assert tb["total_debit_home"] == tb["total_credit_home"]

    inv = reports.inventory_valuation_summary()
    assert inv["is_reconciled"] is True
    assert inv["total_fifo_value_home"] == inv["gl_inventory_balance_home"]
    assert inv["total_fifo_value_home"] > 0

    _assert_all_jes_balanced(engine)


def test_bank_reconciliation(engine: AccountingEngine):
    checking = engine.get_account_by_detail_type("Checking")
    equity = engine.get_account_by_detail_type("Owner's Equity")
    engine.create_manual_journal_entry(
        entry_date="2026-03-01",
        lines=[
            JournalLineInput(account_id=checking["id"], debit_home=250_000, debit_foreign=250_000),
            JournalLineInput(account_id=equity["id"], credit_home=250_000, credit_foreign=250_000),
        ],
    )
    state = engine.get_reconciliation_state(checking["id"])
    assert state["beginning_balance_home"] == 0
    assert len(state["uncleared_lines"]) == 1
    jel_id = state["uncleared_lines"][0]["jel_id"]

    # Wrong ending balance rejected
    with pytest.raises(AccountingError, match="Difference must be"):
        engine.reconcile_bank_account(checking["id"], "2026-03-31", 200_000, [jel_id])

    # Exact ending balance succeeds
    engine.reconcile_bank_account(checking["id"], "2026-03-31", 250_000, [jel_id])
    state_after = engine.get_reconciliation_state(checking["id"])
    assert state_after["beginning_balance_home"] == 250_000
    assert len(state_after["uncleared_lines"]) == 0
