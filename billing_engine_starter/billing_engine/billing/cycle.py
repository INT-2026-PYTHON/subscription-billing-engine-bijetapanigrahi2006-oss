"""
BillingCycle — finds due subscriptions, generates invoices, posts ledger DEBITs,
advances the subscription period. Must be IDEMPOTENT (safe to run twice).
"""

from __future__ import annotations

import calendar
import sqlite3
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from typing import Callable, Optional

from billing_engine.db import (
    Database,
    CustomerRepository, PlanRepository, SubscriptionRepository,
    UsageRecordRepository, InvoiceRepository, InvoiceLineItemRepository,
    LedgerRepository,
)
from billing_engine.billing.pipeline import build_invoice
from billing_engine.models import (
    Subscription, SubscriptionStatus, BillingPeriod,
    InvoiceStatus, LedgerEntry, LedgerDirection,
)


@dataclass
class BillingResult:
    invoices_created: int
    invoices_skipped_duplicate: int
    trials_activated: int


def _add_months(d: date, months: int) -> date:
    """Same day next month (clamped to the month's last day)."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


class BillingCycle:
    """Day-3 deliverable. Day-4 stretch: add `upgrade_subscription(...)`."""

    def __init__(
        self,
        db: Database,
        customer_repo: CustomerRepository,
        plan_repo: PlanRepository,
        subscription_repo: SubscriptionRepository,
        usage_repo: UsageRecordRepository,
        invoice_repo: InvoiceRepository,
        line_item_repo: InvoiceLineItemRepository,
        ledger_repo: LedgerRepository,
        strategy_factory: Callable,    # given a Plan, returns a PricingStrategy
        discount_factory: Callable,    # given a discount_id or None, returns a Discount or None
        tax_factory: Callable,         # given a Customer, returns (TaxCalculator, TaxContext)
    ) -> None:
        self.db = db
        self.customer_repo = customer_repo
        self.plan_repo = plan_repo
        self.subscription_repo = subscription_repo
        self.usage_repo = usage_repo
        self.invoice_repo = invoice_repo
        self.line_item_repo = line_item_repo
        self.ledger_repo = ledger_repo
        self.strategy_factory = strategy_factory
        self.discount_factory = discount_factory
        self.tax_factory = tax_factory

    # --------------------------------------------------------
    def run(self, as_of: date) -> BillingResult:
        """Bill all subscriptions whose current period ends on or before `as_of`."""
        invoices_created = 0
        invoices_skipped = 0
        trials_activated = 0

        # 1. Trials whose trial has ended become ACTIVE
        for sub in self.subscription_repo.list_all():
            if (
                sub.status == SubscriptionStatus.TRIAL
                and sub.trial_end
                and sub.trial_end <= as_of
            ):
                self.subscription_repo.update_status(sub.id, SubscriptionStatus.ACTIVE)
                trials_activated += 1

        # 2. Bill everything that is due
        for sub in self.subscription_repo.get_due_for_billing(as_of):
            plan = self.plan_repo.get(sub.plan_id)
            customer = self.customer_repo.get(sub.customer_id)

            strategy = self.strategy_factory(plan)
            discount = self.discount_factory(sub.discount_id)
            tax_calc, tax_context = self.tax_factory(customer)

            usage = self.usage_repo.sum_for_period(
                sub.id, "usage", sub.current_period_start, sub.current_period_end
            )
            invoice_count = self.invoice_repo.count_for_subscription(sub.id)

            invoice = build_invoice(
                subscription=sub,
                plan=plan,
                strategy=strategy,
                discount=discount,
                tax_calc=tax_calc,
                tax_context=tax_context,
                usage_quantity=usage,
                period_start=sub.current_period_start,
                period_end=sub.current_period_end,
                invoice_count_so_far=invoice_count,
            )
            invoice = replace(
                invoice,
                status=InvoiceStatus.ISSUED,
                issued_at=datetime.combine(as_of, time()),
            )

            months = 12 if plan.billing_period == BillingPeriod.YEARLY else 1
            new_start = sub.current_period_end
            new_end = _add_months(new_start, months)

            # 3. All four writes succeed together, or none of them do
            try:
                with self.db.transaction() as conn:
                    saved = self.invoice_repo.add(invoice, conn=conn)
                    for item in invoice.line_items:
                        self.line_item_repo.add(
                            replace(item, invoice_id=saved.id), conn=conn
                        )
                    self.ledger_repo.add(
                        LedgerEntry(
                            None, saved.id, customer.id, saved.total,
                            LedgerDirection.DEBIT, "Invoice",
                        ),
                        conn=conn,
                    )
                    self.subscription_repo.update_period(
                        sub.id, new_start, new_end, conn=conn
                    )
                invoices_created += 1
            except sqlite3.IntegrityError:
                # Invoice for this (subscription, period) already exists -> skip
                invoices_skipped += 1

        return BillingResult(invoices_created, invoices_skipped, trials_activated)

    # --------------------------------------------------------
    def upgrade_subscription(self, subscription_id: int, new_plan_id: int, switch_date: date) -> None:
        """Mid-cycle upgrade — Day 4 stretch."""
        # TODO Day 4
        raise NotImplementedError("Day 4: implement BillingCycle.upgrade_subscription")