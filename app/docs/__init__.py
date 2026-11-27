"""API docs: the OpenAPI spec and a Swagger UI page that renders it.

The spec is written by hand; tests/test_openapi.py checks it against the routes
and the responses, so it cannot drift from the code unnoticed.
"""

from pathlib import Path

from flask import Blueprint, send_from_directory

DOCS_DIR = Path(__file__).parent
SPEC_FILE = "openapi.yaml"

docs_blueprint = Blueprint("docs", __name__)


@docs_blueprint.get("/openapi.yaml")
def openapi_spec():
    return send_from_directory(DOCS_DIR, SPEC_FILE, mimetype="application/yaml")


@docs_blueprint.get("/docs")
def swagger_ui():
    return send_from_directory(DOCS_DIR, "swagger_ui.html")
