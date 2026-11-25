"""Starting a payment for a cart."""

import logging
import uuid
from datetime import timedelta
from typing import NoReturn

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.cart_total import CartTotal, calculate_cart_total
from app.errors import (
    CartNotActive,
    CartNotFound,
    EmptyCart,
    NothingToPay,
    PaymentInProgress,
    PaymentMethodNotFound,
    ProviderUnavailable,
)
from app.models import Cart, CartItem, CartStatus, Payment, PaymentMethod, PaymentStatus
from app.payment_provider import (
    ChargeResult,
    InvalidChargeResult,
    PaymentProvider,
    PaymentProviderError,
)

# Far longer than a provider call takes. A payment still pending this long after its
# last attempt was left by a crash, and the next request settles it.
STALE_PENDING_AFTER = timedelta(minutes=5)

PROVIDER_ERROR = "provider_error"
STALE_PENDING = "stale_pending"

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
    committed before the provider is called, so a crash never loses a charge,
    and a success is committed before the cart is checked out.
    """
    cart = _lock_users_cart(session, user_id, cart_id)
    _ensure_cart_can_be_paid(cart)
    payment_method = _find_payment_method(session, user_id, payment_method_id)
    total = calculate_cart_total(cart)
    if total.amount <= 0:
        raise NothingToPay()

    paid = _settle_earlier_payment(session, provider, cart.id, total, payment_method_id)
    if paid is not None:
        _check_out(session, paid)
        session.commit()
        return paid
    session.flush()  # free the cart's unique payment slot before the new payment takes it

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
    except InvalidChargeResult:
        _keep_pending(payment)
    else:
        _record_charge_result(payment, result)
        if payment.status == PaymentStatus.SUCCEEDED:
            session.commit()  # the money was taken: save it before anything else can fail
            _check_out(session, payment)
    session.commit()
    return payment


def _settle_earlier_payment(
    session: Session,
    provider: PaymentProvider,
    cart_id: uuid.UUID,
    total: CartTotal,
    payment_method_id: uuid.UUID | None,
) -> Payment | None:
    """Finish the cart's earlier payment, if there is one.

    Returns it if it already paid for the cart as it is now; then no new charge
    is needed. Returns None if a new payment must be made.

    A pending payment that is not stale is still in progress. A stale one was
    left by a crash: the provider says whether its charge went through.
    """
    # The unique index uq_payments_cart_active allows at most one such row.
    row = session.execute(
        select(Payment, Payment.updated_at < func.now() - STALE_PENDING_AFTER).where(
            Payment.cart_id == cart_id,
            Payment.status.in_([PaymentStatus.PENDING, PaymentStatus.SUCCEEDED]),
            ~Payment.needs_refund,
        )
    ).first()
    if row is None:
        return None
    payment, is_stale = row
    if payment.status == PaymentStatus.PENDING:
        if not is_stale:
            raise PaymentInProgress()
        _record_provider_answer(provider, payment)
    if payment.status != PaymentStatus.SUCCEEDED:
        return None
    if _pays_for(payment, total, payment_method_id):
        return payment
    payment.needs_refund = True
    logger.warning(
        "Payment %s paid for an earlier version of cart %s; refund needed.", payment.id, cart_id
    )
    return None


def _record_provider_answer(provider: PaymentProvider, payment: Payment) -> None:
    """Save what the provider did with the payment's charge. It never charges the card."""
    try:
        result = provider.find_charge(idempotency_key=str(payment.id))
    except PaymentProviderError:
        raise ProviderUnavailable() from None
    except InvalidChargeResult:
        _keep_pending(payment)
    if result is None:
        payment.mark_failed(STALE_PENDING)  # the charge never reached the provider
    else:
        _record_charge_result(payment, result)


def _record_charge_result(payment: Payment, result: ChargeResult) -> None:
    # ChargeResult rejects a success without an id and a decline without a reason,
    # so the asserts only tell the type checker what is already guaranteed.
    if result.succeeded:
        assert result.provider_payment_id is not None
        payment.mark_succeeded(result.provider_payment_id)
    else:
        assert result.failure_reason is not None
        payment.mark_failed(result.failure_reason)


def _keep_pending(payment: Payment) -> NoReturn:
    """Stop on an answer that does not say whether the card was charged.

    The payment stays pending as committed. Once stale, the next attempt asks the
    provider again, so the card is never charged twice.
    """
    logger.exception("Payment %s got an invalid answer from the provider.", payment.id)
    raise ProviderUnavailable() from None


def _pays_for(payment: Payment, total: CartTotal, payment_method_id: uuid.UUID | None) -> bool:
    """Whether the payment charged the cart's current total, with the card the user chose."""
    return (
        payment.amount == total.amount
        and payment.currency == total.currency
        and payment_method_id in (None, payment.payment_method_id)
    )


def _check_out(session: Session, payment: Payment) -> None:
    """Mark the paid cart checked out, unless it stopped being active.

    The cart lock was released while the provider was called, so the cart may
    have been abandoned meanwhile. Then the charge must be refunded.
    """
    checked_out_id = session.scalar(
        update(Cart)
        .where(Cart.id == payment.cart_id, Cart.status == CartStatus.ACTIVE)
        .values(status=CartStatus.CHECKED_OUT)
        .returning(Cart.id)
    )
    if checked_out_id is None:
        payment.needs_refund = True
        logger.warning(
            "Payment %s succeeded but cart %s is no longer active; refund needed.",
            payment.id,
            payment.cart_id,
        )


def _lock_users_cart(session: Session, user_id: uuid.UUID, cart_id: uuid.UUID) -> Cart:
    """Load the cart and lock its row, so payments for one cart run one at a time.

    Another user's cart gives the same error as a missing one, to not reveal it exists.
    """
    cart = session.scalar(
        select(Cart)
        .where(Cart.id == cart_id, Cart.user_id == user_id)
        .options(selectinload(Cart.items).selectinload(CartItem.product))
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
