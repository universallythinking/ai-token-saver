"""Model detection and rough USD savings estimates (input-token list prices).

Detection is deliberately strict. Cursor request bodies embed the user's files
and system prompt, which routinely mention model names ("gpt-4o", "composer").
Scanning loosely for those produced wildly wrong prices, so we only accept:

* an explicit JSON ``model`` field (reverse-proxy / BYOK paths), or
* a protobuf *model descriptor* — ``<tag><len>\\n<namelen><name>`` where the
  outer length is exactly ``namelen + 2``, i.e. a field whose entire payload is
  the model name. Cursor's repeated "available models" menu entries carry
  trailing metadata (context/effort/thinking) and are ignored.

Cursor usually sends ``default`` and resolves the model server-side, so
``pricing.assume_model`` in config.yaml supplies the rate in that case.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Approximate public *input* prices USD / 1M tokens. Matched by substring
# (longest key wins). Used only for dashboard estimates — not billing.
_INPUT_USD_PER_MTOK: list[tuple[str, float]] = [
    # Anthropic (incl. Cursor's internal opus/sonnet/haiku naming)
    ("claude-opus", 15.0),
    ("claude-3-opus", 15.0),
    ("claude-sonnet", 3.0),
    ("claude-3-5-sonnet", 3.0),
    ("claude-3.5-sonnet", 3.0),
    ("claude-haiku", 0.80),
    ("claude-3-haiku", 0.25),
    # OpenAI
    ("gpt-4o-mini", 0.15),
    ("gpt-4o", 2.50),
    ("gpt-4.1-mini", 0.40),
    ("gpt-4.1-nano", 0.10),
    ("gpt-4.1", 2.0),
    ("gpt-4-turbo", 10.0),
    ("gpt-4", 30.0),
    ("gpt-5-mini", 0.25),
    ("gpt-5", 1.25),
    ("o1-mini", 1.10),
    ("o1", 15.0),
    ("o3-mini", 1.10),
    ("o3", 10.0),
    ("o4-mini", 1.10),
    # Google
    ("gemini-2.5-pro", 1.25),
    ("gemini-2.0-flash", 0.10),
    ("gemini-1.5-pro", 1.25),
    ("gemini-flash", 0.10),
    ("gemini-pro", 1.25),
    # Cursor
    ("composer", 3.0),
    ("cursor-small", 0.15),
    ("cursor-fast", 0.15),
]

# Used when nothing is detected and no assume_model is configured.
FALLBACK_USD_PER_MTOK = 3.0

# Server-routed placeholders — real model is unknown to us.
PLACEHOLDER_MODELS = {"default", "auto", "undefined", "null", "none", ""}

# Set from config (pricing.assume_model) so placeholder traffic can be priced.
_ASSUMED_MODEL = ""


def set_assumed_model(model: str | None) -> None:
    """Record the user's configured model for pricing placeholder traffic."""
    global _ASSUMED_MODEL
    _ASSUMED_MODEL = (model or "").strip()


def assumed_model() -> str:
    return _ASSUMED_MODEL


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
    rb'["\']\s*:\s*["\']([^"\']{1,120})["\']'
)

# A model id must look like a real id: family + version, or a known short name.
_STRICT_MODEL_RE = re.compile(
    r"^(?:"
    r"default|auto"
    r"|claude-[a-z]+-\d[\w.\-]*"          # claude-opus-5-5, claude-sonnet-4-20250514
    r"|claude-\d[\w.]*-[a-z]+[\w.\-]*"    # claude-3-5-sonnet, claude-4-opus
    r"|gpt-\d[\w.\-]*"                    # gpt-4o, gpt-5-mini
    r"|o[1345](?:-(?:mini|pro))?"         # o1, o3-mini, o4-mini
    r"|gemini-\d[\w.\-]*"                 # gemini-2.5-pro
    r"|composer-\d+"                      # composer-1
    r"|cursor-(?:small|fast|best)"
    r")$",
    re.IGNORECASE,
)

# Protobuf: \n <namelen> <ascii name>
_PB_NAME_RE = re.compile(rb"\x0a([\x02-\x40])([ -~]{2,64})")


def normalize_model(name: str) -> str:
    name = (name or "").strip().strip('"').strip("'")
    if not name or " " in name or len(name) > 100:
        return "unknown"
    if name.lower() in PLACEHOLDER_MODELS:
        return "default"
    if not _STRICT_MODEL_RE.match(name):
        return "unknown"
    return name[:100]


def is_priceable(model: str) -> bool:
    """True when we can attach a real rate to this model id."""
    m = (model or "").lower()
    if not m or m in {"unknown", "default"}:
        return False
    return any(key in m for key in (k for k, _ in _INPUT_USD_PER_MTOK))


def rate_usd_per_mtok(model: str) -> float:
    """Best-match input USD per 1M tokens for a model id."""
    m = (model or "").lower()
    if m in {"", "unknown", "default"}:
        m = _ASSUMED_MODEL.lower()
    if not m:
        return FALLBACK_USD_PER_MTOK
    best_key = ""
    best_rate = FALLBACK_USD_PER_MTOK
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


