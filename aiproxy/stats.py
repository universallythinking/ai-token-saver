from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


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

    @property
    def chars_saved(self) -> int:
        return max(0, self.chars_in - self.chars_out)

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_in - self.tokens_out)


class StatsStore:
    """Thread-safe cumulative + recent request stats for the dashboard."""

    def __init__(self, persist_path: str | Path | None = None, history_limit: int = 500):
        self._lock = threading.Lock()
        self.persist_path = Path(persist_path) if persist_path else None
        self.history_limit = history_limit
        self.started_at = time.time()
        self.totals = {
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
        self.recent: deque[RequestEvent] = deque(maxlen=history_limit)
        # Per-minute buckets for sparkline: {minute_epoch: {chars_in, chars_saved, tokens_in, tokens_saved, n}}
        self.buckets: dict[int, dict[str, int]] = {}
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
                # Count bytes toward dashboard totals (previously omitted → always 0).
                self.totals["chars_in"] += chars_in
                self.totals["chars_out"] += chars_in
                self.totals["tokens_in"] += tokens
                self.totals["tokens_out"] += tokens
                minute = int(time.time() // 60) * 60
                b = self.buckets.setdefault(
                    minute,
                    {
                        "chars_in": 0,
                        "chars_out": 0,
                        "chars_saved": 0,
                        "tokens_in": 0,
                        "tokens_out": 0,
                        "tokens_saved": 0,
                        "n": 0,
                        "stripped": 0,
                    },
                )
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
    ) -> RequestEvent:
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

            minute = int(ev.ts // 60) * 60
            b = self.buckets.setdefault(
                minute,
                {
                    "chars_in": 0,
                    "chars_out": 0,
                    "chars_saved": 0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "tokens_saved": 0,
                    "n": 0,
                    "stripped": 0,
                },
            )
            b["chars_in"] += chars_in
            b["chars_out"] += chars_out
            b["chars_saved"] += ev.chars_saved
            b["tokens_in"] += tokens_in
            b["tokens_out"] += tokens_out
            b["tokens_saved"] += ev.tokens_saved
            b["n"] += 1
            if stripped:
                b["stripped"] += 1

            # Keep ~6 hours of minute buckets
            cutoff = minute - 6 * 60 * 60
            for k in [k for k in self.buckets if k < cutoff]:
                del self.buckets[k]

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
            recent = [asdict(e) | {"chars_saved": e.chars_saved, "tokens_saved": e.tokens_saved} for e in list(self.recent)[:50]]
            series = [
                {"t": k, **self.buckets[k]}
                for k in sorted(self.buckets.keys())
            ]
            return {
                "started_at": self.started_at,
                "now": time.time(),
                "uptime_s": int(time.time() - self.started_at),
                "totals": t,
                "save_pct_chars": save_pct_chars if t["chars_in"] else 0.0,
                "save_pct_tokens": save_pct_tokens if t["tokens_in"] else 0.0,
                "recent": recent,
                "series": series,
            }

    def reset(self) -> None:
        with self._lock:
            for k in self.totals:
                self.totals[k] = 0
            self.recent.clear()
            self.buckets.clear()
            self.started_at = time.time()
            self._persist_unlocked()

    def _persist_unlocked(self) -> None:
        if not self.persist_path:
            return
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "started_at": self.started_at,
                "totals": self.totals,
                "recent": [asdict(e) for e in list(self.recent)[:100]],
                "buckets": {str(k): v for k, v in self.buckets.items()},
            }
            self.persist_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass

    def _load(self) -> None:
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
        except (OSError, json.JSONDecodeError):
            return
        self.started_at = float(data.get("started_at") or time.time())
        for k, v in (data.get("totals") or {}).items():
            if k in self.totals:
                self.totals[k] = int(v)
        for item in data.get("recent") or []:
            try:
                self.recent.append(
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
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        for k, v in (data.get("buckets") or {}).items():
            try:
                self.buckets[int(k)] = {kk: int(vv) for kk, vv in v.items()}
            except (TypeError, ValueError):
                continue


_STORE: StatsStore | None = None
_STORE_LOCK = threading.Lock()


def get_store(persist_path: str | Path | None = None) -> StatsStore:
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            _STORE = StatsStore(persist_path=persist_path)
        return _STORE


def init_store(persist_path: str | Path) -> StatsStore:
    global _STORE
    with _STORE_LOCK:
        _STORE = StatsStore(persist_path=persist_path)
        return _STORE
