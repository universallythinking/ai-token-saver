"""Schema-less Connect/protobuf stripping for Cursor agent traffic.

Does not require .proto files. Walks protobuf wire format and truncates large
UTF-8 string fields inside Connect envelopes (or raw protobuf bodies).

Claude / OpenAI JSON reverse-proxy paths are unaffected — this is only used
by the MITM addon for Cursor Connect bodies.
"""

from __future__ import annotations

import gzip
import hashlib
import re
from dataclasses import dataclass, field
from .config import StripConfig
from .stripper import (
    SYSTEM_BOILERPLATE_MARKERS,
    StripResult,
    _compress_system,
    _dedupe_file_blocks,
    _truncate,
    try_strip_bytes,
)

FLAG_COMPRESSED = 0x01
FLAG_END_STREAM = 0x02

# Paths where Cursor packs prompt / tool / context payloads we can shrink.
CURSOR_STRIP_PATH_RE = re.compile(
    r"(AgentService|AiService|BidiService|ChatService|ConversationService|"
    r"ComposerService|BackgroundComposerService|"
    r"RunSSE|Run|Stream|BidiAppend|GetChat|SubmitChat|"
    r"StreamUnified|StreamCpp|AvailableModels|"
    r"/agent/v1/|/aiserver\.v1\.)",
    re.IGNORECASE,
)

# Telemetry / infra — keep streaming, never rewrite.
CURSOR_NEVER_STRIP_PATH_RE = re.compile(
    r"(AnalyticsService|OnlineMetricsService|ReportClient|SubmitLogs|"
    r"/v1/traces|FileSyncService|DashboardService|rgstr|"
    r"GetTeam|GetUser|GetUsage|GetBackground|GetEffective|ListPrivate|"
    r"GetDefaultModel|AvailableModels|GetServerConfig|ReportProcessMetrics|"
    r"GetDefaultModelNudge)",
    re.IGNORECASE,
)


@dataclass
class _ProtoStats:
    string_chars_in: int = 0
    string_chars_out: int = 0
    truncated_fields: int = 0
    deduped_fields: int = 0
    hex_decoded: int = 0
    notes: list[str] = field(default_factory=list)
    seen_hashes: set[str] = field(default_factory=set)
    # BidiAppend: gut file/code blobs; keep ids/metadata.
    aggressive: bool = False
    file_char_limit: int = 256


def path_is_sync(path: str) -> bool:
    """Cursor file-sync channels — not model prompts; rewriting breaks replies."""
    if not path:
        return False
    return bool(
        re.search(r"(BidiAppend|FileSync|FSUpload|FSSync)", path, re.IGNORECASE)
    )


def path_is_noise(path: str) -> bool:
    """Telemetry, dashboard, and other calls that are not model prompts.

    Their bytes must not count as requested tokens.
    """
    if not path:
        return False
    return bool(CURSOR_NEVER_STRIP_PATH_RE.search(path))


def path_should_strip_connect(path: str) -> bool:
    if not path or path_is_noise(path):
        return False
    return bool(CURSOR_STRIP_PATH_RE.search(path))


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while i < len(buf):
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")
    raise ValueError("truncated varint")


def _write_varint(n: int) -> bytes:
    out = bytearray()
    n = int(n)
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _is_mostly_utf8_text(data: bytes) -> bool:
    if not data:
        return False
    if b"\x00" in data:
        return False
    # Nested protobuf messages often decode as latin noise — prefer message walk.
    if _looks_like_message(data) and not data.lstrip()[:1] in (b"{", b"[", b"<"):
        # Still treat as text if it's clearly prose/code (high ASCII letter ratio)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return False
        letters = sum(1 for c in text if c.isalpha() or c.isspace())
        if letters / max(1, len(text)) < 0.55:
            return False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if len(text) < 16:
        return False
    printable = sum(1 for c in text if c.isprintable() or c in "\n\r\t")
    return printable / len(text) >= 0.85


def _looks_like_message(data: bytes) -> bool:
    if len(data) < 2:
        return False
    try:
        i = 0
        fields = 0
        while i < len(data) and fields < 8:
            tag, i = _read_varint(data, i)
            wt = tag & 7
            if wt == 0:
                _, i = _read_varint(data, i)
            elif wt == 1:
                i += 8
            elif wt == 2:
                ln, i = _read_varint(data, i)
                i += ln
            elif wt == 5:
                i += 4
            else:
                return False
            if i > len(data):
                return False
            fields += 1
        return fields >= 1 and i <= len(data)
    except (ValueError, IndexError):
        return False


def _is_hex_ascii(data: bytes) -> bool:
    """True if payload is hex-encoded bytes (Cursor BidiAppend nesting trick)."""
    n = len(data)
    if n < 64 or n % 2:
        return False
    # Fast path: sample + full check
    sample = data[:64] + data[-64:]
    if not all(c in b"0123456789abcdefABCDEF" for c in sample):
        return False
    return all(c in b"0123456789abcdefABCDEF" for c in data)


