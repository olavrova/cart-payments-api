"""Errors the API returns to the client, each with its HTTP status."""

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException


class ApiError(Exception):
    status_code = 400
    message = "Bad request."

    def __init__(self, message: str | None = None):
        super().__init__(message or self.message)
        self.message = message or self.message


class InvalidRequest(ApiError):
    status_code = 400


class CartNotFound(ApiError):
    status_code = 404
    message = "Cart not found."


class CartNotActive(ApiError):
    status_code = 409
    message = "Cart is not active."


class PaymentInProgress(ApiError):
    status_code = 409
    message = "A payment for this cart is already in progress."


class UnprocessableCart(ApiError):
    status_code = 422


class EmptyCart(UnprocessableCart):
    message = "Cart is empty."


class NothingToPay(UnprocessableCart):
    message = "Cart total must be greater than zero."


class MixedCurrencies(UnprocessableCart):
    message = "Cart items have different currencies."


class PaymentMethodNotFound(UnprocessableCart):
    message = "Payment method not found."


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        return jsonify(error=error.message), error.status_code

    @app.errorhandler(HTTPException)
    def handle_http_exception(error: HTTPException):
        return jsonify(error=error.description), error.code