def max_rate_usd_per_mtok(
    by_model: dict[str, dict[str, int]] | None = None,
) -> tuple[float, str]:
    """Upper-bound input $/MTok: the most expensive model among traffic + config.

    Historical / mislabeled cheap models are kept in by_model for the pie, but
    dollar estimates use the highest applicable rate (plus ``assume_model``).
    Returns ``(rate, model_label)``.
    """
    best_rate = 0.0
    best_label = ""

    def _consider(model: str) -> None:
        nonlocal best_rate, best_label
        if not model:
            return
        rate = rate_usd_per_mtok(model)
        # Only count concrete / configured models, not bare unknown→fallback
        # unless nothing else is available.
        if model.lower() in {"unknown", "default"} and not _ASSUMED_MODEL:
            return
        if rate > best_rate:
            best_rate = rate
            best_label = model

    if _ASSUMED_MODEL:
        _consider(_ASSUMED_MODEL)
    for model, st in (by_model or {}).items():
        tok = int(st.get("tokens_saved") or 0) or int(st.get("tokens_in") or 0)
        if tok <= 0:
            continue
        if is_priceable(model):
            _consider(model)

    if best_rate <= 0:
        return FALLBACK_USD_PER_MTOK, "fallback"
    return best_rate, best_label


def actual_usd_saved(
    by_model: dict[str, dict[str, int]] | None = None,
    *,
    tokens_saved: int | None = None,
) -> tuple[float, float]:
    """Per-model priced savings (actual) and effective blended $/MTok.

    Each model's ``tokens_saved`` is priced at that model's input rate.
    ``unknown`` / ``default`` rows use ``assume_model`` (or the fallback rate).
    Any ``tokens_saved`` not covered by by_model is priced the same way.
    Returns ``(usd, blended_usd_per_mtok)``.
    """
    by_model = by_model or {}
    priced_tok = 0
    usd = 0.0
    for model, st in by_model.items():
        tok = max(0, int(st.get("tokens_saved") or 0))
        if tok <= 0:
            continue
        usd += estimate_usd(tok, model)
        priced_tok += tok

    target = int(tokens_saved) if tokens_saved is not None else priced_tok
    remainder = max(0, target - priced_tok)
    if remainder:
        # Unattributed tokens → assume_model / fallback (same as unknown).
        usd += estimate_usd(remainder, "unknown")
        priced_tok += remainder

    if priced_tok <= 0:
        return 0.0, rate_usd_per_mtok("unknown")
    blended = (usd / priced_tok) * 1_000_000.0
    return round(usd, 4), round(blended, 4)


def estimate_usd_saved(
    by_model: dict[str, dict[str, int]] | None = None,
    *,
    tokens_saved: int | None = None,
) -> float:
    """Upper-bound USD saved: all tokens at the most expensive applicable rate."""
    rate, _ = max_rate_usd_per_mtok(by_model)
    if tokens_saved is not None:
        total_tok = max(0, int(tokens_saved))
    else:
        total_tok = sum(int(st.get("tokens_saved", 0)) for st in (by_model or {}).values())
    return round(total_tok * rate / 1_000_000.0, 4)


def _model_from_mapping(obj: dict, depth: int = 0) -> str:
    if depth > 4:
        return "unknown"
    for key in _JSON_MODEL_KEYS:
        m = obj.get(key)
        if isinstance(m, str) and m.strip():
            got = normalize_model(m)
            if got != "unknown":
                return got
    for key in ("body", "request", "params", "data", "message", "config"):
        inner = obj.get(key)
        if isinstance(inner, dict):
            got = _model_from_mapping(inner, depth + 1)
            if got != "unknown":
                return got
    return "unknown"


def extract_model_from_protobuf(data: bytes) -> str:
    """Selected model from a Cursor protobuf body.

    Only accepts descriptors whose entire field payload is the model name
    (outer length == namelen + 2). Repeated "available models" menu entries
    carry trailing metadata and are skipped, as is prompt text.
    """
    selected = ""
    for m in _PB_NAME_RE.finditer(data):
        namelen = m.group(1)[0]
        raw_name = m.group(2)[:namelen]
        if len(raw_name) != namelen:
            continue
        try:
            name = raw_name.decode("ascii")
        except UnicodeDecodeError:
            continue
        got = normalize_model(name)
        if got == "unknown":
            continue
        start = m.start()
        if start < 1 or data[start - 1] != namelen + 2:
            continue  # has trailing metadata → menu entry, not the selection
        # Prefer a concrete model over the "default" placeholder.
        if got != "default":
            return got
        selected = selected or got
    return selected or "unknown"


def extract_model(raw: bytes | None = None, parsed: Any = None) -> str:
    """Best-effort model id from a JSON object and/or raw request body."""
    if isinstance(parsed, dict):
        got = _model_from_mapping(parsed)
        if got != "unknown":
            return got

    if not raw:
        return "unknown"

    looks_json = raw[:1] in (b"{", b"[") or raw.lstrip()[:1] in (b"{", b"[")
    if looks_json:
        try:
            obj = json.loads(raw.decode("utf-8", errors="ignore"))
            if isinstance(obj, dict):
                got = _model_from_mapping(obj)
                if got != "unknown":
                    return got
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            pass
        m = _JSON_MODEL_RE.search(raw[:65536])
        if m:
            got = normalize_model(m.group(1).decode("utf-8", errors="ignore"))
            if got != "unknown":
                return got
        return "unknown"

    return extract_model_from_protobuf(raw)
