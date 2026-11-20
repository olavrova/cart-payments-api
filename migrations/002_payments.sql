-- Payments for carts.
--
-- One row per payment attempt. Failed attempts are kept as history,
-- so a user can retry after a decline.
-- Status values:
--   pending    — the row is saved and the provider is being called.
--   succeeded  — the provider charged the card.
--   failed     — the provider declined the charge or returned an error.
-- needs_refund marks a succeeded charge that did not pay for the cart: the cart
-- changed or stopped being active meanwhile. The money must go back to the user.


-- Targets for the composite foreign keys below. They let the database check
-- that a payment's cart and payment method belong to the same user.
ALTER TABLE carts
    ADD CONSTRAINT uq_carts_id_user_id UNIQUE (id, user_id);
ALTER TABLE user_payment_methods
    ADD CONSTRAINT uq_user_payment_methods_id_user_id UNIQUE (id, user_id);


CREATE TABLE payments (
    id                  UUID           PRIMARY KEY DEFAULT gen_random_uuid(),
    cart_id             UUID           NOT NULL,
    user_id             UUID           NOT NULL REFERENCES users(id),
    payment_method_id   UUID           NOT NULL,
    amount              NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    currency            CHAR(3)        NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    status              TEXT           NOT NULL DEFAULT 'pending'
                                       CHECK (status IN ('pending', 'succeeded', 'failed')),
    provider_payment_id TEXT,
    failure_reason      TEXT,
    needs_refund        BOOLEAN        NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ    NOT NULL DEFAULT NOW(),

    -- The cart and the payment method must belong to the paying user.
    CONSTRAINT fk_payments_cart_user
        FOREIGN KEY (cart_id, user_id) REFERENCES carts(id, user_id),
    CONSTRAINT fk_payments_payment_method_user
        FOREIGN KEY (payment_method_id, user_id) REFERENCES user_payment_methods(id, user_id),

    -- Only a succeeded payment has the provider's id; only a failed one has a reason.
    CONSTRAINT ck_payments_provider_id_iff_succeeded
        CHECK ((status = 'succeeded') = (provider_payment_id IS NOT NULL)),
    CONSTRAINT ck_payments_failure_reason_iff_failed
        CHECK ((status = 'failed') = (failure_reason IS NOT NULL)),
    -- Only money that was taken can be refunded.
    CONSTRAINT ck_payments_needs_refund_only_if_succeeded
        CHECK (NOT needs_refund OR status = 'succeeded')
);

CREATE INDEX idx_payments_cart_id ON payments(cart_id);
CREATE INDEX idx_payments_user_id ON payments(user_id);

-- A cart is paid at most once: no two in-flight or succeeded payments per cart.
-- This holds even for concurrent requests. Failed attempts and charges to be
-- refunded do not block a retry.
CREATE UNIQUE INDEX uq_payments_cart_active
    ON payments(cart_id)
    WHERE status IN ('pending', 'succeeded') AND NOT needs_refund;

-- One provider charge maps to exactly one payment.
CREATE UNIQUE INDEX uq_payments_provider_payment_id
    ON payments(provider_payment_id)
    WHERE provider_payment_id IS NOT NULL;
