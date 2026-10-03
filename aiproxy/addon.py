from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from mitmproxy import ctx, http

from .config import Config, host_matches
from .connect_strip import (
    path_is_noise,
    path_is_sync,
    path_should_strip_connect,
    try_strip_connect_bytes,
)
from .stats import StatsStore, get_store, resolve_stats_path
from .stripper import estimate_tokens, try_strip_bytes

log = logging.getLogger("aiproxy.addon")


def _should_block(host: str, config: Config) -> bool:
    if config.block_hosts and host_matches(host, config.block_hosts):
        return True
    h = host.lower()
    for suf in config.block_host_suffixes:
        s = suf.lower().lstrip("*").lstrip(".")
        if not s:
            continue
        if h == s or h.endswith("." + s):
            return True
    return False


def _is_streaming_content_type(content_type: str) -> bool:
    ct = (content_type or "").lower()
    return any(
        marker in ct
        for marker in (
            "text/event-stream",
            "connect",
            "protobuf",
            "proto",
            "grpc",
            "application/x-ndjson",
            "application/jsonl",
        )
    )


def _content_length(flow: http.HTTPFlow) -> int:
    try:
        return int(flow.request.headers.get("content-length") or 0)
    except ValueError:
        return 0


class CursorStripAddon:
    def __init__(self, config: Config, store: StatsStore | None = None):
        self.config = config
        self.store = store or get_store(resolve_stats_path(config.stats_path))
        self.dump_dir = Path(config.logging.dump_dir)
        self.dump_dir.mkdir(parents=True, exist_ok=True)

    def load(self, loader) -> None:  # noqa: ANN001
        ctx.log.info(
            f"aiproxy loaded — intercepting {len(self.config.intercept_hosts)} "
            f"host patterns; dashboard :{self.config.dashboard_port}"
        )

    def requestheaders(self, flow: http.HTTPFlow) -> None:
        """
        Always buffer intercept-host request bodies so we can count + strip.
        (Streaming requests made bodies invisible → 0 tokens / 0 stripped.)
        Responses still stream — see responseheaders.
        """
        host = flow.request.pretty_host or ""
        if host_matches(host, self.config.intercept_hosts):
            flow.request.stream = False

    def responseheaders(self, flow: http.HTTPFlow) -> None:
        """
        Forward SSE / Connect / chunked responses immediately.

        mitmproxy buffers bodies below stream_large_bodies by default; that stalls
        Cursor chat replies (small streaming chunks never reach the client).
        """
        if flow.response is None:
            return
        host = flow.request.pretty_host or ""
        ct = flow.response.headers.get("content-type") or ""
        te = (flow.response.headers.get("transfer-encoding") or "").lower()
        if (
            _is_streaming_content_type(ct)
            or "chunked" in te
            or host_matches(host, self.config.intercept_hosts)
        ):
            flow.response.stream = True

    def tls_failed_client(self, data) -> None:  # noqa: ANN001
        ctx.log.warn(
            f"client TLS failed for {getattr(data, 'context', data)} — "
            "trust mitmproxy CA + set NODE_EXTRA_CA_CERTS when launching Cursor"
        )

    def tls_failed_server(self, data) -> None:  # noqa: ANN001
        ctx.log.warn(f"server TLS failed: {getattr(data, 'context', data)}")

    def request(self, flow: http.HTTPFlow) -> None:
        host = flow.request.pretty_host or ""
        path = flow.request.path or ""

        if _should_block(host, self.config):
            self.store.record_blocked()
            flow.response = http.Response.make(204, b"", {"Content-Type": "text/plain"})
            ctx.log.info(f"blocked {host}{path}")
            return

        if flow.request.method.upper() not in {"POST", "PUT", "PATCH"}:
            return

        if not host_matches(host, self.config.intercept_hosts):
            return

        # Analytics and dashboard polls are not model prompts.
        if path_is_noise(path):
            self.store.record_ignored()
            return

        # BidiAppend / file-sync: forwarding only. Rewriting breaks replies and
        # these bytes are not the model prompt (don't count as requested tokens).
        if path_is_sync(path):
            try:
                raw = flow.request.raw_content if flow.request.raw_content is not None else b""
                if not raw:
                    raw = flow.request.content or b""
            except Exception:
                raw = b""
            self.store.record_sync(chars_in=len(raw) or _content_length(flow))
            return

        # If something re-enabled streaming, count via Content-Length at least.
        if getattr(flow.request, "stream", False):
            cl = _content_length(flow)
            self.store.record_passthrough(
                "passthrough",
                host=host,
                path=path,
                chars_in=cl,
                reason="streamed",
            )
            return

        content_type = (flow.request.headers.get("content-type") or "").lower()
        try:
            raw = flow.request.raw_content if flow.request.raw_content is not None else b""
            if not raw:
                raw = flow.request.content or b""
        except Exception:
            raw = flow.request.raw_content or b""

        cl = _content_length(flow)
        if not raw:
            self.store.record_passthrough(
                "passthrough",
                host=host,
                path=path,
                chars_in=cl,
                reason="empty-body" if cl == 0 else f"missing-body(cl={cl})",
            )
            ctx.log.warn(
                f"passthrough empty/missing body {host}{path} "
                f"ct={content_type or '-'} content-length={cl}"
            )
            return

        # --- Cursor Connect / protobuf ---
        looks_connect = path_should_strip_connect(path) or any(
            m in content_type
            for m in ("connect", "protobuf", "proto", "grpc")
        )
        if looks_connect and "json" in content_type and "connect" not in content_type:
            looks_connect = False

        if looks_connect:
            new_raw, result = try_strip_connect_bytes(
                raw, self.config.strip, content_type=content_type, path=path
            )
            if result is None:
                self.store.record_passthrough(
                    "passthrough",
                    host=host,
                    path=path,
                    chars_in=len(raw),
                    reason="connect-unhandled",
                )
                ctx.log.info(
                    f"passthrough connect-unhandled {host}{path} "
                    f"({len(raw)} bytes, ct={content_type or '-'})"
                )
                return
            ctx.log.info(
                f"connect-strip {host}{path}: {len(raw)} bytes ct={content_type or '-'} "
                f"changed={result.changed} notes={result.notes}"
            )
            self._record_and_apply(flow, host, path, raw, new_raw, result)
            return

        # --- OpenAI / Anthropic-style JSON (also marketplace etc.) ---
        looks_json = (
            "json" in content_type
            or raw.lstrip().startswith(b"{")
            or raw.lstrip().startswith(b"[")
        )
        if not looks_json:
            self.store.record_passthrough(
                "passthrough",
                host=host,
                path=path,
                chars_in=len(raw),
                reason="non-json",
            )
            return

        new_raw, result = try_strip_bytes(raw, self.config.strip)
        if result is None:
            self.store.record_passthrough(
                "passthrough",
                host=host,
                path=path,
                chars_in=len(raw),
                reason="unparseable",
            )
            return

        self._record_and_apply(flow, host, path, raw, new_raw, result)

    def websocket_message(self, flow: http.HTTPFlow) -> None:
        """Strip client→server agent WebSocket frames (/agent/v1/run etc.)."""
        if flow.websocket is None or not flow.websocket.messages:
            return
        message = flow.websocket.messages[-1]
        if not message.from_client or message.dropped:
            return

        host = flow.request.pretty_host or ""
        path = flow.request.path or ""
        if not host_matches(host, self.config.intercept_hosts):
            return
        if not path_should_strip_connect(path) and "/agent/" not in path:
            return

        raw = message.content or b""
        if len(raw) < 32:
            return

        new_raw, result = try_strip_connect_bytes(
            raw, self.config.strip, content_type="application/proto", path=path
        )
        if result is None:
            return

        # Reuse accounting without an HTTP body rewrite helper specifics
        before_chars = result.original_chars
        after_chars = result.stripped_chars if result.changed else before_chars
        tokens_in = max(1, before_chars // 4)
        tokens_out = max(1, after_chars // 4) if result.changed else tokens_in
        # Allow substantial WS prompt stripping (file stubs). Only block extreme
        # gutting that keeps <15% — BidiAppend sync is handled separately.
        force_dry = bool(
            result.changed
            and before_chars >= 8000
            and after_chars < int(before_chars * 0.15)
        )
        effective_dry = self.config.strip.dry_run or force_dry
        notes = ["websocket", *result.notes]
        if force_dry:
            notes.append("safety-skip-apply")
        self.store.record(
            host=host,
            path=f"ws:{path}",
            chars_in=before_chars,
            chars_out=after_chars if not force_dry else before_chars,
            tokens_in=tokens_in,
            tokens_out=tokens_out if not force_dry else tokens_in,
            stripped=result.changed and not force_dry,
            notes=notes,
            dry_run=effective_dry,
        )
        if result.changed:
            if not effective_dry and new_raw != raw:
                message.content = bytes(new_raw)
            ctx.log.info(
                f"{'dry-run ' if effective_dry else ''}ws-stripped {host}{path}: "
                f"{before_chars}→{after_chars} chars notes={notes}"
            )
            if len(raw) >= 2048:
                self._dump_raw_sample(host, f"ws{path}", raw, "websocket/binary")
        else:
            ctx.log.info(
                f"ws-no-op {host}{path}: {len(raw)} bytes / {before_chars} chars"
            )
            if len(raw) >= 1024:
                self._dump_raw_sample(host, f"ws{path}", raw, "websocket/binary")

    def _record_and_apply(
        self,
        flow: http.HTTPFlow,
        host: str,
        path: str,
        raw: bytes,
        new_raw: bytes,
        result,  # noqa: ANN001
    ) -> None:
        if isinstance(new_raw, str):
            new_raw = new_raw.encode("utf-8")
        if not isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw) if raw is not None else b""

        before = raw.decode("utf-8", errors="ignore")
        after = new_raw.decode("utf-8", errors="ignore") if result.changed else before
        # For binary protobuf, estimate from string-char counts in the result
        if result.original_chars and (
            not before or b"\x00" in raw[:64] or not looks_like_text(raw)
        ):
            tokens_in = max(1, result.original_chars // 4)
            tokens_out = (
                max(1, result.stripped_chars // 4) if result.changed else tokens_in
            )
        else:
            tokens_in = estimate_tokens(before)
            tokens_out = estimate_tokens(after) if result.changed else tokens_in

        chars_in = result.original_chars
        chars_out = result.stripped_chars if result.changed else chars_in

        # BidiAppend handled above — never rewrite here.
        force_dry = bool(
            result.changed
            and chars_in >= 8000
            and chars_out < int(chars_in * 0.15)
        )
        effective_dry = self.config.strip.dry_run or force_dry
        notes = list(result.notes)
        if force_dry:
            notes.append("safety-skip-apply")
            ctx.log.warn(
                f"safety-skip {host}{path}: would keep {chars_out}/{chars_in} chars — "
                "forwarding original body"
            )

        self.store.record(
            host=host,
            path=path,
            chars_in=chars_in,
            chars_out=chars_out if not force_dry else chars_in,
            tokens_in=tokens_in,
            tokens_out=tokens_out if not force_dry else tokens_in,
            stripped=result.changed and not force_dry,
            notes=notes,
            dry_run=effective_dry,
        )

        if result.changed:
            if not effective_dry and new_raw != raw:
                flow.request.raw_content = bytes(new_raw)
                flow.request.headers["content-length"] = str(len(new_raw))
                if "transfer-encoding" in flow.request.headers:
                    del flow.request.headers["transfer-encoding"]

            ctx.log.info(
                f"{'dry-run ' if effective_dry else ''}stripped {host}{path}: "
                f"{chars_in}→{chars_out} chars "
                f"(~{tokens_in}→{tokens_out} tok) notes={notes}"
            )
            if not force_dry:
                self._maybe_dump(host, path, result, tokens_in, tokens_out)
        else:
            ctx.log.info(
                f"no-op {host}{path} ({result.original_chars} chars) "
                f"notes={result.notes} ct={flow.request.headers.get('content-type', '-')}"
            )
            # Capture unstripped agent bodies so we can tune the protobuf walker.
            if len(raw) >= 1024 and path_should_strip_connect(path):
                self._dump_raw_sample(host, path, raw, flow.request.headers.get("content-type") or "")

    def _dump_raw_sample(self, host: str, path: str, raw: bytes, content_type: str) -> None:
        try:
            ts = int(time.time() * 1000)
            safe = path.replace("/", "_")[:80]
            base = self.dump_dir / f"{ts}_noop{safe}"
            meta = {
                "host": host,
                "path": path,
                "content_type": content_type,
                "bytes": len(raw),
                "head_hex": raw[:64].hex(),
                "ascii_sample": raw[:400].decode("utf-8", errors="replace"),
            }
            base.with_suffix(".meta.json").write_text(
                json.dumps(meta, indent=2), encoding="utf-8"
            )
            base.with_suffix(".bin").write_bytes(raw)
            ctx.log.info(f"wrote noop sample {base.name}.bin ({len(raw)} bytes)")
        except OSError as e:
            ctx.log.warn(f"noop dump failed: {e}")

    def done(self) -> None:
        ctx.log.info(f"aiproxy stats: {self.store.snapshot()['totals']}")

    def _maybe_dump(
        self, host: str, path: str, result, tokens_in: int, tokens_out: int
    ) -> None:  # noqa: ANN001
        lc = self.config.logging
        if not lc.dump_bodies:
            return
        if lc.dump_only_when_stripped and not result.changed:
            return
        ts = int(time.time() * 1000)
        safe_host = host.replace("/", "_")
        safe_path = path.replace("/", "_")[:80]
        base = self.dump_dir / f"{ts}_{safe_host}{safe_path}"
        meta = {
            "host": host,
            "path": path,
            "original_chars": result.original_chars,
            "stripped_chars": result.stripped_chars,
            "saved_chars": result.saved_chars,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_saved": max(0, tokens_in - tokens_out),
            "notes": result.notes,
            "dry_run": self.config.strip.dry_run,
        }
        base.with_suffix(".meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        try:
            base.with_name(base.name + ".before.json").write_text(
                json.dumps(_redact(result.original), indent=2, ensure_ascii=False)[
                    :500_000
                ],
                encoding="utf-8",
            )
            base.with_name(base.name + ".after.json").write_text(
                json.dumps(_redact(result.stripped), indent=2, ensure_ascii=False)[
                    :500_000
                ],
                encoding="utf-8",
            )
        except (TypeError, ValueError):
            pass


def looks_like_text(raw: bytes) -> bool:
    if not raw:
        return False
    if b"\x00" in raw[:64]:
        return False
    sample = raw[:200]
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _redact(obj, depth: int = 0):  # noqa: ANN001
    if depth > 12:
        return "…"
    if isinstance(obj, str):
        if len(obj) > 2000:
            return obj[:1000] + f"…[{len(obj)} chars]…" + obj[-200:]
        return obj
    if isinstance(obj, list):
        return [_redact(x, depth + 1) for x in obj[:80]]
    if isinstance(obj, dict):
        out = {}
        for k, v in list(obj.items())[:80]:
            lk = str(k).lower()
            if any(
                s in lk
                for s in ("key", "token", "secret", "authorization", "password")
            ):
                out[k] = "[redacted]"
            else:
                out[k] = _redact(v, depth + 1)
        return out
    return obj


addons: list = []


def make_addons(config: Config, store: StatsStore | None = None) -> list:
    global addons
    addons = [CursorStripAddon(config, store=store)]
    return addons
