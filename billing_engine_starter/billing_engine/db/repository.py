"""
Repositories — the ONLY place SQL lives.

Each repository wraps the Database connection and exposes methods that
take/return domain dataclasses (defined in billing_engine/models/).

⚠️ YOU IMPLEMENT every method body marked TODO.
   The signatures, docstrings, and the LedgerRepository's append-only
   guarantee are already in place — do not change them.

Conventions:
  - Always use parameterized queries (`?` placeholders) — NEVER f-string SQL.
  - Money values are persisted as TEXT using `money.to_storage()`.
  - Dates are persisted as ISO strings (`date.isoformat()`).
"""

from __future__ import annotations

from datetime import date, datetime
from dataclasses import replace
from contextlib import contextmanager
from decimal import Decimal
from typing import Optional

from billing_engine.db.database import Database
from billing_engine.money import Money
from billing_engine.models import (
    Customer,
    Plan, PricingType, BillingPeriod,
    Subscription, SubscriptionStatus,
    Invoice, InvoiceStatus, InvoiceLineItem, LineItemKind,
    LedgerEntry, LedgerDirection,
)


# ============================================================
# CUSTOMERS
# ============================================================
@contextmanager
def _use(db: Database, conn=None):
    """Use the given connection (the caller owns the transaction),
    or open a fresh transaction of our own."""
    if conn is not None:
        yield conn
    else:
        with db.transaction() as new_conn:
            yield new_conn

def _row_to_customer(row) -> Customer:
    return Customer(
        id=row["id"],
        name=row["name"],
        email=row["email"],
        country_code=row["country_code"],
        state_code=row["state_code"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
class CustomerRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, customer: Customer) -> Customer:
        """Insert and return the customer with `id` populated."""
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO customers (name, email, country_code, state_code) "
                "VALUES (?, ?, ?, ?)",
                (customer.name, customer.email, customer.country_code, customer.state_code),
            )
            new_id = cursor.lastrowid
        return self.get(new_id)

    def get(self, customer_id: int) -> Optional[Customer]:
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT * FROM customers WHERE id = ?", (customer_id,)
            ).fetchone()
        finally:
            conn.close()
        return _row_to_customer(row) if row else None

    def find_by_email(self, email: str) -> Optional[Customer]:
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT * FROM customers WHERE email = ?", (email,)
            ).fetchone()
        finally:
            conn.close()
        return _row_to_customer(row) if row else None

    def list_all(self) -> list[Customer]:
        conn = self.db.connect()
        try:
            rows = conn.execute("SELECT * FROM customers ORDER BY id").fetchall()
        finally:
            conn.close()
        return [_row_to_customer(r) for r in rows]
# ============================================================
# PLANS  +  PLAN TIERS
# ============================================================
def _row_to_plan(row) -> Plan:
    return Plan(
        id=row["id"],
        name=row["name"],
        pricing_type=PricingType(row["pricing_type"]),
        billing_period=BillingPeriod(row["billing_period"]),
        currency=row["currency"],
        config_json=row["config_json"],
    )


class PlanRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, plan: Plan) -> Plan:
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO plans (name, pricing_type, billing_period, currency, config_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    plan.name,
                    plan.pricing_type.value,
                    plan.billing_period.value,
                    plan.currency,
                    plan.config_json,
                ),
            )
            new_id = cursor.lastrowid
        return self.get(new_id)

    def get(self, plan_id: int) -> Optional[Plan]:
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT * FROM plans WHERE id = ?", (plan_id,)
            ).fetchone()
        finally:
            conn.close()
        return _row_to_plan(row) if row else None

    def list_all(self) -> list[Plan]:
        conn = self.db.connect()
        try:
            rows = conn.execute("SELECT * FROM plans ORDER BY id").fetchall()
        finally:
            conn.close()
        return [_row_to_plan(r) for r in rows]


class PlanTierRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, plan_id: int, from_units: int, to_units: Optional[int], unit_price: Money) -> int:
        """Insert a tier; return new id."""
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO plan_tiers (plan_id, from_units, to_units, unit_price) "
                "VALUES (?, ?, ?, ?)",
                (plan_id, from_units, to_units, unit_price.to_storage()),
            )
            return cursor.lastrowid

    def list_for_plan(self, plan_id: int, currency: str) -> list[tuple[int, Optional[int], Money]]:
        """Return [(from_units, to_units, unit_price)] ordered by from_units."""
        conn = self.db.connect()
        try:
            rows = conn.execute(
                "SELECT from_units, to_units, unit_price FROM plan_tiers "
                "WHERE plan_id = ? ORDER BY from_units",
                (plan_id,),
            ).fetchall()
        finally:
            conn.close()
        return [
            (r["from_units"], r["to_units"], Money(r["unit_price"], currency))
            for r in rows
        ]


class DiscountRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, code: str, discount_type: str, value: str, currency: Optional[str] = None) -> int:
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO discounts (code, discount_type, value, currency) "
                "VALUES (?, ?, ?, ?)",
                (code, discount_type, value, currency),
            )
            return cursor.lastrowid

    def get_by_code(self, code: str) -> Optional[dict]:
        """Return raw row as dict, or None."""
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT * FROM discounts WHERE code = ?", (code,)
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None


# ============================================================
# SUBSCRIPTIONS
# ============================================================
def _row_to_subscription(row) -> Subscription:
    return Subscription(
        id=row["id"],
        customer_id=row["customer_id"],
        plan_id=row["plan_id"],
        status=SubscriptionStatus(row["status"]),
        current_period_start=date.fromisoformat(row["current_period_start"]),
        current_period_end=date.fromisoformat(row["current_period_end"]),
        trial_end=date.fromisoformat(row["trial_end"]) if row["trial_end"] else None,
        discount_id=row["discount_id"],
        past_due_since=(
            date.fromisoformat(row["past_due_since"]) if row["past_due_since"] else None
        ),
    )


class SubscriptionRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, subscription: Subscription) -> Subscription:
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO subscriptions (customer_id, plan_id, status, "
                "current_period_start, current_period_end, trial_end, "
                "discount_id, past_due_since) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    subscription.customer_id,
                    subscription.plan_id,
                    subscription.status.value,
                    subscription.current_period_start.isoformat(),
                    subscription.current_period_end.isoformat(),
                    subscription.trial_end.isoformat() if subscription.trial_end else None,
                    subscription.discount_id,
                    subscription.past_due_since.isoformat()
                    if subscription.past_due_since
                    else None,
                ),
            )
            new_id = cursor.lastrowid
        return self.get(new_id)

    def get(self, subscription_id: int) -> Optional[Subscription]:
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)
            ).fetchone()
        finally:
            conn.close()
        return _row_to_subscription(row) if row else None

    def list_all(self) -> list[Subscription]:
        """All subscriptions, regardless of status."""
        conn = self.db.connect()
        try:
            rows = conn.execute("SELECT * FROM subscriptions ORDER BY id").fetchall()
        finally:
            conn.close()
        return [_row_to_subscription(r) for r in rows]

    def get_due_for_billing(self, as_of: date) -> list[Subscription]:
        """ACTIVE subscriptions whose current_period_end <= as_of."""
        conn = self.db.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM subscriptions "
                "WHERE status = 'ACTIVE' AND current_period_end <= ? ORDER BY id",
                (as_of.isoformat(),),
            ).fetchall()
        finally:
            conn.close()
        return [_row_to_subscription(r) for r in rows]

    def update_period(self, subscription_id: int, new_start: date, new_end: date, conn=None) -> None:
        with _use(self.db, conn) as c:
            c.execute(
                "UPDATE subscriptions SET current_period_start = ?, "
                "current_period_end = ? WHERE id = ?",
                (new_start.isoformat(), new_end.isoformat(), subscription_id),
            )

    def update_status(
        self,
        subscription_id: int,
        new_status: SubscriptionStatus,
        past_due_since: Optional[date] = None,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE subscriptions SET status = ?, past_due_since = ? WHERE id = ?",
                (
                    new_status.value,
                    past_due_since.isoformat() if past_due_since else None,
                    subscription_id,
                ),
            )

    def update_plan(self, subscription_id: int, new_plan_id: int) -> None:
        """Switch the subscription to a different plan (used by upgrade flow)."""
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE subscriptions SET plan_id = ? WHERE id = ?",
                (new_plan_id, subscription_id),
            )


