"""
build_invoice — PURE function that turns inputs into an Invoice dataclass.

⚠️ NO database calls here. No `datetime.now()`. No PDF. Just math.

The order is FIXED:
    1. base       = strategy.calculate(usage)
    2. discount   = discount.apply(base) if discount else 0
    3. taxable    = base - discount
    4. tax        = tax_calc.apply(taxable)
    5. total      = taxable + tax.total
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from billing_engine.money import Money
from billing_engine.models import (
    Invoice, InvoiceStatus, InvoiceLineItem, LineItemKind, Subscription, Plan,
)
from billing_engine.pricing.base import PricingStrategy
from billing_engine.discounts.base import Discount, DiscountContext
from billing_engine.taxes.base import TaxCalculator, TaxContext


def build_invoice(
    subscription: Subscription,
    plan: Plan,
    strategy: PricingStrategy,
    discount: Optional[Discount],
    tax_calc: TaxCalculator,
    tax_context: TaxContext,
    usage_quantity: int,
    period_start: date,
    period_end: date,
    invoice_count_so_far: int,
) -> Invoice:
    """Pure function. Returns an Invoice (id=None, status=DRAFT) ready to be persisted."""
    # 1. Base charge from the pricing strategy
    base = strategy.calculate(usage_quantity)
    zero = Money.zero(base.currency)

    # 2. Discount (if any)
    if discount is not None:
        discount_amount = discount.apply(base, DiscountContext(invoice_count_so_far))
    else:
        discount_amount = zero

    # 3. Taxable amount
    taxable = base - discount_amount

    # 4. Tax on the taxable amount
    tax = tax_calc.apply(taxable, tax_context)

    # 5. Total
    total = taxable + tax.total

    # Line items (invoice_id is None until the invoice is saved)
    line_items = [
        InvoiceLineItem(
            id=None,
            invoice_id=None,
            description=f"{plan.name} charge",
            amount=base,
            kind=LineItemKind.BASE,
        )
    ]
    if discount_amount != zero:
        line_items.append(
            InvoiceLineItem(
                id=None,
                invoice_id=None,
                description="Discount",
                amount=zero - discount_amount,   # shown as a negative amount
                kind=LineItemKind.DISCOUNT,
            )
        )
    for label, amount in tax.components:
        line_items.append(
            InvoiceLineItem(
                id=None,
                invoice_id=None,
                description=label,
                amount=amount,
                kind=LineItemKind.TAX,
            )
        )

    return Invoice(
        id=None,
        subscription_id=subscription.id,
        period_start=period_start,
        period_end=period_end,
        subtotal=base,
        discount_total=discount_amount,
        tax_total=tax.total,
        total=total,
        status=InvoiceStatus.DRAFT,
        line_items=line_items,
    )