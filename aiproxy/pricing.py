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
    # xAI (Cursor catalog)
    ("grok-4.7", 3.0),
    ("grok-4.6", 3.0),
    ("grok-4.5", 3.0),
    ("grok-4", 3.0),
    ("grok-code", 0.20),
    ("grok", 3.0),
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
    r"|grok-[\w.\-]+"                     # grok-4.7, grok-code-fast-1
    r"|composer-[\w.\-]+"                 # composer-1, composer-2.5
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


def _lookup_rate(model: str) -> float:
    """Substring match against the price table (no placeholder substitution)."""
    m = (model or "").lower()
    if not m:
        return FALLBACK_USD_PER_MTOK
    best_key = ""
    best_rate = FALLBACK_USD_PER_MTOK
    for key, rate in _INPUT_USD_PER_MTOK:
        if key in m and len(key) > len(best_key):
            best_key = key
            best_rate = rate
    return best_rate


def rate_usd_per_mtok(model: str) -> float:
    """Best-match input USD per 1M tokens (placeholders → assume_model)."""
    m = (model or "").lower()
    if m in {"", "unknown", "default"}:
        return _lookup_rate(_ASSUMED_MODEL) if _ASSUMED_MODEL else FALLBACK_USD_PER_MTOK
    return _lookup_rate(m)


def rate_usd_per_mtok_actual(model: str) -> float:
    """Rate for the *actual* estimate.

    - concrete model id → that model's list price
    - ``default`` (Cursor placeholder) → ``assume_model`` (what you said you use)
    - ``unknown`` (unidentified) → mid-tier fallback, not the max assume
    """
    m = (model or "").lower()
    if m in {"", "unknown"}:
        return FALLBACK_USD_PER_MTOK
    if m == "default":
        return _lookup_rate(_ASSUMED_MODEL) if _ASSUMED_MODEL else FALLBACK_USD_PER_MTOK
    return _lookup_rate(m)


def estimate_usd(tokens: int, model: str) -> float:
    """Estimate USD for a token count at the model's input list price."""
    if tokens <= 0:
        return 0.0
    return tokens * rate_usd_per_mtok(model) / 1_000_000.0


def estimate_usd_actual(tokens: int, model: str) -> float:
    if tokens <= 0:
        return 0.0
    return tokens * rate_usd_per_mtok_actual(model) / 1_000_000.0


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

    Concrete models use their list price; Cursor ``default`` uses assume_model;
    true ``unknown`` uses the mid-tier fallback (not the max). Unattributed
    remainder is treated as unknown.
    Returns ``(usd, blended_usd_per_mtok)``.
    """
    by_model = by_model or {}
    priced_tok = 0
    usd = 0.0
    for model, st in by_model.items():
        tok = max(0, int(st.get("tokens_saved") or 0))
        if tok <= 0:
            continue
        usd += estimate_usd_actual(tok, model)
        priced_tok += tok

    target = int(tokens_saved) if tokens_saved is not None else priced_tok
    remainder = max(0, target - priced_tok)
    if remainder:
        usd += estimate_usd_actual(remainder, "unknown")
        priced_tok += remainder

    if priced_tok <= 0:
        return 0.0, FALLBACK_USD_PER_MTOK
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


# Server streams sometimes name the routed model explicitly (esp. when
# "Underlying model: Displayed" is on, or in provider metadata).
_SERVER_MODEL_RE = re.compile(
    rb"(?:model(?:Name|Id|_name|_id)?|served[_-]?model|routed[_-]?model|"
    rb"underlying[_-]?model|cursor)\s*[\"':=\s]+[\"']?("
    rb"claude-[\w.\-]+|gpt-[\w.\-]+|o[1345](?:-[\w.\-]+)?|"
    rb"gemini-[\w.\-]+|grok-[\w.\-]+|composer-[\w.\-]+|cursor-[\w.\-]+"
    rb")",
    re.IGNORECASE,
)


def extract_model_from_server_bytes(data: bytes | None) -> str:
    """Best-effort routed model id from a server→client agent frame.

    Auto/default requests hide the model on the way out; when Cursor includes
    it in the response stream (or metadata), pick it up from the first few KB
    only so we don't match echoed request menus deep in the payload.
    """
    if not data:
        return "unknown"
    sample = data[:16384]

    # JSON / text annotations first (more specific than bare descriptors).
    m = _SERVER_MODEL_RE.search(sample)
    if m:
        got = normalize_model(m.group(1).decode("utf-8", errors="ignore"))
        if got not in {"unknown", "default"}:
            return got

    m = _JSON_MODEL_RE.search(sample)
    if m:
        got = normalize_model(m.group(1).decode("utf-8", errors="ignore"))
        if got not in {"unknown", "default"}:
            return got

    # Exact protobuf name fields near the start of the response.
    for m in _PB_NAME_RE.finditer(sample):
        namelen = m.group(1)[0]
        raw_name = m.group(2)[:namelen]
        if len(raw_name) != namelen:
            continue
        try:
            name = raw_name.decode("ascii")
        except UnicodeDecodeError:
            continue
        got = normalize_model(name)
        if got in {"unknown", "default"}:
            continue
        start = m.start()
        if start >= 1 and sample[start - 1] == namelen + 2:
            return got
        # Also accept early bare name runs that look like model ids (no menu
        # metadata) when they appear in the first 2KB of a server frame.
        if start < 2048 and is_priceable(got):
            return got

    return "unknown"


def _apply_cursor_ui_hint(got: str) -> str:
    """When the wire says default/unknown, prefer Cursor's last UI selection."""
    if got not in {"unknown", "default"}:
        return got
    try:
        from .cursor_state import read_cursor_selected_model

        hint = normalize_model(read_cursor_selected_model())
    except Exception:  # noqa: BLE001
        return got
    if hint not in {"unknown", "default", ""}:
        return hint
    return got


def extract_model(raw: bytes | None = None, parsed: Any = None) -> str:
    """Best-effort model id from a JSON object and/or raw request body.

    Cursor Agent traffic usually embeds ``default`` (Auto). In that case we
    fall back to Cursor's local UI state (last applied model), then leave
    ``default`` for pricing via ``assume_model``.
    """
    got = "unknown"
    if isinstance(parsed, dict):
        got = _model_from_mapping(parsed)
        if got not in {"unknown", "default"}:
            return got

    if raw:
        looks_json = raw[:1] in (b"{", b"[") or raw.lstrip()[:1] in (b"{", b"[")
        if looks_json:
            try:
                obj = json.loads(raw.decode("utf-8", errors="ignore"))
                if isinstance(obj, dict):
                    got = _model_from_mapping(obj)
                    if got not in {"unknown", "default"}:
                        return got
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                pass
            m = _JSON_MODEL_RE.search(raw[:65536])
            if m:
                cand = normalize_model(m.group(1).decode("utf-8", errors="ignore"))
                if cand not in {"unknown", "default"}:
                    return cand
                if got == "unknown":
                    got = cand
        else:
            pb = extract_model_from_protobuf(raw)
            if pb not in {"unknown", "default"}:
                return pb
            if got == "unknown":
                got = pb

    return _apply_cursor_ui_hint(got)
