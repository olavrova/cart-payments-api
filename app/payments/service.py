"""Starting a payment for a cart."""

import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, select
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

# Far longer than a provider call takes. A payment still pending this long after its
# last attempt was left by a crash, and the next request resumes it.
STALE_PENDING_AFTER = timedelta(minutes=5)

PROVIDER_ERROR = "provider_error"

logger = logging.getLogger(__name__)


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
    A payment left pending by a crash is resumed with its own idempotency key,
    so the provider never charges the card twice for it.
    """
    cart = _lock_users_cart(session, user_id, cart_id)
    _ensure_cart_can_be_paid(cart)
    payment = _resume_stale_payment(session, cart.id) or _new_payment(
        session, cart, user_id, payment_method_id
    )
    session.commit()

    try:
        result = provider.charge(
            token=payment.payment_method.provider_token,
            amount=payment.amount,
            currency=payment.currency,
            idempotency_key=str(payment.id),
        )
    except PaymentProviderError:
        payment.mark_failed(PROVIDER_ERROR)
    else:
        if result.succeeded:
            payment.mark_succeeded(result.provider_payment_id)
            _check_out(_lock_users_cart(session, user_id, cart_id), payment)
        else:
            payment.mark_failed(result.failure_reason)
    session.commit()
    return payment


def _check_out(cart: Cart, payment: Payment) -> None:
    """Mark the paid cart checked out, unless it changed status during the charge.

    The lock was released while the provider was called. The charge is recorded
    either way, because the money was taken.
    """
    if cart.status == CartStatus.ACTIVE:
        cart.status = CartStatus.CHECKED_OUT
    else:
        logger.warning(
            "Payment %s succeeded but cart %s became %s during the charge; refund needed.",
            payment.id,
            cart.id,
            cart.status,
        )


def _lock_users_cart(session: Session, user_id: uuid.UUID, cart_id: uuid.UUID) -> Cart:
    """Load the cart and lock its row, so payments for one cart run one at a time.

    Another user's cart gives the same error as a missing one, to not reveal it exists.
    """
    cart = session.scalar(
        select(Cart)
        .where(Cart.id == cart_id, Cart.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)  # read the locked row, not a cached copy
    )
    if cart is None:
        raise CartNotFound()
    return cart


def _ensure_cart_can_be_paid(cart: Cart) -> None:
    if cart.status != CartStatus.ACTIVE:
        raise CartNotActive()
    if not cart.items:
        raise EmptyCart()


def _resume_stale_payment(session: Session, cart_id: uuid.UUID) -> Payment | None:
    """The cart's pending payment left by a crash, to retry, or None if there is none.

    It keeps its id, amount and card: the provider then answers the first charge
    again instead of charging twice. A fresh pending or a succeeded payment means
    a payment is in progress.
    """
    row = session.execute(
        select(Payment, Payment.updated_at < func.now() - STALE_PENDING_AFTER).where(
            Payment.cart_id == cart_id,
            Payment.status.in_([PaymentStatus.PENDING, PaymentStatus.SUCCEEDED]),
        )
    ).first()
    if row is None:
        return None
    payment, is_stale = row
    if payment.status != PaymentStatus.PENDING or not is_stale:
        raise PaymentInProgress()
    payment.updated_at = func.now()  # this attempt is not stale while it runs
    return payment


def _new_payment(
    session: Session, cart: Cart, user_id: uuid.UUID, payment_method_id: uuid.UUID | None
) -> Payment:
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
    return payment


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
