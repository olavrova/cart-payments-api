"""The hand-written OpenAPI spec must match what the app really does."""

from http import HTTPStatus

import pytest
import yaml

from app.errors import ApiError

PAYMENTS_PATH = "/carts/{cart_id}/payments"
DOCS_ENDPOINTS = {"docs.openapi_spec", "docs.swagger_ui"}
# Statuses that return a payment body (see routes._status_code).
PAYMENT_STATUSES = {HTTPStatus.CREATED, HTTPStatus.PAYMENT_REQUIRED, HTTPStatus.BAD_GATEWAY}


@pytest.fixture
def spec(client):
    response = client.get("/openapi.yaml")
    assert response.status_code == 200
    assert response.mimetype == "application/yaml"
    return yaml.safe_load(response.data)


def _all_subclasses(cls: type) -> set[type]:
    return {sub for direct in cls.__subclasses__() for sub in {direct, *_all_subclasses(direct)}}


def test_swagger_ui_page_loads_the_spec(client):
    response = client.get("/docs")

    assert response.status_code == 200
    assert b'url: "/openapi.yaml"' in response.data


def test_spec_documents_exactly_the_api_routes(app, spec):
    routes = {
        rule.rule.replace("<", "{").replace(">", "}")
        for rule in app.url_map.iter_rules()
        if rule.endpoint not in DOCS_ENDPOINTS | {"static"}
    }

    assert set(spec["paths"]) == routes


def test_spec_documents_exactly_the_status_codes_the_endpoint_returns(spec):
    error_statuses = {cls.status_code for cls in _all_subclasses(ApiError)}
    documented = {int(code) for code in spec["paths"][PAYMENTS_PATH]["post"]["responses"]}

    assert documented == error_statuses | PAYMENT_STATUSES


def test_spec_payment_schema_matches_the_payment_response(client, factory, spec):
    user = factory.user()
    factory.payment_method(user)
    cart = factory.cart(user, items=[(factory.product("10.00"), 1)])

    response = client.post(f"/carts/{cart.id}/payments", headers={"X-User-Id": str(user.id)})

    schema = spec["components"]["schemas"]["Payment"]
    assert response.status_code == 201
    assert set(response.get_json()) == set(schema["properties"]) == set(schema["required"])
