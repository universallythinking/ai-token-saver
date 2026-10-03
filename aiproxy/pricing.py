"""Model detection and rough USD savings estimates (input-token list prices)."""

from __future__ import annotations

import json
import re
from typing import Any

# Approximate public *input* prices USD / 1M tokens. Matched by substring
# (longest key wins). Used only for dashboard estimates — not billing.
_INPUT_USD_PER_MTOK: list[tuple[str, float]] = [
    ("claude-opus-4", 15.0),
    ("claude-4-opus", 15.0),
    ("claude-opus", 15.0),
    ("claude-sonnet-4", 3.0),
    ("claude-4-sonnet", 3.0),
    ("claude-3-5-sonnet", 3.0),
    ("claude-3.5-sonnet", 3.0),
    ("claude-sonnet", 3.0),
    ("claude-3-haiku", 0.25),
    ("claude-haiku", 0.80),
    ("claude-3-opus", 15.0),
    ("gpt-4o-mini", 0.15),
    ("gpt-4.1-mini", 0.40),
    ("gpt-4.1-nano", 0.10),
    ("gpt-4.1", 2.0),
    ("gpt-4o", 2.50),
    ("gpt-4-turbo", 10.0),
    ("gpt-4", 30.0),
    ("gpt-5-mini", 0.25),
    ("gpt-5", 1.25),
    ("o3-mini", 1.10),
    ("o3", 10.0),
    ("o1-mini", 1.10),
    ("o1", 15.0),
    ("o4-mini", 1.10),
    ("gemini-2.5-pro", 1.25),
    ("gemini-2.0-flash", 0.10),
    ("gemini-1.5-pro", 1.25),
    ("gemini-flash", 0.10),
    ("gemini-pro", 1.25),
    ("composer-1", 3.0),
    ("composer", 3.0),
    ("cursor-small", 0.15),
    ("cursor-fast", 0.15),
]

# Cursor agent traffic is often Sonnet-class when the model id is missing.
_DEFAULT_USD_PER_MTOK = 3.0

_JSON_MODEL_KEYS = (
    "model",
    "model_name",
    "modelName",
    "modelId",
    "model_id",
    "llmModel",
    "llm_model",
)

_JSON_MODEL_RE = re.compile(
    rb'["\'](?:model|model_name|modelName|modelId|model_id|llmModel|llm_model)'
    rb'["\']\s*:\s*["\']([^"\']{1,120})["\']',
    re.IGNORECASE,
)

# Common provider / Cursor model id shapes in JSON or protobuf text.
_BARE_MODEL_RE = re.compile(
    rb"(?:"
    rb"gpt-4o(?:-mini)?|gpt-4\.1(?:-mini|-nano)?|gpt-4-turbo|gpt-4|gpt-5(?:-mini)?|"
    rb"o[134](?:-mini|-pro)?|"
    rb"claude-(?:3(?:\.[57])?-|4-|opus-|sonnet-|haiku-)[a-z0-9._+\-]{0,48}|"
    rb"claude-(?:opus|sonnet|haiku)-\d[a-z0-9._+\-]{0,48}|"
    rb"anthropic\.claude[a-z0-9._+\-]{0,48}|"
    rb"gemini-[\w.+\-]{2,48}|"
    rb"composer(?:-[\w.+\-]+)?|"
    rb"cursor-(?:small|fast|best)(?:-[\w.+\-]+)?"
    rb")",
    re.IGNORECASE,
)

# Printable runs that look like model ids (protobuf often embeds these).
_ASCII_MODEL_RUN = re.compile(
    rb"(?:claude|gpt|o[134]|gemini|composer|cursor|anthropic)[\w.+\-]{2,60}",
    re.IGNORECASE,
)


def normalize_model(name: str) -> str:
    name = (name or "").strip().strip('"').strip("'")
    if not name:
        return "unknown"
    # Allow provider paths like anthropic/claude-sonnet-4
    if " " in name or len(name) > 100:
        return "unknown"
    if name.lower() in {"default", "auto", "undefined", "null", "none"}:
        return "unknown"
    return name[:100]


