from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class StripConfig:
    max_messages: int = 24
    max_chars_recent: int = 24000
    max_chars_old: int = 4000
    recent_turn_window: int = 6
    drop_reasoning_parts: bool = True
    dedupe_file_blocks: bool = True
    max_tool_result_chars: int = 2000
    # BidiAppend hex-nested file bodies — stub down to this many chars
    max_bidi_file_chars: int = 256
    drop_keys: list[str] = field(default_factory=list)
    compress_system: bool = True
    max_system_chars: int = 12000
    dry_run: bool = False


@dataclass
class LogConfig:
    level: str = "INFO"
    dump_dir: str = "logs"
    dump_bodies: bool = True
    dump_only_when_stripped: bool = True


@dataclass
class Config:
    listen_host: str = "127.0.0.1"
    listen_port: int = 8080
    dashboard_port: int = 8081
    mode: str = "mitm"
    openai_upstream: str = "https://api.openai.com"
    anthropic_upstream: str = "https://api.anthropic.com"
    intercept_hosts: list[str] = field(default_factory=list)
    block_hosts: list[str] = field(default_factory=list)
    block_host_suffixes: list[str] = field(default_factory=list)
    strip: StripConfig = field(default_factory=StripConfig)
    logging: LogConfig = field(default_factory=LogConfig)
    stats_path: str = "logs/stats.json"

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        path = Path(path)
        raw: dict[str, Any] = {}
        if path.exists():
            with path.open() as f:
                raw = yaml.safe_load(f) or {}

        strip_raw = raw.get("strip") or {}
        log_raw = raw.get("logging") or {}

        return cls(
            listen_host=raw.get("listen_host", "127.0.0.1"),
            listen_port=int(raw.get("listen_port", 8080)),
            dashboard_port=int(raw.get("dashboard_port", 8081)),
            mode=raw.get("mode", "mitm"),
            openai_upstream=raw.get("openai_upstream", "https://api.openai.com"),
            anthropic_upstream=raw.get(
                "anthropic_upstream", "https://api.anthropic.com"
            ),
            intercept_hosts=list(raw.get("intercept_hosts") or []),
            block_hosts=list(raw.get("block_hosts") or []),
            block_host_suffixes=list(raw.get("block_host_suffixes") or []),
            strip=StripConfig(
                max_messages=int(strip_raw.get("max_messages", 24)),
                max_chars_recent=int(strip_raw.get("max_chars_recent", 24000)),
                max_chars_old=int(strip_raw.get("max_chars_old", 4000)),
                recent_turn_window=int(strip_raw.get("recent_turn_window", 6)),
                drop_reasoning_parts=bool(strip_raw.get("drop_reasoning_parts", True)),
                dedupe_file_blocks=bool(strip_raw.get("dedupe_file_blocks", True)),
                max_tool_result_chars=int(strip_raw.get("max_tool_result_chars", 2000)),
                max_bidi_file_chars=int(strip_raw.get("max_bidi_file_chars", 256)),
                drop_keys=list(strip_raw.get("drop_keys") or []),
                compress_system=bool(strip_raw.get("compress_system", True)),
                max_system_chars=int(strip_raw.get("max_system_chars", 12000)),
                dry_run=bool(strip_raw.get("dry_run", False)),
            ),
            logging=LogConfig(
                level=str(log_raw.get("level", "INFO")),
                dump_dir=str(log_raw.get("dump_dir", "logs")),
                dump_bodies=bool(log_raw.get("dump_bodies", True)),
                dump_only_when_stripped=bool(
                    log_raw.get("dump_only_when_stripped", True)
                ),
            ),
            stats_path=str(raw.get("stats_path", "logs/stats.json")),
        )


def host_matches(host: str, patterns: list[str]) -> bool:
    host = host.lower().split(":")[0]
    for pat in patterns:
        pat = pat.lower().strip()
        if not pat:
            continue
        if pat.startswith("*."):
            suffix = pat[1:]  # .example.com
            if host.endswith(suffix) or host == pat[2:]:
                return True
        elif host == pat or host.endswith("." + pat):
            return True
    return False