"""Quick tests for Connect/protobuf stripping. Run: .venv/bin/python -m aiproxy.test_connect_strip"""

from __future__ import annotations

from .config import StripConfig
from .connect_strip import (
    _write_varint,
    path_is_noise,
    path_should_strip_connect,
    try_strip_connect_bytes,
)


def _len_delim(field_num: int, payload: bytes) -> bytes:
    tag = (field_num << 3) | 2
    return _write_varint(tag) + _write_varint(len(payload)) + payload


def test_path_filters() -> None:
    assert path_should_strip_connect("/agent.v1.AgentService/RunSSE")
    assert path_should_strip_connect("/aiserver.v1.BidiService/BidiAppend")
    assert not path_should_strip_connect("/aiserver.v1.AnalyticsService/Batch")
    assert not path_should_strip_connect("/v1/traces")
    assert path_is_noise("/tev1/v1/rgstr?k=client&gz=1")
    assert path_is_noise("/aiserver.v1.DashboardService/GetTeams")
    assert not path_is_noise("/agent.v1.AgentService/RunSSE")


def test_raw_protobuf_truncates_long_string() -> None:
    cfg = StripConfig(max_chars_old=100, max_tool_result_chars=100, dry_run=False)
    big = ("hello world " * 80).encode()
    msg = _len_delim(1, b"short") + _len_delim(2, big)
    new, result = try_strip_connect_bytes(
        msg, cfg, content_type="application/proto"
    )
    assert result is not None
    assert result.changed
    assert result.stripped_chars < result.original_chars
    assert len(new) < len(msg)
    assert b"protobuf" in new or b"truncated" in new or len(new) < len(msg)


def test_connect_frame() -> None:
    cfg = StripConfig(max_chars_old=80, max_tool_result_chars=80, dry_run=False)
    big = ("context dump " * 100).encode()
    proto = _len_delim(1, big)
    frame = bytes([0]) + len(proto).to_bytes(4, "big") + proto
    new, result = try_strip_connect_bytes(
        frame, cfg, content_type="application/connect+proto"
    )
    assert result is not None
    assert result.changed
    assert len(new) < len(frame)


def test_dry_run_does_not_rewrite() -> None:
    cfg = StripConfig(max_chars_old=50, max_tool_result_chars=50, dry_run=True)
    big = ("x" * 400).encode()
    msg = _len_delim(1, big)
    new, result = try_strip_connect_bytes(msg, cfg, content_type="application/proto")
    assert result is not None
    assert result.changed
    assert new == msg


def test_cross_field_dedupe() -> None:
    cfg = StripConfig(max_chars_old=50_000, max_tool_result_chars=50_000, dry_run=False)
    blob = ("repeated context block " * 40).encode()
    msg = _len_delim(1, blob) + _len_delim(2, blob) + _len_delim(3, b"unique once")
    new, result = try_strip_connect_bytes(msg, cfg, content_type="application/proto")
    assert result is not None
    assert result.changed
    assert result.stripped_chars < result.original_chars
    assert b"deduped" in new
    assert new.count(blob) == 1


def test_top_level_gzip_body() -> None:
    import gzip

    cfg = StripConfig(max_chars_old=80, max_tool_result_chars=80, dry_run=False)
    big = ("hello world " * 80).encode()
    msg = _len_delim(1, big)
    gz = gzip.compress(msg)
    new, result = try_strip_connect_bytes(gz, cfg, content_type="application/proto")
    assert result is not None
    assert result.changed
    assert new[:2] == b"\x1f\x8b"
    assert len(gzip.decompress(new)) < len(msg)
    assert "gunzipped body" in result.notes


def test_bidi_hex_nested_file_stub() -> None:
    """Agent prompts wrap file bytes as hex inside protobuf — stub the inner source."""
    cfg = StripConfig(
        max_chars_old=200_000,
        max_bidi_file_chars=80,
        dry_run=False,
    )
    source = ("from __future__ import annotations\n\n" + ("line of code\n" * 200)).encode()
    # innermost: field 2 = source text
    inner = _len_delim(2, source)
    hex_blob = inner.hex().encode("ascii")
    # outer: field 1 = hex string
    outer = _len_delim(1, hex_blob)
    new, result = try_strip_connect_bytes(
        outer,
        cfg,
        content_type="application/proto",
        path="/agent/v1/run",
    )
    assert result is not None
    assert result.changed
    assert result.stripped_chars < result.original_chars
    assert b"bidi file omitted" in new or b"deduped" in new or len(new) < len(outer)
    # Hex nesting note or truncated strings
    assert result.notes


if __name__ == "__main__":
    test_path_filters()
    test_raw_protobuf_truncates_long_string()
    test_connect_frame()
    test_dry_run_does_not_rewrite()
    test_cross_field_dedupe()
    test_top_level_gzip_body()
    test_bidi_hex_nested_file_stub()
    print("ok")
