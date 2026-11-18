from decimal import Decimal

import pytest

from app.cart_total import CartTotal, calculate_cart_total
from app.errors import MixedCurrencies
from app.models import Cart, CartItem, Product


def item(quantity: int, unit_price: str, currency: str = "USD") -> CartItem:
    return CartItem(
        quantity=quantity, unit_price=Decimal(unit_price), product=Product(currency=currency)
    )


def test_total_is_the_sum_of_quantity_times_unit_price():
    cart = Cart(items=[item(1, "45.00"), item(2, "12.50")])

    assert calculate_cart_total(cart) == CartTotal(amount=Decimal("70.00"), currency="USD")


def test_total_keeps_exact_decimal_cents():
    cart = Cart(items=[item(3, "0.10")])

    assert calculate_cart_total(cart).amount == Decimal("0.30")


def test_mixed_currencies_are_rejected():
    cart = Cart(items=[item(1, "1.00", "USD"), item(1, "1.00", "EUR")])

    with pytest.raises(MixedCurrencies):
        calculate_cart_total(cart)
