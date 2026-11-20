"""Fixtures for endpoint tests against the real PostgreSQL test database.

Each test runs inside one transaction that is rolled back at the end. The app's
commits become savepoints inside it, so tests never leave data behind.
"""

import os
import uuid
from collections.abc import Callable, Sequence
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import create_app
from app.db import SESSION_FACTORY_KEY
from app.models import Cart, CartItem, CartStatus, PaymentMethod, Product, User
from app.payment_provider import ChargeResult, PaymentProviderError
from app.payments.routes import PAYMENT_PROVIDER_KEY

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://shop:shop@localhost:5433/cart_payments_test"
)


class FakePaymentProvider:
    """Records every charge and answers with the outcome the test sets.

    during_charge, if set, runs inside the call, to simulate changes made by
    other requests while the provider is working. charges holds the answer per
    idempotency key, which find_charge looks up; tests can add to it.
    """

    SUCCEED, DECLINE, ERROR, INVALID = "succeed", "decline", "error", "invalid"

    def __init__(self):
        self.outcome = self.SUCCEED
        self.calls: list[dict] = []
        self.charges: dict[str, ChargeResult] = {}
        self.lookup_error: Exception | None = None
        self.during_charge: Callable[[], None] | None = None

    def charge(self, **charge_args) -> ChargeResult:
        self.calls.append(charge_args)
        if self.during_charge is not None:
            self.during_charge()
        if self.outcome == self.ERROR:
            raise PaymentProviderError("Provider is down.")
        if self.outcome == self.INVALID:
            return ChargeResult(succeeded=True)  # raises InvalidChargeResult
        if self.outcome == self.DECLINE:
            result = ChargeResult(succeeded=False, failure_reason="card_declined")
        else:
            result = ChargeResult(succeeded=True, provider_payment_id=f"fake_{uuid.uuid4().hex}")
        self.charges[charge_args["idempotency_key"]] = result
        return result

    def find_charge(self, *, idempotency_key: str) -> ChargeResult | None:
        if self.lookup_error is not None:
            raise self.lookup_error
        return self.charges.get(idempotency_key)


class Factory:
    """Creates test data. Each call makes new rows, independent from the sample data."""

    def __init__(self, session: Session):
        self.session = session

    def user(self) -> User:
        user_id = uuid.uuid4()
        return self._save(User(id=user_id, email=f"{user_id}@example.com", name="Test User"))

    def product(self, price: str = "10.00", currency: str = "USD") -> Product:
        return self._save(Product(name="Test Product", price=Decimal(price), currency=currency))

    def cart(
        self,
        user: User,
        items: Sequence[tuple[Product, int]] = (),
        status: CartStatus = CartStatus.ACTIVE,
    ) -> Cart:
        cart = Cart(
            user_id=user.id,
            status=status,
            items=[
                CartItem(product=product, quantity=quantity, unit_price=product.price)
                for product, quantity in items
            ],
        )
        return self._save(cart)

    def payment_method(
        self, user: User, token: str = "tok_test_visa", is_default: bool = True
    ) -> PaymentMethod:
        return self._save(
            PaymentMethod(
                user_id=user.id, provider_token=token, last_four="4242", is_default=is_default
            )
        )

    def _save(self, entity):
        self.session.add(entity)
        self.session.flush()
        return entity


@pytest.fixture(scope="session")
def engine():
    engine = create_engine(TEST_DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture
def connection(engine):
    with engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()


@pytest.fixture
def session_factory(connection):
    return sessionmaker(bind=connection, join_transaction_mode="create_savepoint")


@pytest.fixture
def db(session_factory):
    """Session the test uses to create data and to check the results."""
    with session_factory() as session:
        yield session


@pytest.fixture
def provider():
    return FakePaymentProvider()


@pytest.fixture
def app(session_factory, provider):
    app = create_app({"DATABASE_URL": TEST_DATABASE_URL, "TESTING": True})
    app.extensions[SESSION_FACTORY_KEY] = session_factory
    app.extensions[PAYMENT_PROVIDER_KEY] = provider
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def factory(db):
    return Factory(db)
