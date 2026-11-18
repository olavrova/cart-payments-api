"""Starting a payment for a cart."""

import uuid
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.cart_total import calculate_cart_total
from app.errors import (
    CartNotActive,
    CartNotFound,
    EmptyCart,
    NothingToPay,
    PaymentInProgress,
    PaymentMethodNotFound,
)
from app.models import Cart, CartStatus, Payment, PaymentMethod, PaymentStatus
from app.payment_provider import PaymentProvider, PaymentProviderError

# Far longer than a provider call takes. A pending payment this old was left by a crash.
STALE_PENDING_AFTER = timedelta(minutes=5)

PROVIDER_ERROR = "provider_error"
STALE_PENDING = "stale_pending"


def start_payment(
    session: Session,
    provider: PaymentProvider,
    *,
    user_id: uuid.UUID,
    cart_id: uuid.UUID,
    payment_method_id: uuid.UUID | None = None,
) -> Payment:
    """Charge the user's card for the cart and return the payment.

    The returned payment is either succeeded or failed. The pending payment is
    committed before the provider is called, so a crash never loses a charge.
    """
    cart = _lock_users_cart(session, user_id, cart_id)
    _ensure_cart_can_be_paid(session, cart)
    payment_method = _find_payment_method(session, user_id, payment_method_id)
    total = calculate_cart_total(cart)
    if total.amount <= 0:
        raise NothingToPay()

    payment = Payment(
        id=uuid.uuid4(),
        cart_id=cart.id,
        user_id=user_id,
        payment_method=payment_method,
        amount=total.amount,
        currency=total.currency,
    )
    session.add(payment)
    session.commit()

    try:
        result = provider.charge(
            token=payment_method.provider_token,
            amount=payment.amount,
            currency=payment.currency,
            idempotency_key=str(payment.id),
        )
    except PaymentProviderError:
        payment.mark_failed(PROVIDER_ERROR)
    else:
        if result.succeeded:
            payment.mark_succeeded(result.provider_payment_id)
            cart.status = CartStatus.CHECKED_OUT
        else:
            payment.mark_failed(result.failure_reason)
    session.commit()
    return payment


def _lock_users_cart(session: Session, user_id: uuid.UUID, cart_id: uuid.UUID) -> Cart:
    """Load the cart and lock its row, so payments for one cart run one at a time.

    Another user's cart gives the same error as a missing one, to not reveal it exists.
    """
    cart = session.scalar(
        select(Cart).where(Cart.id == cart_id, Cart.user_id == user_id).with_for_update()
    )
    if cart is None:
        raise CartNotFound()
    return cart


def _ensure_cart_can_be_paid(session: Session, cart: Cart) -> None:
    if cart.status != CartStatus.ACTIVE:
        raise CartNotActive()
    if not cart.items:
        raise EmptyCart()
    _fail_stale_pending_payments(session, cart.id)
    if _has_active_payment(session, cart.id):
        raise PaymentInProgress()


def _fail_stale_pending_payments(session: Session, cart_id: uuid.UUID) -> None:
    session.execute(
        update(Payment)
        .where(
            Payment.cart_id == cart_id,
            Payment.status == PaymentStatus.PENDING,
            Payment.created_at < func.now() - STALE_PENDING_AFTER,
        )
        .values(status=PaymentStatus.FAILED, failure_reason=STALE_PENDING)
    )


def _has_active_payment(session: Session, cart_id: uuid.UUID) -> bool:
    return session.scalar(
        select(
            select(Payment)
            .where(
                Payment.cart_id == cart_id,
                Payment.status.in_([PaymentStatus.PENDING, PaymentStatus.SUCCEEDED]),
            )
            .exists()
        )
    )


def _find_payment_method(
    session: Session, user_id: uuid.UUID, payment_method_id: uuid.UUID | None
) -> PaymentMethod:
    """The given payment method of the user, or the user's default one."""
    query = select(PaymentMethod).where(PaymentMethod.user_id == user_id)
    if payment_method_id is None:
        query = query.where(PaymentMethod.is_default).order_by(PaymentMethod.created_at.desc())
    else:
        query = query.where(PaymentMethod.id == payment_method_id)
    payment_method = session.scalars(query.limit(1)).first()
    if payment_method is None:
        raise PaymentMethodNotFound()
    return payment_method
