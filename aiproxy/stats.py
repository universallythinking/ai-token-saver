from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .pricing import (
    actual_usd_saved,
    assumed_model,
    estimate_usd_saved,
    max_rate_usd_per_mtok,
)

log = logging.getLogger("aiproxy.stats")

# Keep minute buckets long enough that charts survive overnight / weekend gaps.
_BUCKET_RETENTION_S = 14 * 24 * 60 * 60
_RECENT_PERSIST = 200


@dataclass
class RequestEvent:
    ts: float
    host: str
    path: str
    chars_in: int
    chars_out: int
    tokens_in: int
    tokens_out: int
    stripped: bool
    notes: list[str] = field(default_factory=list)
    dry_run: bool = False
    model: str = "unknown"

    @property
    def chars_saved(self) -> int:
        return max(0, self.chars_in - self.chars_out)

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_in - self.tokens_out)


def _empty_model_stats() -> dict[str, int]:
    return {
        "n": 0,
        "stripped": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens_saved": 0,
        "chars_in": 0,
        "chars_out": 0,
        "chars_saved": 0,
    }


def _empty_totals() -> dict[str, int]:
    return {
        "requests": 0,
        "stripped": 0,
        "unchanged": 0,
        "passthrough": 0,
        "ignored": 0,
        "sync": 0,
        "sync_chars": 0,
        "blocked": 0,
        "chars_in": 0,
        "chars_out": 0,
        "chars_saved": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens_saved": 0,
    }


def _empty_bucket() -> dict[str, int]:
    return {
        "chars_in": 0,
        "chars_out": 0,
        "chars_saved": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens_saved": 0,
        "n": 0,
        "stripped": 0,
    }


