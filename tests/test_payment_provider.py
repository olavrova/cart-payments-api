from decimal import Decimal

import pytest

from app.payment_provider import MockPaymentProvider, PaymentProviderError


def charge(token: str):
    return MockPaymentProvider().charge(
        token=token, amount=Decimal("10.00"), currency="USD", idempotency_key="key"
    )


def test_charge_succeeds_with_a_regular_token():
    result = charge("tok_test_visa")

    assert result.succeeded
    assert result.provider_payment_id.startswith("mock_")
    assert result.failure_reason is None


def test_each_success_has_its_own_provider_payment_id():
    assert charge("tok_a").provider_payment_id != charge("tok_a").provider_payment_id


def test_charge_is_declined_when_token_contains_decline():
    result = charge("tok_decline_visa")

    assert not result.succeeded
    assert result.failure_reason == "card_declined"
    assert result.provider_payment_id is None


def test_charge_raises_when_token_contains_error():
    with pytest.raises(PaymentProviderError):
        charge("tok_error_visa")
