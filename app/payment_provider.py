"""The external payment provider: an interface and a mock implementation."""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class ChargeResult:
    succeeded: bool
    provider_payment_id: str | None = None
    failure_reason: str | None = None


class PaymentProviderError(Exception):
    """The provider could not process the charge. The card was not charged."""


class PaymentProvider(Protocol):
    def charge(
        self, *, token: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> ChargeResult: ...


class MockPaymentProvider:
    """Decides the outcome by the card token, so every outcome is easy to reproduce:

    - token contains "decline" -> the charge is declined;
    - token contains "error"   -> the provider fails (PaymentProviderError);
    - any other token          -> the charge succeeds.
    """

    def charge(
        self, *, token: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> ChargeResult:
        if "decline" in token:
            return ChargeResult(succeeded=False, failure_reason="card_declined")
        if "error" in token:
            raise PaymentProviderError("Mock provider is unavailable.")
        return ChargeResult(succeeded=True, provider_payment_id=f"mock_{uuid.uuid4().hex}")
