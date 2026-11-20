import uuid
from decimal import Decimal

import pytest

from app.payment_provider import (
    ChargeResult,
    IdempotencyKeyReused,
    MockPaymentProvider,
    PaymentProviderError,
)


def charge(token: str, provider=None, idempotency_key=None, amount="10.00"):
    return (provider or MockPaymentProvider()).charge(
        token=token,
        amount=Decimal(amount),
        currency="USD",
        idempotency_key=idempotency_key or str(uuid.uuid4()),
    )


def test_charge_succeeds_with_a_regular_token():
    result = charge("tok_test_visa")

    assert result.succeeded
    assert result.provider_payment_id.startswith("mock_")
    assert result.failure_reason is None


def test_each_charge_has_its_own_provider_payment_id():
    provider = MockPaymentProvider()

    assert charge("tok_a", provider).provider_payment_id != (
        charge("tok_a", provider).provider_payment_id
    )


def test_same_idempotency_key_returns_the_first_answer_without_charging_again():
    provider = MockPaymentProvider()

    first = charge("tok_a", provider, idempotency_key="payment-1")
    again = charge("tok_a", provider, idempotency_key="payment-1")

    assert again == first


@pytest.mark.parametrize(("token", "amount"), [("tok_b", "10.00"), ("tok_a", "99.00")])
def test_key_reused_with_other_charge_details_is_rejected(token, amount):
    provider = MockPaymentProvider()
    charge("tok_a", provider, idempotency_key="payment-1", amount="10.00")

    with pytest.raises(IdempotencyKeyReused):
        charge(token, provider, idempotency_key="payment-1", amount=amount)


def test_oldest_keys_are_forgotten_after_max_keys():
    provider = MockPaymentProvider(max_keys=2)
    for key in ["payment-1", "payment-2", "payment-3"]:
        charge("tok_a", provider, idempotency_key=key)

    assert provider.find_charge(idempotency_key="payment-1") is None
    assert provider.find_charge(idempotency_key="payment-3") is not None


def test_find_charge_returns_the_answer_for_the_key_without_charging():
    provider = MockPaymentProvider()
    first = charge("tok_a", provider, idempotency_key="payment-1")

    assert provider.find_charge(idempotency_key="payment-1") == first
    assert provider.find_charge(idempotency_key="payment-2") is None


def test_charge_is_declined_when_token_contains_decline():
    result = charge("tok_decline_visa")

    assert not result.succeeded
    assert result.failure_reason == "card_declined"
    assert result.provider_payment_id is None


def test_charge_raises_when_token_contains_error():
    with pytest.raises(PaymentProviderError):
        charge("tok_error_visa")


def test_provider_error_is_not_remembered_so_a_retry_can_succeed():
    provider = MockPaymentProvider()
    with pytest.raises(PaymentProviderError):
        charge("tok_error_visa", provider, idempotency_key="payment-1")

    assert charge("tok_ok", provider, idempotency_key="payment-1").succeeded


@pytest.mark.parametrize(
    "fields",
    [
        {"succeeded": True},
        {"succeeded": True, "provider_payment_id": ""},
        {"succeeded": False},
        {"succeeded": False, "failure_reason": ""},
    ],
)
def test_charge_result_without_its_required_field_is_rejected(fields):
    with pytest.raises(ValueError):
        ChargeResult(**fields)