def rate_usd_per_mtok(model: str) -> float:
    """Best-match input USD per 1M tokens for a model id."""
    m = (model or "").lower()
    if not m or m == "unknown":
        return _DEFAULT_USD_PER_MTOK
    best_key = ""
    best_rate = _DEFAULT_USD_PER_MTOK
    for key, rate in _INPUT_USD_PER_MTOK:
        if key in m and len(key) > len(best_key):
            best_key = key
            best_rate = rate
    return best_rate


def estimate_usd(tokens: int, model: str) -> float:
    """Estimate USD for a token count at the model's input list price."""
    if tokens <= 0:
        return 0.0
    return tokens * rate_usd_per_mtok(model) / 1_000_000.0


def blended_rate_usd_per_mtok(by_model: dict[str, dict[str, int]] | None) -> float:
    """Token-weighted average input $/MTok from per-model stats."""
    total_tok = 0
    weighted = 0.0
    for model, st in (by_model or {}).items():
        # Prefer tokens_saved for the blend used in savings; fall back to tokens_in.
        tok = int(st.get("tokens_saved") or 0) or int(st.get("tokens_in") or 0)
        if tok <= 0:
            continue
        total_tok += tok
        weighted += tok * rate_usd_per_mtok(model)
    if total_tok <= 0:
        return _DEFAULT_USD_PER_MTOK
    return weighted / total_tok


def estimate_usd_saved(
    by_model: dict[str, dict[str, int]] | None = None,
    *,
    tokens_saved: int | None = None,
) -> float:
    """Estimate USD saved.

    Prefer ``tokens_saved × blended rate`` so totals stay aligned with the
    dashboard token counters (by_model alone can undercount).
    """
    if tokens_saved is not None:
        rate = blended_rate_usd_per_mtok(by_model)
        return round(max(0, int(tokens_saved)) * rate / 1_000_000.0, 4)

    total = 0.0
    for model, st in (by_model or {}).items():
        total += estimate_usd(int(st.get("tokens_saved", 0)), model)
    return round(total, 4)


def _model_from_mapping(obj: dict) -> str:
    for key in _JSON_MODEL_KEYS:
        m = obj.get(key)
        if isinstance(m, str) and m.strip():
            got = normalize_model(m)
            if got != "unknown":
                return got
    for key in ("body", "request", "params", "data", "message", "config"):
        inner = obj.get(key)
        if isinstance(inner, dict):
            got = _model_from_mapping(inner)
            if got != "unknown":
                return got
    return "unknown"


def _model_from_ascii_runs(sample: bytes) -> str:
    """Pick the most specific model-like ASCII run in a binary blob."""
    best = "unknown"
    best_score = -1
    for m in _ASCII_MODEL_RUN.finditer(sample):
        try:
            cand = normalize_model(m.group(0).decode("ascii", errors="ignore"))
        except Exception:  # noqa: BLE001
            continue
        if cand == "unknown":
            continue
        # Prefer priced/specific ids, then longer names.
        priced = 1 if rate_usd_per_mtok(cand) != _DEFAULT_USD_PER_MTOK else 0
        score = priced * 1000 + len(cand)
        if score > best_score:
            best = cand
            best_score = score
    return best


def extract_model(raw: bytes | None = None, parsed: Any = None) -> str:
    """Best-effort model id from a JSON object and/or raw request body."""
    if isinstance(parsed, dict):
        got = _model_from_mapping(parsed)
        if got != "unknown":
            return got

    if not raw:
        return "unknown"

    # Model ids are usually near the start; scan a generous prefix.
    sample = raw[:262144]

    try:
        text = sample.decode("utf-8", errors="ignore")
        stripped = text.lstrip()
        if stripped.startswith("{"):
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                got = _model_from_mapping(obj)
                if got != "unknown":
                    return got
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        pass

    m = _JSON_MODEL_RE.search(sample)
    if m:
        try:
            got = normalize_model(m.group(1).decode("utf-8", errors="ignore"))
            if got != "unknown":
                return got
        except Exception:  # noqa: BLE001
            pass

    m = _BARE_MODEL_RE.search(sample)
    if m:
        try:
            got = normalize_model(m.group(0).decode("utf-8", errors="ignore"))
            if got != "unknown":
                return got
        except Exception:  # noqa: BLE001
            pass

    got = _model_from_ascii_runs(sample)
    if got != "unknown":
        return got

    return "unknown"
