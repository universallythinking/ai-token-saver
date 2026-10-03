from __future__ import annotations

from pathlib import Path

from aiproxy.stats import StatsStore, resolve_stats_path


def test_resolve_stats_path_relative_to_base(tmp_path: Path):
    p = resolve_stats_path("logs/stats.json", base=tmp_path)
    assert p == (tmp_path / "logs" / "stats.json").resolve()


def test_persist_survives_reload(tmp_path: Path):
    path = tmp_path / "stats.json"
    store = StatsStore(persist_path=path)
    store.record(
        host="api.example.com",
        path="/v1/chat",
        chars_in=4000,
        chars_out=1000,
        tokens_in=1000,
        tokens_out=250,
        stripped=True,
        notes=["dedupe"],
        model="gpt-4o",
    )
    store.record_passthrough(chars_in=100, host="h", path="/p", reason="skip")
    assert path.exists()
    assert store.totals["tokens_saved"] == 750
    assert store.totals["requests"] == 2
    assert store.by_model["gpt-4o"]["tokens_saved"] == 750
    snap = store.snapshot()
    assert snap["usd_saved_est"] > 0
    assert "gpt-4o" in snap["by_model"]

    first = store.first_started_at
    reloaded = StatsStore(persist_path=path)
    assert reloaded.totals["tokens_saved"] == 750
    assert reloaded.totals["tokens_in"] == 1000 + max(1, 100 // 4)
    assert reloaded.totals["stripped"] == 1
    assert reloaded.totals["requests"] == 2
    assert reloaded.by_model["gpt-4o"]["tokens_saved"] == 750
    assert abs(reloaded.first_started_at - first) < 1e-6
    # New session clock
    assert reloaded.started_at >= first
    assert len(reloaded.recent) == 2
    assert reloaded.recent[0].stripped is False  # newest first (passthrough)
    assert reloaded.recent[1].stripped is True
    assert reloaded.recent[1].model == "gpt-4o"
    assert reloaded.buckets


def test_reset_clears_persisted(tmp_path: Path):
    path = tmp_path / "stats.json"
    store = StatsStore(persist_path=path)
    store.record(
        host="h",
        path="/",
        chars_in=100,
        chars_out=50,
        tokens_in=25,
        tokens_out=12,
        stripped=True,
    )
    store.reset()
    reloaded = StatsStore(persist_path=path)
    assert reloaded.totals["requests"] == 0
    assert reloaded.totals["tokens_saved"] == 0
    assert len(reloaded.recent) == 0


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        test_resolve_stats_path_relative_to_base(root)
        test_persist_survives_reload(root)
        test_reset_clears_persisted(root)
    print("ok")
