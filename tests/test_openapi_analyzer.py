from __future__ import annotations

import json
import yaml
import pytest
from phases.openapi_analyzer import analyze, APIChangeItem


OLD_SPEC = {
    "openapi": "3.0.0",
    "paths": {
        "/orders": {
            "get": {
                "operationId": "listOrders",
                "parameters": [{"name": "limit", "in": "query", "required": False, "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "OK"}},
            },
            "post": {
                "operationId": "createOrder",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {
                        "type": "object",
                        "required": ["amount", "user_id"],
                        "properties": {
                            "amount":  {"type": "string"},
                            "user_id": {"type": "integer"},
                        },
                    }}},
                },
                "responses": {"201": {"description": "Created"}},
            },
        },
        "/orders/{id}": {
            "delete": {
                "operationId": "deleteOrder",
                "responses": {"204": {"description": "No Content"}},
            },
        },
    },
}


def _yaml(spec: dict) -> str:
    return yaml.dump(spec, default_flow_style=False)


def _json(spec: dict) -> str:
    return json.dumps(spec)


def _change(old: dict, new: dict) -> list[APIChangeItem]:
    return analyze(_yaml(old), _yaml(new), "openapi.yaml")


# ── Removed endpoint ─────────────────────────────────────────────────────────

def test_detects_removed_endpoint():
    new = {k: v for k, v in OLD_SPEC["paths"].items() if k != "/orders/{id}"}
    items = _change(OLD_SPEC, {**OLD_SPEC, "paths": new})
    types = [i.change_type for i in items]
    assert any("removed_endpoint" in t for t in types)
    assert any(i.risk == "high" for i in items)


def test_detects_removed_method():
    import copy
    new_spec = copy.deepcopy(OLD_SPEC)
    del new_spec["paths"]["/orders"]["post"]
    items = _change(OLD_SPEC, new_spec)
    assert any(i.risk == "high" for i in items)


# ── Field type changed ────────────────────────────────────────────────────────

def test_detects_changed_field_type():
    import copy
    new_spec = copy.deepcopy(OLD_SPEC)
    new_spec["paths"]["/orders"]["post"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["amount"]["type"] = "number"
    items = _change(OLD_SPEC, new_spec)
    assert any(i.change_type == "changed_field_type" for i in items)
    assert any(i.risk == "high" for i in items)


# ── New required field ────────────────────────────────────────────────────────

def test_detects_new_required_field():
    import copy
    new_spec = copy.deepcopy(OLD_SPEC)
    schema = new_spec["paths"]["/orders"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    schema["required"].append("currency")
    schema["properties"]["currency"] = {"type": "string"}
    items = _change(OLD_SPEC, new_spec)
    assert any(i.change_type == "added_required_field" for i in items)
    assert any(i.risk == "high" for i in items)


# ── Safe changes are low risk ─────────────────────────────────────────────────

def test_new_optional_field_is_low_risk():
    import copy
    new_spec = copy.deepcopy(OLD_SPEC)
    schema = new_spec["paths"]["/orders"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    schema["properties"]["note"] = {"type": "string"}
    items = _change(OLD_SPEC, new_spec)
    # Only low-risk items (new optional field)
    if items:
        assert all(i.risk in ("low",) for i in items)


def test_new_endpoint_is_low_risk():
    import copy
    new_spec = copy.deepcopy(OLD_SPEC)
    new_spec["paths"]["/invoices"] = {"get": {"operationId": "listInvoices", "responses": {"200": {"description": "OK"}}}}
    items = _change(OLD_SPEC, new_spec)
    if items:
        assert all(i.risk == "low" for i in items)


# ── Format support ────────────────────────────────────────────────────────────

def test_handles_json_format():
    import copy
    new_spec = copy.deepcopy(OLD_SPEC)
    del new_spec["paths"]["/orders/{id}"]
    items = analyze(_json(OLD_SPEC), _json(new_spec), "openapi.json")
    assert any(i.risk == "high" for i in items)


def test_returns_empty_for_identical_specs():
    items = _change(OLD_SPEC, OLD_SPEC)
    assert items == []
