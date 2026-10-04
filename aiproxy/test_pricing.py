from __future__ import annotations

import json

from aiproxy.pricing import (
    actual_usd_saved,
    estimate_usd,
    estimate_usd_saved,
    extract_model,
    extract_model_from_protobuf,
    max_rate_usd_per_mtok,
    rate_usd_per_mtok,
    set_assumed_model,
)


def test_extract_model_from_json():
    body = {"model": "gpt-4o-mini", "messages": []}
    raw = json.dumps(body).encode()
    assert extract_model(raw, body) == "gpt-4o-mini"
    assert extract_model(raw) == "gpt-4o-mini"


def test_extract_model_from_protobuf_descriptor():
    name = b"claude-opus-5-5"
    field = b"\x0a" + bytes([len(name)]) + name
    raw = bytes([len(field)]) + field
    assert extract_model_from_protobuf(raw) == "claude-opus-5-5"


def test_extract_ignores_menu_entries_with_metadata():
    name = b"claude-opus-5-5"
    inner = b"\x0a" + bytes([len(name)]) + name + b"\x1a\x0f\n\x07context\x12\x04300k"
    raw = bytes([len(inner)]) + inner
    assert extract_model_from_protobuf(raw) == "unknown"


def test_extract_model_nested_key():
    body = {"request": {"modelName": "claude-opus-5-thinking"}}
    got = extract_model(None, body)
    assert got.startswith("claude-opus-5")


def test_actual_vs_max_savings():
    set_assumed_model("claude-opus-5")
    by = {
        "gpt-4o-mini": {"tokens_saved": 1_000_000},  # $0.15
        "default": {"tokens_saved": 1_000_000},  # assume opus → $15
        "unknown": {"tokens_saved": 1_000_000},  # fallback mid-tier → $3
    }
    actual, blended = actual_usd_saved(by, tokens_saved=3_000_000)
    assert abs(actual - (0.15 + 15.0 + 3.0)) < 1e-6
    assert blended > 0.15 and blended < 15.0

    rate_max, label = max_rate_usd_per_mtok(by)
    assert abs(rate_max - 15.0) < 1e-9
    assert "opus" in label.lower()
    mx = estimate_usd_saved(by, tokens_saved=3_000_000)
    assert abs(mx - 45.0) < 1e-9  # all 3M @ $15
    assert mx > actual


def test_rate_and_estimate():
    set_assumed_model("")
    assert rate_usd_per_mtok("gpt-4o-mini") < rate_usd_per_mtok("claude-opus-4")
    assert abs(estimate_usd(1_000_000, "gpt-4o") - 2.50) < 1e-9


if __name__ == "__main__":
    test_extract_model_from_json()
    test_extract_model_from_protobuf_descriptor()
    test_extract_ignores_menu_entries_with_metadata()
    test_extract_model_nested_key()
    test_actual_vs_max_savings()
    test_rate_and_estimate()
    print("ok")
