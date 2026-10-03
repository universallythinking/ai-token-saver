from __future__ import annotations

import json

from aiproxy.pricing import (
    blended_rate_usd_per_mtok,
    estimate_usd,
    estimate_usd_saved,
    extract_model,
    rate_usd_per_mtok,
)


def test_extract_model_from_json():
    body = {"model": "gpt-4o-mini", "messages": []}
    raw = json.dumps(body).encode()
    assert extract_model(raw, body) == "gpt-4o-mini"
    assert extract_model(raw) == "gpt-4o-mini"


def test_extract_model_from_protobufish_blob():
    raw = b"\x0a\x10padding" + b"claude-sonnet-4-20250514" + b"\x00more"
    assert "claude-sonnet-4" in extract_model(raw)


def test_extract_model_nested_key():
    body = {"request": {"modelName": "claude-4-opus-thinking"}}
    assert "opus" in extract_model(None, body).lower()


def test_rate_and_estimate():
    assert rate_usd_per_mtok("gpt-4o-mini") < rate_usd_per_mtok("claude-opus-4")
    # 1M tokens at $2.50 / MTok → $2.50
    assert abs(estimate_usd(1_000_000, "gpt-4o") - 2.50) < 1e-9
    saved = estimate_usd_saved(
        {
            "gpt-4o": {"tokens_saved": 1_000_000},
            "unknown": {"tokens_saved": 0},
        }
    )
    assert abs(saved - 2.50) < 1e-9
    # Align dollars to the Tokens saved counter even if by_model is sparse.
    aligned = estimate_usd_saved({"unknown": {"tokens_saved": 1}}, tokens_saved=1_000_000)
    assert abs(aligned - 3.0) < 1e-9
    assert abs(blended_rate_usd_per_mtok({"claude-opus-4": {"tokens_saved": 1}}) - 15.0) < 1e-9


if __name__ == "__main__":
    test_extract_model_from_json()
    test_extract_model_from_protobufish_blob()
    test_rate_and_estimate()
    print("ok")
