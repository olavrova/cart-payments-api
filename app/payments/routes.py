"""HTTP layer: parse the request, call the service, build the response."""

import uuid
from http import HTTPStatus

from flask import Blueprint, current_app, jsonify, request

from app.db import get_session
from app.errors import InvalidRequest
from app.models import Payment, PaymentStatus
from app.payments.service import PROVIDER_ERROR, start_payment

PAYMENT_PROVIDER_KEY = "payment_provider"
USER_ID_HEADER = "X-User-Id"

payments_blueprint = Blueprint("payments", __name__)


@payments_blueprint.post("/carts/<cart_id>/payments")
def create_payment(cart_id: str):
    body = _json_body()
    payment_method_id = body.get("payment_method_id")
    payment = start_payment(
        get_session(),
        current_app.extensions[PAYMENT_PROVIDER_KEY],
        user_id=_parse_uuid(request.headers.get(USER_ID_HEADER), f"{USER_ID_HEADER} header"),
        cart_id=_parse_uuid(cart_id, "cart id"),
        payment_method_id=(
            None
            if payment_method_id is None
            else _parse_uuid(payment_method_id, "payment_method_id")
        ),
    )
    return jsonify(_payment_json(payment)), _status_code(payment)


def _json_body() -> dict:
    """The optional JSON object body. An empty body means no options."""
    if not request.get_data():
        return {}
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        raise InvalidRequest("Request body must be a JSON object.")
    return body


def _parse_uuid(value: object, name: str) -> uuid.UUID:
    if not isinstance(value, str):
        raise InvalidRequest(f"{name} is required.")
    try:
        return uuid.UUID(value)
    except ValueError:
        raise InvalidRequest(f"{name} must be a UUID.") from None


def _status_code(payment: Payment) -> HTTPStatus:
    if payment.status == PaymentStatus.SUCCEEDED:
        return HTTPStatus.CREATED
    if payment.failure_reason == PROVIDER_ERROR:
        return HTTPStatus.BAD_GATEWAY
    return HTTPStatus.PAYMENT_REQUIRED


def _payment_json(payment: Payment) -> dict:
    return {
        "id": str(payment.id),
        "cart_id": str(payment.cart_id),
        "amount": str(payment.amount),
        "currency": payment.currency,
        "status": payment.status,
        "last_four": payment.payment_method.last_four,
        "failure_reason": payment.failure_reason,
        "created_at": payment.created_at.isoformat(),
    }
