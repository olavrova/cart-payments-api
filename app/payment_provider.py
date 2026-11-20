"""The external payment provider: an interface and a mock implementation."""

import threading
import uuid
from collections import OrderedDict
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


class IdempotencyKeyReused(Exception):
    """A key was sent again with another card, amount or currency. Nothing was charged.

    Real providers reject such a request, because it is a bug in the caller. It is
    not a PaymentProviderError: whether the key's first charge went through is
    unknown to the caller, so the payment must not be marked failed.
    """


class MockPaymentProvider:
    """Decides the outcome by the card token, so every outcome is easy to reproduce:

    - token contains "decline" -> the charge is declined;
    - token contains "error"   -> the provider fails (PaymentProviderError);
    - any other token          -> the charge succeeds.

    Like a real provider, it remembers the answer per idempotency key, rejects a
    key reused with other charge details, and forgets the oldest keys after
    max_keys, so memory stays bounded. Safe to use from several threads.
    """

    def __init__(self, max_keys: int = 10_000):
        self._max_keys = max_keys
        self._charges: OrderedDict[str, tuple[tuple, ChargeResult]] = OrderedDict()
        self._lock = threading.Lock()

    def charge(
        self, *, token: str, amount: Decimal, currency: str, idempotency_key: str
    ) -> ChargeResult:
        details = (token, amount, currency)
        with self._lock:
            if idempotency_key in self._charges:
                first_details, result = self._charges[idempotency_key]
                if details != first_details:
                    raise IdempotencyKeyReused(idempotency_key)
                return result
            result = self._decide(token)
            self._charges[idempotency_key] = (details, result)
            if len(self._charges) > self._max_keys:
                self._charges.popitem(last=False)
            return result

    def find_charge(self, *, idempotency_key: str) -> ChargeResult | None:
        with self._lock:
            charge = self._charges.get(idempotency_key)
        return None if charge is None else charge[1]

    @staticmethod
    def _decide(token: str) -> ChargeResult:
        if "decline" in token:
            return ChargeResult(succeeded=False, failure_reason="card_declined")
        if "error" in token:
            raise PaymentProviderError("Mock provider is unavailable.")
        return ChargeResult(succeeded=True, provider_payment_id=f"mock_{uuid.uuid4().hex}")