def _looks_like_code_or_file(text: str) -> bool:
    if len(text) < 400:
        return False
    head = text.lstrip()[:240]
    markers = (
        "from __future__",
        "import ",
        "package ",
        "#!/",
        '"""',
        "'''",
        "#include",
        "export ",
        "def ",
        "class ",
        "const ",
        "fn ",
        "pub fn",
        "func ",
        "using ",
        "<!DOCTYPE",
        "<?xml",
        "{",
        "---\n",
        "strip:",
        "listen_host:",
    )
    if any(head.startswith(m) for m in markers):
        return True
    if any(m in head for m in ("from __future__", "import ", "def ", "class ")):
        return True
    # Dense multi-line source / config
    if text.count("\n") >= 10 and len(text) >= 800:
        return True
    return False


def _truncate_utf8_bytes(data: bytes, limit: int, stats: _ProtoStats) -> bytes:
    text = data.decode("utf-8", errors="ignore")
    if not text:
        return data

    # Exact duplicates across fields (Cursor often repeats rules / file dumps).
    if len(text) >= 256:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if digest in stats.seen_hashes:
            stats.deduped_fields += 1
            return f"[deduped {len(text)} chars]".encode("utf-8")
        stats.seen_hashes.add(digest)

    changed = False
    text2, n = _dedupe_file_blocks(text)
    if n:
        text = text2
        changed = True

    # BidiAppend file sync: replace bulky source/config with a short stub.
    if stats.aggressive and _looks_like_code_or_file(text):
        cap = max(64, int(stats.file_char_limit))
        if len(text) > cap:
            stub = (
                text[: max(0, cap - 48)].rstrip()
                + f"\n…[bidi file omitted: {len(text)} chars]…"
            )
            return stub.encode("utf-8")

    # Boilerplate-only blobs can still be compressed; mixed agent context must not
    # be crushed or Cursor returns 400 "content missing".
    head = text.lstrip()[:800]
    is_pure_boilerplate = (
        any(head.startswith(m) for m in SYSTEM_BOILERPLATE_MARKERS)
        or head.startswith("<user_rules")
        or head.startswith("<communication>")
        or (len(text) < 12_000 and "AUXIO AIRPLAY" in head)
    )
    effective_limit = min(limit, 8_000) if is_pure_boilerplate else limit
    if is_pure_boilerplate and len(text) > effective_limit:
        text2, did = _compress_system(text, max_chars=effective_limit)
        if did:
            text = text2
            changed = True

    # Hard truncate only above the configured cap (keep config very high for agent bodies).
    if len(text) > effective_limit:
        text = _truncate(text, effective_limit, "protobuf")
        changed = True

    if changed:
        return text.encode("utf-8")
    return data


def _strip_protobuf(data: bytes, max_chars: int, stats: _ProtoStats, depth: int = 0) -> bytes:
    if depth > 20 or not data:
        return data
    out = bytearray()
    i = 0
    try:
        while i < len(data):
            start = i
            tag, i = _read_varint(data, i)
            wire = tag & 7
            out.extend(data[start:i])

            if wire == 0:
                v_start = i
                _, i = _read_varint(data, i)
                out.extend(data[v_start:i])
            elif wire == 1:
                out.extend(data[i : i + 8])
                i += 8
            elif wire == 5:
                out.extend(data[i : i + 4])
                i += 4
            elif wire == 2:
                ln, i = _read_varint(data, i)
                if i + ln > len(data):
                    # corrupt — abort, return original
                    raise ValueError("length past end")
                payload = data[i : i + ln]
                i += ln

                # Hex-encoded nested protobuf (BidiAppend). NEVER truncate as text.
                if _is_hex_ascii(payload):
                    try:
                        nested_raw = bytes.fromhex(payload.decode("ascii"))
                    except ValueError:
                        out.extend(_write_varint(ln))
                        out.extend(payload)
                        continue
                    nested = _strip_protobuf(nested_raw, max_chars, stats, depth + 1)
                    if nested != nested_raw:
                        stats.hex_decoded += 1
                        # Preserve original hex case (Cursor uses lowercase).
                        new_hex = nested.hex().encode("ascii")
                        if payload[:1] in b"ABCDEF":
                            new_hex = nested.hex().upper().encode("ascii")
                        out.extend(_write_varint(len(new_hex)))
                        out.extend(new_hex)
                    else:
                        out.extend(_write_varint(ln))
                        out.extend(payload)
                    continue

                # Prefer descending into nested messages so inner strings get hit.
                if _looks_like_message(payload) and not _is_mostly_utf8_text(payload):
                    nested = _strip_protobuf(payload, max_chars, stats, depth + 1)
                    out.extend(_write_varint(len(nested)))
                    out.extend(nested)
                elif _is_mostly_utf8_text(payload):
                    text = payload.decode("utf-8", errors="ignore")
                    stats.string_chars_in += len(text)
                    # Always run through reducer for known boilerplate; may change even if <= max_chars.
                    new_payload = _truncate_utf8_bytes(payload, max_chars, stats)
                    new_text = new_payload.decode("utf-8", errors="ignore")
                    stats.string_chars_out += len(new_text)
                    if new_payload != payload:
                        stats.truncated_fields += 1
                        out.extend(_write_varint(len(new_payload)))
                        out.extend(new_payload)
                    else:
                        out.extend(_write_varint(ln))
                        out.extend(payload)
                else:
                    # gzip-wrapped blobs sometimes appear as opaque bytes
                    if len(payload) >= 2 and payload[:2] == b"\x1f\x8b":
                        try:
                            inner = gzip.decompress(payload)
                            nested = _strip_protobuf(inner, max_chars, stats, depth + 1)
                            if nested != inner:
                                recompressed = gzip.compress(nested)
                                out.extend(_write_varint(len(recompressed)))
                                out.extend(recompressed)
                                continue
                        except OSError:
                            pass
                    out.extend(_write_varint(ln))
                    out.extend(payload)
            else:
                raise ValueError(f"unsupported wire type {wire}")
    except (ValueError, IndexError):
        return data
    return bytes(out)


