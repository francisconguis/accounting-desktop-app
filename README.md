# Local-First Double-Entry Accounting Desktop App (QuickBooks Desktop Clone)

A native Windows desktop accounting application built with **Python 3.12+**, **PySide6**, and **SQLite (WAL mode)** — zero cloud dependencies, strict double-entry home-currency enforcement (`SUM(debit_home) == SUM(credit_home)`), integer-cent precision (zero floating-point arithmetic), multi-currency realized FX gain/loss posting, and FIFO inventory valuation.

## Quick Start

### Easiest Way (Double-Click `.bat` Launchers)
1. Double-click **`run_app.bat`** — automatically creates a local `.venv`, installs `PySide6`, `reportlab`, `pandas`, `openpyxl`, and `pytest` on the first run, and launches the app.
2. Double-click **`run_tests.bat`** — runs the full automated unit test suite (`pytest -v`) in `.venv`.

### Manual PowerShell Commands
```powershell
cd "C:\Users\thunderobot i7\Desktop\AI Projects\Accounting App"
python -m pip install -r requirements.txt
python -m pytest -v
python main.py
```

The test suite verifies:
- Integer-cent precision & rejection of `float` values
- Strict zero-sum rejection & SQLite rollback on unbalanced journal entries
- Multi-batch FIFO inventory purchases, chronological layer consumption, exact COGS posting, and oversell prevention
- Multi-currency foreign Invoice payment & Bill payment realized FX Gain/Loss with AR/AP clearing to `$0.00` in both currencies
- Bank Reconciliation enforcement (`Difference == $0.00`)
- Sample Company Balance Sheet (`Assets == Liabilities + Equity`), Trial Balance (`Debits == Credits`), and Inventory Valuation (`FIFO Layers == Inventory Asset GL`)

### 3. Launch the Desktop App
```powershell
python main.py
```

### 4. Load Sample Company & Verify
1. Click **"Load Sample Company"** on the Home Dashboard (or **File -> Load Sample Company**).
2. Open **Reports -> Balance Sheet** and verify the green **`✔ Assets == Liabilities + Equity`** badge.
3. Open **Reports -> Inventory Valuation Summary (FIFO)** and verify the green **`✔ FIFO Layers == Inventory Asset GL`** badge.
4. Double-click any account in **Chart of Accounts** to inspect its checkbook-style **Account Register**.
5. Use **Export CSV**, **Export Excel**, or **Print / Save PDF** on any financial report.
