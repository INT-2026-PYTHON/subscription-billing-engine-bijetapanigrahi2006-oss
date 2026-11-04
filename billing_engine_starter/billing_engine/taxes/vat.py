"""
VATCalculator — single-rate VAT (e.g. 19% in Germany).
"""

from decimal import Decimal

from billing_engine.money import Money
from billing_engine.taxes.base import TaxCalculator, TaxContext, TaxBreakdown


class VATCalculator(TaxCalculator):
    def __init__(self, rate: Decimal) -> None:
        if isinstance(rate, float):
            raise TypeError("rate must be a Decimal, not a float")
        if not (Decimal("0") <= rate <= Decimal("1")):
            raise ValueError("rate must be between 0 and 1")
        self.rate = rate

    def apply(self, taxable: Money, context: TaxContext) -> TaxBreakdown:
        vat = taxable * self.rate
        label = f"VAT {self.rate * 100}%"
        return TaxBreakdown(components=[(label, vat)], total=vat)
