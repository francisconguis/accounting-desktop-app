"""Realistic Sample Company Seeder demonstrating Multi-Currency FX & FIFO Inventory."""

from __future__ import annotations

from datetime import date, timedelta

from accounting.engine import AccountingEngine
from accounting.models import BillLineInput, InvoiceLineInput, JournalLineInput
from accounting.money import RATE_SCALE


def load_sample_company(engine: AccountingEngine) -> dict[str, int]:
    """Reset the company file and populate realistic domestic & foreign transactions.

    Includes:
    - Owner's initial capital contribution into Checking
    - Domestic (USD) and foreign (EUR, GBP, PHP) customers & vendors
    - Multi-batch FIFO inventory purchases at different unit costs (both USD & EUR)
    - Domestic and foreign invoices that consume across multiple FIFO cost layers
    - Partial & full foreign invoice payments at shifted FX rates (booking Realized FX Gain/Loss)
    - Foreign bill payment at shifted FX rate (booking Realized FX Gain/Loss)
    - Operating expense checks & Undeposited Funds -> Checking deposit
    """
    engine.db.reset_all_data()
    engine.update_company_settings(
        company_name="Acme Global Trading Co.",
        address="500 Commerce Plaza, Suite 400, Austin, TX 78701",
        fiscal_year_start="01-01",
        home_currency_code="USD",
        tax_rate_default_bps=0,
    )

    # Set deterministic exchange rates
    engine.upsert_currency("EUR", "Euro", "€", 1_100_000)         # 1 EUR = 1.10 USD
    engine.upsert_currency("GBP", "British Pound", "£", 1_280_000)  # 1 GBP = 1.28 USD
    engine.upsert_currency("PHP", "Philippine Peso", "₱", 18_000)   # 1 PHP = 0.018 USD

    today = date.today()
    d = lambda days_ago: (today - timedelta(days=days_ago)).isoformat()

    checking = engine.get_account_by_detail_type("Checking")
    owners_eq = engine.get_account_by_detail_type("Owner's Equity")
    rent_acct = engine.get_account_by_number("6100")
    supplies_acct = engine.get_account_by_number("6000")

    # 1. Owner capital contribution: $50,000.00 into Checking
    engine.create_manual_journal_entry(
        entry_date=d(60),
        currency_code="USD",
        exchange_rate_micros=RATE_SCALE,
        memo="Initial owner capital contribution",
        reference_no="JE-0001",
        lines=[
            JournalLineInput(account_id=checking["id"], debit_foreign=5_000_000, debit_home=5_000_000),
            JournalLineInput(account_id=owners_eq["id"], credit_foreign=5_000_000, credit_home=5_000_000),
        ],
    )

    # 2. Customers (Domestic + Foreign)
    cust_us = engine.create_customer(
        name="Summit Retail Group",
        company_name="Summit Retail LLC",
        email="ap@summitretail.example",
        phone="555-0101",
        billing_address="100 Market St, Chicago, IL",
        currency_code="USD",
        terms="Net 30",
    )
    cust_eu = engine.create_customer(
        name="Nordic Tech GmbH",
        company_name="Nordic Tech GmbH",
        email="finance@nordictech.example",
        phone="+49-30-555019",
        billing_address="Friedrichstrasse 45, Berlin, Germany",
        currency_code="EUR",
        terms="Net 30",
    )
    cust_uk = engine.create_customer(
        name="Thames Electronics Ltd",
        company_name="Thames Electronics Ltd",
        email="accounts@thameselec.example",
        phone="+44-20-7946001",
        billing_address="22 Fleet St, London, UK",
        currency_code="GBP",
        terms="Net 15",
    )

    # 3. Vendors (Domestic + Foreign)
    vend_us = engine.create_vendor(
        name="Pacific Hardware Supply",
        company_name="Pacific Hardware Supply Inc.",
        email="orders@pacifichw.example",
        phone="555-0202",
        address="800 Industrial Way, Seattle, WA",
        currency_code="USD",
        terms="Net 30",
    )
    vend_eu = engine.create_vendor(
        name="EuroPrecision Components",
        company_name="EuroPrecision AG",
        email="billing@europrecision.example",
        phone="+49-89-555044",
        address="Industriestrasse 12, Munich, Germany",
        currency_code="EUR",
        terms="Net 30",
    )
    vend_landlord = engine.create_vendor(
        name="Metro Commercial Properties",
        company_name="Metro Commercial Properties LLC",
        email="rent@metroprop.example",
        phone="555-0303",
        address="500 Commerce Plaza, Austin, TX",
        currency_code="USD",
        terms="Due on Receipt",
    )

    # 4. Items (Inventory + Service)
    item_widget = engine.create_item(
        sku="WDG-100",
        name="Precision Smart Sensor",
        item_type="Inventory",
        description="Industrial IoT temperature & vibration sensor",
        sales_price=12_000,   # $120.00
        purchase_cost=5_000,  # $50.00 baseline
    )
    item_hub = engine.create_item(
        sku="HUB-200",
        name="Wireless Telemetry Hub",
        item_type="Inventory",
        description="Multi-channel telemetry gateway",
        sales_price=35_000,   # $350.00
        purchase_cost=18_000, # $180.00 baseline
    )
    item_consult = engine.create_item(
        sku="SRV-001",
        name="Onboarding & Calibration Service",
        item_type="Service",
        description="On-site or remote calibration consulting",
        sales_price=15_000,   # $150.00
        purchase_cost=0,
    )

    # 5. Multi-Batch FIFO Inventory Purchases at DIFFERENT unit costs
    # Batch 1: 100 Sensors @ $50.00 USD (50 days ago)
    bill_1 = engine.create_bill(
        vendor_id=vend_us,
        txn_date=d(50),
        due_date=d(20),
        currency_code="USD",
        exchange_rate_micros=RATE_SCALE,
        memo="FIFO Batch 1 - 100 Sensors @ $50.00",
        lines=[BillLineInput(item_id=item_widget, description="Precision Smart Sensor (Batch 1)", quantity=100, rate_foreign=5_000)],
    )
    # Batch 2: 50 Sensors @ $60.00 USD (35 days ago)
    bill_2 = engine.create_bill(
        vendor_id=vend_us,
        txn_date=d(35),
        due_date=d(5),
        currency_code="USD",
        exchange_rate_micros=RATE_SCALE,
        memo="FIFO Batch 2 - 50 Sensors @ $60.00",
        lines=[BillLineInput(item_id=item_widget, description="Precision Smart Sensor (Batch 2)", quantity=50, rate_foreign=6_000)],
    )
    # Batch 3 (Foreign EUR Vendor): 20 Hubs @ €150.00 at rate 1.10 (= $165.00 home each)
    bill_eu = engine.create_bill(
        vendor_id=vend_eu,
        txn_date=d(30),
        due_date=d(0),
        currency_code="EUR",
        exchange_rate_micros=1_100_000,
        memo="Foreign EUR inventory purchase - 20 Hubs @ €150.00",
        lines=[BillLineInput(item_id=item_hub, description="Wireless Telemetry Hub (EUR Batch)", quantity=20, rate_foreign=15_000)],
    )
    # Pay Bill 1 (domestic) in full
    engine.pay_bills(
        vendor_id=vend_us,
        txn_date=d(25),
        bank_account_id=checking["id"],
        currency_code="USD",
        exchange_rate_micros=RATE_SCALE,
        applications=[(bill_1, 500_000)],
        memo="Paid Pacific Hardware Batch 1",
    )
    # Pay EUR Bill at shifted rate 1.15 (1 EUR = $1.15 vs $1.10 -> Realized FX Loss of $150.00)
    engine.pay_bills(
        vendor_id=vend_eu,
        txn_date=d(10),
        bank_account_id=checking["id"],
        currency_code="EUR",
        exchange_rate_micros=1_150_000,
        applications=[(bill_eu, 300_000)],  # €3,000.00
        memo="Wire payment for EUR Bill (FX rate shifted 1.10 -> 1.15)",
    )

    # 6. Invoices (Domestic + Foreign) consuming across FIFO Batch 1 & Batch 2
    # Domestic Invoice: 120 Sensors @ $120.00 -> consumes ALL 100 @ $50 ($5,000) + 20 @ $60 ($1,200) = $6,200 COGS!
    inv_us = engine.create_invoice(
        customer_id=cust_us,
        txn_date=d(20),
        due_date=(today + timedelta(days=10)).isoformat(),
        currency_code="USD",
        exchange_rate_micros=RATE_SCALE,
        memo="Bulk sensor order + calibration",
        lines=[
            InvoiceLineInput(item_id=item_widget, description="Precision Smart Sensor", quantity=120, rate_foreign=12_000),
            InvoiceLineInput(item_id=item_consult, description="Onboarding & Calibration", quantity=4, rate_foreign=15_000),
        ],
    )
    # Foreign EUR Invoice: 10 Hubs @ €320.00 at rate 1.10 (= $3,520.00 home), consumes 10 @ $165 = $1,650 COGS
    inv_eu = engine.create_invoice(
        customer_id=cust_eu,
        txn_date=d(18),
        due_date=(today + timedelta(days=12)).isoformat(),
        currency_code="EUR",
        exchange_rate_micros=1_100_000,
        memo="EU Telemetry Hub shipment",
        lines=[
            InvoiceLineInput(item_id=item_hub, description="Wireless Telemetry Hub", quantity=10, rate_foreign=32_000),
        ],
    )
    # Foreign GBP Invoice (left open for A/R Aging): 10 Sensors @ £95.00 at rate 1.28
    inv_uk = engine.create_invoice(
        customer_id=cust_uk,
        txn_date=d(40),
        due_date=d(25),  # 25 days overdue -> appears in 1-30 A/R Aging bucket!
        currency_code="GBP",
        exchange_rate_micros=1_280_000,
        memo="UK Sensor pilot order",
        lines=[
            InvoiceLineInput(item_id=item_widget, description="Precision Smart Sensor", quantity=10, rate_foreign=9_500),
        ],
    )

    # 7. Customer Payments (with FX Gain on EUR payment!)
    # Receive full EUR payment at rate 1.14 (vs 1.10 -> Realized FX Gain of €3,200 * 0.04 = $128.00)
    engine.receive_payment(
        customer_id=cust_eu,
        txn_date=d(5),
        currency_code="EUR",
        exchange_rate_micros=1_140_000,
        applications=[(inv_eu, 320_000)],
        memo="SEPA wire received from Nordic Tech (FX 1.10 -> 1.14)",
    )
    # Partial USD payment on domestic invoice ($10,000 of $15,000)
    engine.receive_payment(
        customer_id=cust_us,
        txn_date=d(4),
        currency_code="USD",
        exchange_rate_micros=RATE_SCALE,
        applications=[(inv_us, 1_000_000)],
        memo="Partial check payment from Summit Retail",
    )

    # 8. Deposit Undeposited Funds into Checking
    engine.make_deposit(
        target_bank_account_id=checking["id"],
        txn_date=d(3),
        amount_home=1_000_000,
        memo="Deposit Summit Retail check into Checking",
    )

    # 9. Operating Expense Checks (Rent & Office Supplies)
    engine.write_check(
        txn_date=d(15),
        bank_account_id=checking["id"],
        vendor_id=vend_landlord,
        currency_code="USD",
        exchange_rate_micros=RATE_SCALE,
        memo="Monthly office rent",
        lines=[BillLineInput(account_id=rent_acct["id"], description="Office Rent", quantity=1, rate_foreign=250_000)],
    )
    engine.write_check(
        txn_date=d(7),
        bank_account_id=checking["id"],
        currency_code="USD",
        exchange_rate_micros=RATE_SCALE,
        memo="Printer toner & shipping labels",
        lines=[BillLineInput(account_id=supplies_acct["id"], description="Office Supplies", quantity=1, rate_foreign=18_500)],
    )

    return {
        "customers": 3,
        "vendors": 3,
        "items": 3,
        "bill_open_id": bill_2,
        "invoice_open_usd_id": inv_us,
        "invoice_overdue_gbp_id": inv_uk,
    }
