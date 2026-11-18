"""Stand-in for the existing payment total calculation service."""

from dataclasses import dataclass
from decimal import Decimal

from app.errors import MixedCurrencies
from app.models import Cart


@dataclass(frozen=True)
class CartTotal:
    amount: Decimal
    currency: str


def calculate_cart_total(cart: Cart) -> CartTotal:
    """Sum of quantity * unit_price. Expects a cart with at least one item."""
    currencies = {item.product.currency for item in cart.items}
    if len(currencies) != 1:
        raise MixedCurrencies()
    amount = sum((item.quantity * item.unit_price for item in cart.items), Decimal("0"))
    return CartTotal(amount=amount, currency=currencies.pop())
