import os

DEFAULT_DATABASE_URL = "postgresql+psycopg://shop:shop@localhost:5433/cart_payments"


def load_config() -> dict:
    return {"DATABASE_URL": os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)}