def _frame_payload(flags: int, payload: bytes) -> bytes:
    return bytes([flags & 0xFF]) + len(payload).to_bytes(4, "big") + payload


def _looks_connect_framed(data: bytes) -> bool:
    if len(data) < 5:
        return False
    flags = data[0]
    if flags & ~0x03:
        return False
    length = int.from_bytes(data[1:5], "big")
    if length > len(data) - 5:
        return False
    # At least one complete frame, no huge nonsense
    if length > 64 * 1024 * 1024:
        return False
    # Unary raw protobuf often starts with a field tag varint that can look
    # like flags=0 + length. Prefer framed only if the frame fills most of body
    # or multiple frames parse cleanly.
    if 5 + length == len(data):
        return True
    # Try parsing consecutive frames covering the whole buffer
    i = 0
    frames = 0
    while i + 5 <= len(data):
        f = data[i]
        if f & ~0x03:
            return False
        ln = int.from_bytes(data[i + 1 : i + 5], "big")
        if ln > 64 * 1024 * 1024 or i + 5 + ln > len(data):
            return False
        i += 5 + ln
        frames += 1
        if frames > 64:
            break
    return frames >= 1 and i == len(data)


def _strip_connect_framed(
    data: bytes, cfg: StripConfig, *, aggressive: bool = False
) -> tuple[bytes, StripResult]:
    # Cap embedded prompt/tool/file dumps (config max_chars_old).
    max_chars = max(64, int(cfg.max_chars_old))

    stats = _ProtoStats(
        aggressive=aggressive,
        file_char_limit=int(getattr(cfg, "max_bidi_file_chars", 256) or 256),
    )
    out = bytearray()
    i = 0
    while i + 5 <= len(data):
        flags = data[i]
        length = int.from_bytes(data[i + 1 : i + 5], "big")
        i += 5
        payload = data[i : i + length]
        i += length

        if flags & FLAG_END_STREAM:
            # Trailer JSON — leave alone
            out.extend(_frame_payload(flags, payload))
            continue

        body = payload
        if flags & FLAG_COMPRESSED:
            try:
                body = gzip.decompress(payload)
            except OSError:
                out.extend(_frame_payload(flags, payload))
                continue

        # connect+json frames sometimes carry JSON text
        if body.lstrip()[:1] in (b"{", b"["):
            new_body, result = try_strip_bytes(body, cfg)
            if result and result.changed:
                stats.notes.extend(result.notes)
                stats.string_chars_in += result.original_chars
                stats.string_chars_out += result.stripped_chars
                body = new_body if not cfg.dry_run else body
            else:
                stats.string_chars_in += len(body)
                stats.string_chars_out += len(body)
        else:
            stripped = _strip_protobuf(body, max_chars, stats)
            if not cfg.dry_run and stripped != body:
                body = stripped
            elif cfg.dry_run:
                # stats already reflect truncated sizes from dry walk
                pass

        if flags & FLAG_COMPRESSED:
            body = gzip.compress(body)
        out.extend(_frame_payload(flags, body))

    if i != len(data):
        # incomplete parse — don't rewrite
        return data, StripResult(
            original={"bytes": len(data)},
            stripped={"bytes": len(data)},
            changed=False,
            original_chars=len(data),
            stripped_chars=len(data),
            notes=["connect frame parse incomplete"],
        )

    changed = (
        stats.truncated_fields > 0
        or stats.deduped_fields > 0
        or stats.hex_decoded > 0
        or stats.string_chars_out < stats.string_chars_in
    )
    if stats.truncated_fields:
        stats.notes.append(f"truncated {stats.truncated_fields} protobuf strings")
    if stats.deduped_fields:
        stats.notes.append(f"deduped {stats.deduped_fields} repeated strings")
    if stats.hex_decoded:
        stats.notes.append(f"rewrote {stats.hex_decoded} hex-nested blobs")

    chars_in = stats.string_chars_in or len(data)
    chars_out = stats.string_chars_out if changed else chars_in
    if cfg.dry_run:
        new_data = data
    else:
        new_data = bytes(out) if changed else data

    return new_data, StripResult(
        original={"connect_bytes": len(data), "string_chars": chars_in},
        stripped={"connect_bytes": len(new_data), "string_chars": chars_out},
        changed=changed
        and (
            cfg.dry_run
            or new_data != data
            or stats.truncated_fields > 0
            or stats.deduped_fields > 0
            or stats.hex_decoded > 0
        ),
        original_chars=chars_in,
        stripped_chars=chars_out,
        notes=stats.notes,
    )


