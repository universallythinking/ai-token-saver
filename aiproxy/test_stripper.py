from __future__ import annotations

from aiproxy.config import StripConfig
from aiproxy.stripper import strip_payload


def test_trims_old_messages_and_keeps_system():
    cfg = StripConfig(
        max_messages=4,
        recent_turn_window=2,
        max_chars_recent=10_000,
        drop_keys=["store", "metadata"],
    )
    body = {
        "model": "gpt-test",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
        ],
        "store": True,
        "metadata": {"x": 1},
    }
    result = strip_payload(body, cfg)
    assert result.changed
    roles = [m["role"] for m in result.stripped["messages"]]
    assert roles[0] == "system"
    assert "store" not in result.stripped
    assert "metadata" not in result.stripped
    # system + last 3 non-system (max_messages=4)
    assert len(result.stripped["messages"]) == 4


def test_truncates_tool_results():
    # Older tool turns stay tightly capped; recent explore results keep more.
    cfg = StripConfig(
        max_tool_result_chars=50,
        max_messages=24,
        recent_turn_window=1,
    )
    body = {
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": "x" * 500},  # older
            {"role": "user", "content": "thanks"},
            {"role": "tool", "content": "y" * 500},  # recent
        ]
    }
    result = strip_payload(body, cfg)
    assert result.changed
    old_tool = result.stripped["messages"][1]["content"]
    assert len(old_tool) < 200
    assert "tool_result" in old_tool or "removed" in old_tool


def test_responses_input_path():
    cfg = StripConfig(max_messages=3)
    body = {
        "input": [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
            {"role": "assistant", "content": "d"},
            {"role": "user", "content": "e"},
        ],
        "include": ["reasoning.encrypted_content"],
    }
    cfg.drop_keys = ["include"]
    result = strip_payload(body, cfg)
    assert result.changed
    assert len(result.stripped["input"]) == 3
    assert "include" not in result.stripped


if __name__ == "__main__":
    test_trims_old_messages_and_keeps_system()
    test_truncates_tool_results()
    test_responses_input_path()
    print("ok")
