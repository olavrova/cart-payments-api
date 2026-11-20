"""Requests that run at the same time, each on its own database connection.

Unlike the other tests, these commit their data, so other connections can see
it. The fixture deletes the data at the end.
"""

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from app import create_app
from app.models import Cart, CartStatus, Payment, PaymentMethod, PaymentStatus, Product, User
from app.payments.routes import PAYMENT_PROVIDER_KEY
from tests.conftest import TEST_DATABASE_URL, Factory, FakePaymentProvider


@pytest.fixture
def committed(engine):
    """A factory whose rows are committed, and deleted after the test."""
    with Session(engine) as session:
        factory = Factory(session)
        yield factory
        session.rollback()
        created = factory.created
        user_ids = [row.id for row in created if isinstance(row, User)]
        product_ids = [row.id for row in created if isinstance(row, Product)]
        for model in (Payment, Cart, PaymentMethod):  # cart items go with their cart
            session.execute(delete(model).where(model.user_id.in_(user_ids)))
        session.execute(delete(User).where(User.id.in_(user_ids)))
        session.execute(delete(Product).where(Product.id.in_(product_ids)))
        session.commit()


@pytest.fixture
def provider():
    return FakePaymentProvider()


@pytest.fixture
def client(provider):
    app = create_app({"DATABASE_URL": TEST_DATABASE_URL, "TESTING": True})
    app.extensions[PAYMENT_PROVIDER_KEY] = provider
    return app.test_client()


def test_cart_abandoned_by_another_request_during_the_charge_is_kept_for_a_refund(
    engine, committed, client, provider
):
    user = committed.user()
    committed.payment_method(user)
    cart = committed.cart(user, items=[(committed.product("70.00"), 1)])
    committed.session.commit()

    def abandon_cart_from_another_connection():
        with engine.begin() as other:
            # Fails at once if the payment request still held the cart lock.
            other.execute(text("SET LOCAL lock_timeout = '1s'"))
            other.execute(
                update(Cart).where(Cart.id == cart.id).values(status=CartStatus.ABANDONED)
            )

    provider.during_charge = abandon_cart_from_another_connection

    response = client.post(f"/carts/{cart.id}/payments", headers={"X-User-Id": str(user.id)})

    assert response.status_code == 201
    assert response.get_json()["needs_refund"] is True
    with Session(engine) as check:
        payment = check.scalars(select(Payment).where(Payment.cart_id == cart.id)).one()
        assert payment.status == PaymentStatus.SUCCEEDED
        assert payment.needs_refund
        assert check.get(Cart, cart.id).status == CartStatus.ABANDONED