def _strip_raw_protobuf(
    data: bytes, cfg: StripConfig, *, aggressive: bool = False
) -> tuple[bytes, StripResult]:
    max_chars = max(64, int(cfg.max_chars_old))
    stats = _ProtoStats(
        aggressive=aggressive,
        file_char_limit=int(getattr(cfg, "max_bidi_file_chars", 256) or 256),
    )
    stripped = _strip_protobuf(data, max_chars, stats)
    changed = (
        stats.truncated_fields > 0
        or stats.deduped_fields > 0
        or stats.hex_decoded > 0
    )
    if changed:
        if stats.truncated_fields:
            stats.notes.append(f"truncated {stats.truncated_fields} protobuf strings")
        if stats.deduped_fields:
            stats.notes.append(f"deduped {stats.deduped_fields} repeated strings")
        if stats.hex_decoded:
            stats.notes.append(f"rewrote {stats.hex_decoded} hex-nested blobs")
    chars_in = stats.string_chars_in or len(data)
    chars_out = stats.string_chars_out if changed else chars_in
    new_data = data if cfg.dry_run or not changed else stripped
    return new_data, StripResult(
        original={"proto_bytes": len(data), "string_chars": chars_in},
        stripped={"proto_bytes": len(new_data), "string_chars": chars_out},
        changed=changed,
        original_chars=chars_in,
        stripped_chars=chars_out,
        notes=stats.notes,
    )


def try_strip_connect_bytes(
    data: bytes,
    cfg: StripConfig,
    *,
    content_type: str = "",
    path: str = "",
) -> tuple[bytes, StripResult | None]:
    """Return rewritten Connect/protobuf bytes, or (data, None) if not applicable."""
    if not data:
        return data, None
    ct = (content_type or "").lower()
    aggressive = False
    if path and not path_is_sync(path):
        # Agent model prompts (HTTP + WebSocket) — stub bulky embedded files.
        aggressive = bool(
            re.search(
                r"(/agent/|AgentService|RunSSE|ComposerService|ChatService)",
                path,
                re.IGNORECASE,
            )
        )

    if "json" in ct and "connect" not in ct:
        return data, None

    # Cursor BidiAppend often sends the entire protobuf body as gzip, with
    # content-type application/proto (no Content-Encoding header).
    if data[:2] == b"\x1f\x8b":
        try:
            inner = gzip.decompress(data)
        except OSError:
            return data, None
        new_inner, result = try_strip_connect_bytes(
            inner, cfg, content_type=ct or "application/proto", path=path
        )
        if result is None:
            return data, None
        if not result.changed or cfg.dry_run:
            return data, result
        result.notes = list(result.notes) + ["gunzipped body"]
        return gzip.compress(new_inner, compresslevel=6, mtime=0), result

    # Important: content-type application/connect+proto does NOT always mean
    # Connect envelopes are present — Cursor often sends raw protobuf with that
    # type. Only use the framed parser when the body actually looks framed.
    if _looks_connect_framed(data):
        new_data, result = _strip_connect_framed(data, cfg, aggressive=aggressive)
        if result.changed:
            return new_data, result

    is_proto_ct = any(
        m in ct
        for m in (
            "protobuf",
            "connect+proto",
            "connect+json",
            "application/proto",
            "application/connect",
            "grpc",
        )
    )
    if is_proto_ct or _looks_like_message(data):
        if data.lstrip()[:1] in (b"{", b"["):
            return try_strip_bytes(data, cfg)
        return _strip_raw_protobuf(data, cfg, aggressive=aggressive)

    return data, None
