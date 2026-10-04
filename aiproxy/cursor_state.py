"""Read Cursor's local UI state for the currently selected model.

Agent/WebSocket requests almost always send model id ``default`` (Auto); the
concrete model is chosen server-side. Cursor does keep a last-applied selection
in its SQLite state DB — we use that as a best-effort hint when the wire only
says ``default``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger("aiproxy.cursor_state")

_CACHE_TTL_S = 5.0
_cached_at = 0.0
_cached_model = ""


def _state_db_candidates() -> list[Path]:
    home = Path.home()
    return [
        home / "Library/Application Support/Cursor/User/globalStorage/state.vscdb",
        home / ".config/Cursor/User/globalStorage/state.vscdb",
        home / "AppData/Roaming/Cursor/User/globalStorage/state.vscdb",
    ]


def _read_item(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT value FROM ItemTable WHERE key = ?", (key,)).fetchone()
    if not row or row[0] is None:
        return None
    val = row[0]
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val)


def _first_selected_model_id(payload: str | None) -> str:
    if not payload:
        return ""
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return ""
    models = obj.get("selectedModels") if isinstance(obj, dict) else None
    if not isinstance(models, list) or not models:
        return ""
    first = models[0]
    if isinstance(first, dict):
        mid = first.get("modelId") or first.get("model") or ""
        return str(mid).strip()
    return ""


def _composer_selected_from_reactive(payload: str | None) -> str:
    if not payload:
        return ""
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return ""
    try:
        cfg = obj["aiSettings"]["modelConfig"]["composer"]
        models = cfg.get("selectedModels") or []
        if models and isinstance(models[0], dict):
            return str(models[0].get("modelId") or cfg.get("modelName") or "").strip()
        return str(cfg.get("modelName") or "").strip()
    except (KeyError, TypeError, IndexError):
        return ""


def read_cursor_selected_model(*, force: bool = False) -> str:
    """Return Cursor's last applied / composer-selected model id, or \"\"."""
    global _cached_at, _cached_model
    now = time.time()
    if not force and _cached_model is not None and (now - _cached_at) < _CACHE_TTL_S:
        return _cached_model

    model = ""
    for db in _state_db_candidates():
        if not db.is_file():
            continue
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        except sqlite3.Error as e:
            log.debug("cursor state open failed (%s): %s", db, e)
            continue
        try:
            # Prefer the last applied picker selection (Agents / model switcher).
            applied = _first_selected_model_id(
                _read_item(con, "cursor/applicationOpenModelAppliedConfig")
            )
            if applied and applied.lower() not in {"default", "auto"}:
                model = applied
                break
            # Fall back to composer feature config (often still "default").
            composer = _composer_selected_from_reactive(
                _read_item(
                    con,
                    "src.vs.platform.reactivestorage.browser.reactiveStorageServiceImpl"
                    ".persistentStorage.applicationUser",
                )
            )
            if composer and composer.lower() not in {"default", "auto"}:
                model = composer
                break
            if applied:
                model = applied
                break
            if composer:
                model = composer
                break
        except sqlite3.Error as e:
            log.debug("cursor state read failed (%s): %s", db, e)
        finally:
            con.close()
        break  # only try the first existing DB

    _cached_at = now
    _cached_model = model
    return model
