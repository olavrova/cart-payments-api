from flask import Flask

from app.config import load_config
from app.db import init_db
from app.errors import register_error_handlers
from app.payment_provider import MockPaymentProvider
from app.payments.routes import PAYMENT_PROVIDER_KEY, payments_blueprint


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(load_config())
    app.config.update(config or {})
    init_db(app)
    register_error_handlers(app)
    app.extensions[PAYMENT_PROVIDER_KEY] = MockPaymentProvider()
    app.register_blueprint(payments_blueprint)
    return app
