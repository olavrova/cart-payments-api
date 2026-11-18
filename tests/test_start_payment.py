import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import CartStatus, Payment, PaymentStatus
from app.payment_provider import MockPaymentProvider
from app.payments.routes import PAYMENT_PROVIDER_KEY
from app.payments.service import PROVIDER_ERROR


@pytest.fixture
def user(factory):
    return factory.user()


@pytest.fixture
def card(factory, user):
    return factory.payment_method(user)


@pytest.fixture
def cart(factory, user, card):
    """An active cart worth 45.00 + 2 * 12.50 = 70.00 USD."""
    return factory.cart(user, items=[(factory.product("45.00"), 1), (factory.product("12.50"), 2)])


def pay(client, user, cart_id, json=None, headers=None):
    return client.post(
        f"/carts/{cart_id}/payments",
        headers={"X-User-Id": str(user.id)} if headers is None else headers,
        json=json,
    )


def payments_of(db, cart):
    db.expire_all()
    return db.scalars(select(Payment).where(Payment.cart_id == cart.id)).all()


def add_pending_payment(db, cart, card, last_attempt_at=None):
    payment = Payment(
        cart_id=cart.id,
        user_id=cart.user_id,
        payment_method_id=card.id,
        amount=Decimal("70.00"),
        currency="USD",
    )
    if last_attempt_at is not None:
        payment.created_at = payment.updated_at = last_attempt_at
    db.add(payment)
    db.flush()
    return payment


def test_successful_payment_checks_out_the_cart(client, db, user, cart):
    response = pay(client, user, cart.id)

    assert response.status_code == 201
    body = response.get_json()
    assert body["status"] == "succeeded"
    assert body["amount"] == "70.00"
    assert body["currency"] == "USD"
    assert body["last_four"] == "4242"
    assert body["failure_reason"] is None
    [payment] = payments_of(db, cart)
    assert str(payment.id) == body["id"]
    assert payment.status == PaymentStatus.SUCCEEDED
    assert payment.provider_payment_id.startswith("fake_")
    db.refresh(cart)
    assert cart.status == CartStatus.CHECKED_OUT


def test_provider_is_charged_with_card_token_amount_and_idempotency_key(
    client, provider, user, card, cart
):
    body = pay(client, user, cart.id).get_json()

    assert provider.calls == [
        {
            "token": card.provider_token,
            "amount": Decimal("70.00"),
            "currency": "USD",
            "idempotency_key": body["id"],
        }
    ]


def test_declined_payment_is_failed_and_cart_stays_active(client, db, provider, user, cart):
    provider.outcome = provider.DECLINE

    response = pay(client, user, cart.id)

    assert response.status_code == 402
    assert response.get_json()["status"] == "failed"
    assert response.get_json()["failure_reason"] == "card_declined"
    [payment] = payments_of(db, cart)
    assert payment.status == PaymentStatus.FAILED
    db.refresh(cart)
    assert cart.status == CartStatus.ACTIVE


def test_retry_after_decline_succeeds_and_keeps_the_failed_attempt(
    client, db, provider, user, cart
):
    provider.outcome = provider.DECLINE
    pay(client, user, cart.id)
    provider.outcome = provider.SUCCEED

    response = pay(client, user, cart.id)

    assert response.status_code == 201
    statuses = sorted(payment.status for payment in payments_of(db, cart))
    assert statuses == [PaymentStatus.FAILED, PaymentStatus.SUCCEEDED]


def test_provider_error_fails_the_payment_with_bad_gateway(client, db, provider, user, cart):
    provider.outcome = provider.ERROR

    response = pay(client, user, cart.id)

    assert response.status_code == 502
    assert response.get_json()["failure_reason"] == PROVIDER_ERROR
    [payment] = payments_of(db, cart)
    assert payment.status == PaymentStatus.FAILED
    db.refresh(cart)
    assert cart.status == CartStatus.ACTIVE


def test_paying_a_checked_out_cart_is_a_conflict(client, provider, user, cart):
    pay(client, user, cart.id)

    response = pay(client, user, cart.id)

    assert response.status_code == 409
    assert response.get_json() == {"error": "Cart is not active."}
    assert len(provider.calls) == 1


@pytest.mark.parametrize("status", [CartStatus.CHECKED_OUT, CartStatus.ABANDONED])
def test_paying_an_inactive_cart_is_a_conflict(client, factory, user, card, status):
    cart = factory.cart(user, items=[(factory.product(), 1)], status=status)

    assert pay(client, user, cart.id).status_code == 409


def test_payment_in_progress_is_a_conflict(client, db, provider, user, card, cart):
    add_pending_payment(db, cart, card)

    response = pay(client, user, cart.id)

    assert response.status_code == 409
    assert response.get_json() == {"error": "A payment for this cart is already in progress."}
    assert provider.calls == []


def ten_minutes_ago():
    return datetime.now(UTC) - timedelta(minutes=10)


