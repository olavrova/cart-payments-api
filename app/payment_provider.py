"""The external payment provider: an interface and a mock implementation."""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


class InvalidChargeResult(ValueError):
    """The provider's answer is incomplete, so whether the card was charged is unknown."""


@dataclass(frozen=True)
class ChargeResult:
    """A success has the provider's payment id; a decline has a reason."""

    succeeded: bool
    provider_payment_id: str | None = None
    failure_reason: str | None = None

    def __post_init__(self):
        if self.succeeded and not self.provider_payment_id:
            raise InvalidChargeResult("A succeeded charge needs a provider_payment_id.")
        if not self.succeeded and not self.failure_reason:
            raise InvalidChargeResult("A declined charge needs a failure_reason.")


class PaymentProviderError(Exception):
    """The provider could not process the charge. The card was not charged."""


class PaymentProvider(Protocol):
    def charge(
        self, *, token: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> ChargeResult:
        """Charge the card once per idempotency key.

        Calling again with the same key returns the first answer and does not
        charge again, like real providers do.
        """
        ...

    def find_charge(self, *, idempotency_key: str) -> ChargeResult | None:
        """The answer given for this key, or None if no charge was made with it.

        Only looks the charge up and never charges the card.
        """
        ...


class MockPaymentProvider:
    """Decides the outcome by the card token, so every outcome is easy to reproduce:

    - token contains "decline" -> the charge is declined;
    - token contains "error"   -> the provider fails (PaymentProviderError);
    - any other token          -> the charge succeeds.

    Answers are remembered per idempotency key for the life of the process.
    """

    def __init__(self):
        self._results: dict[str, ChargeResult] = {}

    def charge(
        self, *, token: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> ChargeResult:
        if idempotency_key not in self._results:
            self._results[idempotency_key] = self._decide(token)
        return self._results[idempotency_key]

    def find_charge(self, *, idempotency_key: str) -> ChargeResult | None:
        return self._results.get(idempotency_key)

    @staticmethod
    def _decide(token: str) -> ChargeResult:
        if "decline" in token:
            return ChargeResult(succeeded=False, failure_reason="card_declined")
        if "error" in token:
            raise PaymentProviderError("Mock provider is unavailable.")
        return ChargeResult(succeeded=True, provider_payment_id=f"mock_{uuid.uuid4().hex}")
