-- Payments for carts.
--
-- One row per payment attempt. Failed attempts are kept as history,
-- so a user can retry after a decline.
-- Status values:
--   pending    — the row is saved and the provider is being called.
--   succeeded  — the provider charged the card.
--   failed     — the provider declined the charge or returned an error.

CREATE TABLE payments (
    id                  UUID           PRIMARY KEY DEFAULT gen_random_uuid(),
    cart_id             UUID           NOT NULL REFERENCES carts(id),
    user_id             UUID           NOT NULL REFERENCES users(id),
    payment_method_id   UUID           NOT NULL REFERENCES user_payment_methods(id),
    amount              NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    currency            CHAR(3)        NOT NULL,
    status              TEXT           NOT NULL DEFAULT 'pending'
                                       CHECK (status IN ('pending', 'succeeded', 'failed')),
    provider_payment_id TEXT,
    failure_reason      TEXT,
    created_at          TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ    NOT NULL DEFAULT NOW(),

    -- A succeeded payment always has the provider's id; a failed one has a reason.
    CHECK (status <> 'succeeded' OR provider_payment_id IS NOT NULL),
    CHECK (status <> 'failed' OR failure_reason IS NOT NULL)
);

CREATE INDEX idx_payments_cart_id ON payments(cart_id);
CREATE INDEX idx_payments_user_id ON payments(user_id);

-- A cart is paid at most once: no two in-flight or succeeded payments per cart.
-- This holds even for concurrent requests. Failed attempts do not block a retry.
CREATE UNIQUE INDEX uq_payments_cart_active
    ON payments(cart_id)
    WHERE status IN ('pending', 'succeeded');