def test_stale_pending_payment_is_resumed_with_its_own_idempotency_key(
    client, db, factory, provider, user, card, cart
):
    stale = add_pending_payment(db, cart, card, last_attempt_at=ten_minutes_ago())
    other_card = factory.payment_method(user, token="tok_test_other", is_default=False)

    response = pay(client, user, cart.id, json={"payment_method_id": str(other_card.id)})

    assert response.status_code == 201
    assert response.get_json()["id"] == str(stale.id)
    assert provider.calls == [
        {
            "token": card.provider_token,
            "amount": Decimal("70.00"),
            "currency": "USD",
            "idempotency_key": str(stale.id),
        }
    ]
    [payment] = payments_of(db, cart)
    assert payment.status == PaymentStatus.SUCCEEDED


def test_card_charged_before_a_crash_is_not_charged_again(app, client, db, user, card, cart):
    mock_provider = MockPaymentProvider()
    app.extensions[PAYMENT_PROVIDER_KEY] = mock_provider
    stale = add_pending_payment(db, cart, card, last_attempt_at=ten_minutes_ago())
    # The provider charged the card, then the app crashed before saving the result.
    first_charge = mock_provider.charge(
        token=card.provider_token,
        amount=stale.amount,
        currency=stale.currency,
        idempotency_key=str(stale.id),
    )

    response = pay(client, user, cart.id)

    assert response.status_code == 201
    [payment] = payments_of(db, cart)
    assert payment.provider_payment_id == first_charge.provider_payment_id


def test_cart_of_another_user_is_not_found(client, factory, provider, cart):
    other_user = factory.user()
    factory.payment_method(other_user)

    response = pay(client, other_user, cart.id)

    assert response.status_code == 404
    assert provider.calls == []


def test_unknown_cart_is_not_found(client, user, card):
    assert pay(client, user, uuid.uuid4()).status_code == 404


def test_malformed_cart_id_is_a_bad_request(client, user):
    response = pay(client, user, "not-a-uuid")

    assert response.status_code == 400
    assert response.get_json() == {"error": "cart id must be a UUID."}


@pytest.mark.parametrize(
    ("headers", "error"),
    [
        ({}, "X-User-Id header is required."),
        ({"X-User-Id": "alice"}, "X-User-Id header must be a UUID."),
    ],
)
def test_missing_or_malformed_user_id_is_a_bad_request(client, user, cart, headers, error):
    response = pay(client, user, cart.id, headers=headers)

    assert response.status_code == 400
    assert response.get_json() == {"error": error}


def test_body_that_is_not_a_json_object_is_a_bad_request(client, user, cart):
    response = client.post(
        f"/carts/{cart.id}/payments",
        headers={"X-User-Id": str(user.id), "Content-Type": "application/json"},
        data="[1, 2",
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Request body must be a JSON object."}


def test_empty_cart_cannot_be_paid(client, factory, user, card):
    cart = factory.cart(user)

    response = pay(client, user, cart.id)

    assert response.status_code == 422
    assert response.get_json() == {"error": "Cart is empty."}


def test_cart_with_zero_total_cannot_be_paid(client, factory, user, card):
    cart = factory.cart(user, items=[(factory.product("0.00"), 1)])

    response = pay(client, user, cart.id)

    assert response.status_code == 422
    assert response.get_json() == {"error": "Cart total must be greater than zero."}


def test_cart_with_mixed_currencies_cannot_be_paid(client, factory, user, card):
    cart = factory.cart(
        user, items=[(factory.product(currency="USD"), 1), (factory.product(currency="EUR"), 1)]
    )

    assert pay(client, user, cart.id).status_code == 422


def test_user_without_payment_methods_cannot_pay(client, factory, provider):
    user = factory.user()
    cart = factory.cart(user, items=[(factory.product(), 1)])

    response = pay(client, user, cart.id)

    assert response.status_code == 422
    assert response.get_json() == {"error": "Payment method not found."}
    assert provider.calls == []


def test_user_without_a_default_payment_method_must_choose_one(client, factory):
    user = factory.user()
    factory.payment_method(user, is_default=False)
    cart = factory.cart(user, items=[(factory.product(), 1)])

    assert pay(client, user, cart.id).status_code == 422


def test_explicit_payment_method_is_used_instead_of_the_default(
    client, factory, provider, user, cart
):
    other_card = factory.payment_method(user, token="tok_test_other", is_default=False)

    response = pay(client, user, cart.id, json={"payment_method_id": str(other_card.id)})

    assert response.status_code == 201
    assert provider.calls[0]["token"] == "tok_test_other"


def test_payment_method_of_another_user_cannot_be_used(client, factory, provider, user, cart):
    stranger_card = factory.payment_method(factory.user())

    response = pay(client, user, cart.id, json={"payment_method_id": str(stranger_card.id)})

    assert response.status_code == 422
    assert provider.calls == []


def test_malformed_payment_method_id_is_a_bad_request(client, user, cart):
    response = pay(client, user, cart.id, json={"payment_method_id": 42})

    assert response.status_code == 400
