from flask import Flask

from app.config import load_config
from app.db import init_db
from app.errors import register_error_handlers


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(load_config())
    app.config.update(config or {})
    init_db(app)
    register_error_handlers(app)
    return app