class StatsStore:
    """Thread-safe cumulative + recent request stats for the dashboard.

    Totals, recent events, and minute buckets are persisted to disk so they
    survive process restarts.
    """

    def __init__(self, persist_path: str | Path | None = None, history_limit: int = 500):
        self._lock = threading.Lock()
        self.persist_path = Path(persist_path).expanduser().resolve() if persist_path else None
        self.history_limit = history_limit
        # Lifetime clock (preserved across launches) + this process session.
        now = time.time()
        self.first_started_at = now
        self.started_at = now
        self.totals = _empty_totals()
        self.recent: deque[RequestEvent] = deque(maxlen=history_limit)
        self.buckets: dict[int, dict[str, int]] = {}
        # Lifetime per-model aggregates for the models pie + cost estimate.
        self.by_model: dict[str, dict[str, int]] = {}
        self._loaded = False
        if self.persist_path and self.persist_path.exists():
            self._load()

    def record_passthrough(
        self,
        kind: str = "passthrough",
        *,
        host: str = "",
        path: str = "",
        chars_in: int = 0,
        reason: str = "",
    ) -> None:
        tokens = max(1, chars_in // 4) if chars_in else 0
        with self._lock:
            self.totals[kind] = self.totals.get(kind, 0) + 1
            if kind == "passthrough":
                self.totals["requests"] += 1
                self.totals["chars_in"] += chars_in
                self.totals["chars_out"] += chars_in
                self.totals["tokens_in"] += tokens
                self.totals["tokens_out"] += tokens
                minute = int(time.time() // 60) * 60
                b = self.buckets.setdefault(minute, _empty_bucket())
                b["chars_in"] += chars_in
                b["chars_out"] += chars_in
                b["tokens_in"] += tokens
                b["tokens_out"] += tokens
                b["n"] += 1
                if host or path or reason:
                    self.recent.appendleft(
                        RequestEvent(
                            ts=time.time(),
                            host=host,
                            path=path,
                            chars_in=chars_in,
                            chars_out=chars_in,
                            tokens_in=tokens,
                            tokens_out=tokens,
                            stripped=False,
                            notes=[reason] if reason else ["passthrough"],
                            dry_run=False,
                        )
                    )
                self._prune_buckets_unlocked(minute)
            self._persist_unlocked()

    def record_ignored(self) -> None:
        """Telemetry / dashboard polls. Forwarded unchanged, omitted from savings."""
        with self._lock:
            self.totals["ignored"] = self.totals.get("ignored", 0) + 1
            self._persist_unlocked()

    def record_sync(self, *, chars_in: int = 0) -> None:
        """BidiAppend / file-sync. Forwarded unchanged; not model-token traffic."""
        with self._lock:
            self.totals["sync"] = self.totals.get("sync", 0) + 1
            self.totals["sync_chars"] = self.totals.get("sync_chars", 0) + max(0, chars_in)
            self._persist_unlocked()

    def record_blocked(self) -> None:
        with self._lock:
            self.totals["blocked"] += 1
            self._persist_unlocked()

    def record(
        self,
        *,
        host: str,
        path: str,
        chars_in: int,
        chars_out: int,
        tokens_in: int,
        tokens_out: int,
        stripped: bool,
        notes: list[str] | None = None,
        dry_run: bool = False,
        model: str = "unknown",
    ) -> RequestEvent:
        model_name = (model or "unknown").strip() or "unknown"
        ev = RequestEvent(
            ts=time.time(),
            host=host,
            path=path,
            chars_in=chars_in,
            chars_out=chars_out,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            stripped=stripped,
            notes=list(notes or []),
            dry_run=dry_run,
            model=model_name,
        )
        with self._lock:
            self.totals["requests"] += 1
            if stripped:
                self.totals["stripped"] += 1
            else:
                self.totals["unchanged"] += 1
            self.totals["chars_in"] += chars_in
            self.totals["chars_out"] += chars_out
            self.totals["chars_saved"] += ev.chars_saved
            self.totals["tokens_in"] += tokens_in
            self.totals["tokens_out"] += tokens_out
            self.totals["tokens_saved"] += ev.tokens_saved

            ms = self.by_model.setdefault(model_name, _empty_model_stats())
            ms["n"] += 1
            if stripped:
                ms["stripped"] += 1
            ms["tokens_in"] += tokens_in
            ms["tokens_out"] += tokens_out
            ms["tokens_saved"] += ev.tokens_saved
            ms["chars_in"] += chars_in
            ms["chars_out"] += chars_out
            ms["chars_saved"] += ev.chars_saved

            minute = int(ev.ts // 60) * 60
            b = self.buckets.setdefault(minute, _empty_bucket())
            b["chars_in"] += chars_in
            b["chars_out"] += chars_out
            b["chars_saved"] += ev.chars_saved
            b["tokens_in"] += tokens_in
            b["tokens_out"] += tokens_out
            b["tokens_saved"] += ev.tokens_saved
            b["n"] += 1
            if stripped:
                b["stripped"] += 1

            self._prune_buckets_unlocked(minute)
            self.recent.appendleft(ev)
            self._persist_unlocked()
        return ev

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            t = dict(self.totals)
            chars_in = t["chars_in"] or 1
            tokens_in = t["tokens_in"] or 1
            save_pct_chars = round(100.0 * t["chars_saved"] / chars_in, 2)
            save_pct_tokens = round(100.0 * t["tokens_saved"] / tokens_in, 2)
            recent = [
                asdict(e) | {"chars_saved": e.chars_saved, "tokens_saved": e.tokens_saved}
                for e in list(self.recent)[:50]
            ]
            series = [{"t": k, **self.buckets[k]} for k in sorted(self.buckets.keys())]
            by_model = {
                name: dict(st)
                for name, st in sorted(
                    self.by_model.items(),
                    key=lambda kv: (-kv[1].get("tokens_in", 0), kv[0]),
                )
            }
            tok_saved = int(t["tokens_saved"])
            # Actual: each model's tokens at that model's rate (unknown → assume_model).
            usd_actual, rate_actual = actual_usd_saved(by_model, tokens_saved=tok_saved)
            # Max: all tokens at the most expensive applicable model.
            rate_max, rate_model = max_rate_usd_per_mtok(by_model)
            usd_max = estimate_usd_saved(by_model, tokens_saved=tok_saved)
            return {
                "started_at": self.started_at,
                "first_started_at": self.first_started_at,
                "now": time.time(),
                "uptime_s": int(time.time() - self.started_at),
                "lifetime_s": int(time.time() - self.first_started_at),
                "persisted": bool(self.persist_path),
                "persist_path": str(self.persist_path) if self.persist_path else None,
                "totals": t,
                "save_pct_chars": save_pct_chars if t["chars_in"] else 0.0,
                "save_pct_tokens": save_pct_tokens if t["tokens_in"] else 0.0,
                "recent": recent,
                "series": series,
                "by_model": by_model,
                "usd_saved_actual": usd_actual,
                "usd_per_mtok_actual": rate_actual,
                "usd_saved_est": usd_max,  # max (legacy key)
                "usd_saved_max": usd_max,
                "usd_per_mtok": round(rate_max, 4),
                "usd_per_mtok_max": round(rate_max, 4),
                "usd_rate_model": rate_model,
                "assumed_model": assumed_model(),
            }

    def reset(self) -> None:
        with self._lock:
            for k in self.totals:
                self.totals[k] = 0
            self.recent.clear()
            self.buckets.clear()
            self.by_model.clear()
            now = time.time()
            self.started_at = now
            self.first_started_at = now
            self._persist_unlocked()

    def _prune_buckets_unlocked(self, minute: int) -> None:
        cutoff = minute - _BUCKET_RETENTION_S
        stale = [k for k in self.buckets if k < cutoff]
        for k in stale:
            del self.buckets[k]

    def _persist_unlocked(self) -> None:
        if not self.persist_path:
            return
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "first_started_at": self.first_started_at,
                "started_at": self.first_started_at,  # legacy key = lifetime start
                "totals": self.totals,
                "recent": [asdict(e) for e in list(self.recent)[:_RECENT_PERSIST]],
                "buckets": {str(k): v for k, v in self.buckets.items()},
                "by_model": self.by_model,
            }
            text = json.dumps(payload, separators=(",", ":"))
            tmp = self.persist_path.with_suffix(self.persist_path.suffix + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.persist_path)
        except OSError as e:
            log.warning("stats persist failed (%s): %s", self.persist_path, e)

    def _load(self) -> None:
        assert self.persist_path is not None
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("stats load failed (%s): %s", self.persist_path, e)
            return

        # Lifetime start: prefer first_started_at, fall back to legacy started_at.
        lifetime = data.get("first_started_at", data.get("started_at"))
        if lifetime:
            try:
                self.first_started_at = float(lifetime)
            except (TypeError, ValueError):
                pass
        # Session uptime always starts now.
        self.started_at = time.time()

        for k, v in (data.get("totals") or {}).items():
            if k in self.totals:
                try:
                    self.totals[k] = int(v)
                except (TypeError, ValueError):
                    continue

        # File stores newest-first (same as list(self.recent)); restore with appendleft
        # from oldest→newest so left side stays newest. Keep historical model labels.
        items = list(data.get("recent") or [])
        restored: list[RequestEvent] = []
        for item in items:
            try:
                restored.append(
                    RequestEvent(
                        ts=float(item["ts"]),
                        host=str(item.get("host", "")),
                        path=str(item.get("path", "")),
                        chars_in=int(item.get("chars_in", 0)),
                        chars_out=int(item.get("chars_out", 0)),
                        tokens_in=int(item.get("tokens_in", 0)),
                        tokens_out=int(item.get("tokens_out", 0)),
                        stripped=bool(item.get("stripped")),
                        notes=list(item.get("notes") or []),
                        dry_run=bool(item.get("dry_run")),
                        model=str(item.get("model") or "unknown"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        self.recent.clear()
        for ev in reversed(restored):
            self.recent.appendleft(ev)

        for k, v in (data.get("buckets") or {}).items():
            try:
                self.buckets[int(k)] = {
                    kk: int(vv) for kk, vv in dict(v).items() if isinstance(vv, (int, float))
                }
            except (TypeError, ValueError):
                continue

        raw_models = data.get("by_model") or {}
        if isinstance(raw_models, dict) and raw_models:
            for name, st in raw_models.items():
                if not isinstance(st, dict):
                    continue
                ms = _empty_model_stats()
                for kk in ms:
                    try:
                        ms[kk] = int(st.get(kk, 0))
                    except (TypeError, ValueError):
                        pass
                self.by_model[str(name)] = ms
        else:
            # Rebuild from recent if older stats.json lacked by_model.
            for ev in self.recent:
                ms = self.by_model.setdefault(ev.model or "unknown", _empty_model_stats())
                ms["n"] += 1
                if ev.stripped:
                    ms["stripped"] += 1
                ms["tokens_in"] += ev.tokens_in
                ms["tokens_out"] += ev.tokens_out
                ms["tokens_saved"] += ev.tokens_saved
                ms["chars_in"] += ev.chars_in
                ms["chars_out"] += ev.chars_out
                ms["chars_saved"] += ev.chars_saved

        self._prune_buckets_unlocked(int(time.time() // 60) * 60)
        self._loaded = True
        log.info(
            "restored stats from %s — %s requests, %s tokens saved, %s models, %s buckets",
            self.persist_path,
            self.totals.get("requests", 0),
            self.totals.get("tokens_saved", 0),
            len(self.by_model),
            len(self.buckets),
        )


_STORE: StatsStore | None = None
_STORE_LOCK = threading.Lock()


def resolve_stats_path(path: str | Path, *, base: Path | None = None) -> Path:
    """Resolve stats path relative to project/config base (not process cwd)."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        root = base or Path(__file__).resolve().parent.parent
        p = root / p
    return p.resolve()


def get_store(persist_path: str | Path | None = None) -> StatsStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            resolved = resolve_stats_path(persist_path) if persist_path else None
            _STORE = StatsStore(persist_path=resolved)
        return _STORE


def init_store(persist_path: str | Path) -> StatsStore:
    global _STORE
    with _STORE_LOCK:
        _STORE = StatsStore(persist_path=resolve_stats_path(persist_path))
        return _STORE
