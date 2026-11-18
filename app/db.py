"""One SQLAlchemy session per request, closed when the request ends."""

from flask import Flask, current_app, g
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

SESSION_FACTORY_KEY = "db_session_factory"


def init_db(app: Flask) -> None:
    engine = create_engine(app.config["DATABASE_URL"])
    app.extensions[SESSION_FACTORY_KEY] = sessionmaker(bind=engine)
    app.teardown_appcontext(_close_session)


def get_session() -> Session:
    if "db_session" not in g:
        g.db_session = current_app.extensions[SESSION_FACTORY_KEY]()
    return g.db_session


def _close_session(_error: BaseException | None) -> None:
    session = g.pop("db_session", None)
    if session is not None:
        session.close()