# ============================================================
# USAGE
# ============================================================
class UsageRecordRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, subscription_id: int, metric: str, quantity: int) -> int:
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO usage_records (subscription_id, metric, quantity) "
                "VALUES (?, ?, ?)",
                (subscription_id, metric, quantity),
            )
            return cursor.lastrowid

    def sum_for_period(
        self, subscription_id: int, metric: str, period_start: date, period_end: date
    ) -> int:
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(quantity), 0) AS total FROM usage_records "
                "WHERE subscription_id = ? AND metric = ? "
                "AND date(recorded_at) >= ? AND date(recorded_at) < ?",
                (
                    subscription_id,
                    metric,
                    period_start.isoformat(),
                    period_end.isoformat(),
                ),
            ).fetchone()
        finally:
            conn.close()
        return int(row["total"])


# ============================================================
# INVOICES + LINE ITEMS
# ============================================================
def _row_to_invoice(row) -> Invoice:
    currency = row["currency"]
    return Invoice(
        id=row["id"],
        subscription_id=row["subscription_id"],
        period_start=date.fromisoformat(row["period_start"]),
        period_end=date.fromisoformat(row["period_end"]),
        subtotal=Money(row["subtotal"], currency),
        discount_total=Money(row["discount_total"], currency),
        tax_total=Money(row["tax_total"], currency),
        total=Money(row["total"], currency),
        status=InvoiceStatus(row["status"]),
        issued_at=datetime.fromisoformat(row["issued_at"]) if row["issued_at"] else None,
        pdf_path=row["pdf_path"],
    )


class InvoiceRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, invoice: Invoice, conn=None) -> Invoice:
        """Insert invoice (NOT line items). A duplicate (subscription_id,
        period_start) raises sqlite3.IntegrityError naturally."""
        with _use(self.db, conn) as c:
            cursor = c.execute(
                "INSERT INTO invoices (subscription_id, period_start, period_end, "
                "currency, subtotal, discount_total, tax_total, total, status, "
                "issued_at, pdf_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    invoice.subscription_id,
                    invoice.period_start.isoformat(),
                    invoice.period_end.isoformat(),
                    invoice.total.currency,
                    invoice.subtotal.to_storage(),
                    invoice.discount_total.to_storage(),
                    invoice.tax_total.to_storage(),
                    invoice.total.to_storage(),
                    invoice.status.value,
                    invoice.issued_at.isoformat() if invoice.issued_at else None,
                    invoice.pdf_path,
                ),
            )
            new_id = cursor.lastrowid
        return replace(invoice, id=new_id)

    def get(self, invoice_id: int) -> Optional[Invoice]:
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT * FROM invoices WHERE id = ?", (invoice_id,)
            ).fetchone()
        finally:
            conn.close()
        return _row_to_invoice(row) if row else None

    def count_for_subscription(self, subscription_id: int) -> int:
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM invoices WHERE subscription_id = ?",
                (subscription_id,),
            ).fetchone()
        finally:
            conn.close()
        return int(row["n"])

    def mark_paid(self, invoice_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE invoices SET status = 'PAID' WHERE id = ?", (invoice_id,)
            )

    def mark_failed(self, invoice_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE invoices SET status = 'FAILED' WHERE id = ?", (invoice_id,)
            )

    def set_pdf_path(self, invoice_id: int, path: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE invoices SET pdf_path = ? WHERE id = ?", (path, invoice_id)
            )


class InvoiceLineItemRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, line_item: InvoiceLineItem, conn=None) -> InvoiceLineItem:
        with _use(self.db, conn) as c:
            cursor = c.execute(
                "INSERT INTO invoice_line_items (invoice_id, description, amount, kind) "
                "VALUES (?, ?, ?, ?)",
                (
                    line_item.invoice_id,
                    line_item.description,
                    line_item.amount.to_storage(),
                    line_item.kind.value,
                ),
            )
            new_id = cursor.lastrowid
        return replace(line_item, id=new_id)

    def list_for_invoice(self, invoice_id: int) -> list[InvoiceLineItem]:
        conn = self.db.connect()
        try:
            rows = conn.execute(
                "SELECT li.id, li.invoice_id, li.description, li.amount, li.kind, "
                "i.currency FROM invoice_line_items li "
                "JOIN invoices i ON i.id = li.invoice_id "
                "WHERE li.invoice_id = ? ORDER BY li.id",
                (invoice_id,),
            ).fetchall()
        finally:
            conn.close()
        return [
            InvoiceLineItem(
                id=r["id"],
                invoice_id=r["invoice_id"],
                description=r["description"],
                amount=Money(r["amount"], r["currency"]),
                kind=LineItemKind(r["kind"]),
            )
            for r in rows
        ]


# ============================================================
# LEDGER — APPEND-ONLY (do not implement update/delete)
# ============================================================
def _row_to_ledger(row) -> LedgerEntry:
    return LedgerEntry(
        id=row["id"],
        invoice_id=row["invoice_id"],
        customer_id=row["customer_id"],
        amount=Money(row["amount"], row["currency"]),
        direction=LedgerDirection(row["direction"]),
        reason=row["reason"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class LedgerRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, entry: LedgerEntry, conn=None) -> LedgerEntry:
        with _use(self.db, conn) as c:
            cursor = c.execute(
                "INSERT INTO ledger_entries (invoice_id, customer_id, amount, "
                "currency, direction, reason) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    entry.invoice_id,
                    entry.customer_id,
                    entry.amount.to_storage(),
                    entry.amount.currency,
                    entry.direction.value,
                    entry.reason,
                ),
            )
            new_id = cursor.lastrowid
            row = c.execute(
                "SELECT * FROM ledger_entries WHERE id = ?", (new_id,)
            ).fetchone()
        return _row_to_ledger(row)

    def list_for_customer(self, customer_id: int) -> list[LedgerEntry]:
        conn = self.db.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM ledger_entries WHERE customer_id = ? ORDER BY id",
                (customer_id,),
            ).fetchall()
        finally:
            conn.close()
        return [_row_to_ledger(r) for r in rows]

    # ✅ These two methods are intentionally implemented to REJECT — do not override.
    def update(self, *args, **kwargs):
        raise NotImplementedError("Ledger is append-only. Post a reversing entry instead.")

    def delete(self, *args, **kwargs):
        raise NotImplementedError("Ledger is append-only. Post a reversing entry instead.")


# ============================================================
# PAYMENT ATTEMPTS
# ============================================================

class PaymentAttemptRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(
        self,
        invoice_id: int,
        attempt_no: int,
        status: str,
        failure_reason: Optional[str],
        next_retry_at: Optional[datetime],
        conn=None,
    ) -> int:
        with _use(self.db, conn) as c:
            cursor = c.execute(
                "INSERT INTO payment_attempts (invoice_id, attempt_no, status, "
                "failure_reason, next_retry_at) VALUES (?, ?, ?, ?, ?)",
                (
                    invoice_id,
                    attempt_no,
                    status,
                    failure_reason,
                    next_retry_at.isoformat() if next_retry_at else None,
                ),
            )
            return cursor.lastrowid

    def list_for_invoice(self, invoice_id: int) -> list[dict]:
        conn = self.db.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM payment_attempts WHERE invoice_id = ? "
                "ORDER BY attempt_no",
                (invoice_id,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def count_for_invoice(self, invoice_id: int) -> int:
        conn = self.db.connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM payment_attempts WHERE invoice_id = ?",
                (invoice_id,),
            ).fetchone()
        finally:
            conn.close()
        return int(row["n"])