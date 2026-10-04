"""mitmdump -s entrypoint. Loads config from AIPROXY_CONFIG."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from aiproxy.addon import make_addons  # noqa: E402
from aiproxy.config import Config  # noqa: E402
from aiproxy.dashboard import start_dashboard_background  # noqa: E402
from aiproxy.pricing import set_assumed_model  # noqa: E402
from aiproxy.stats import init_store, resolve_stats_path  # noqa: E402

_cfg_path = os.environ.get("AIPROXY_CONFIG") or str(_ROOT / "config.yaml")
_config = Config.load(_cfg_path)
if os.environ.get("AIPROXY_DRY_RUN") == "1":
    _config.strip.dry_run = True

set_assumed_model(_config.pricing.assume_model)

_store = init_store(resolve_stats_path(_config.stats_path, base=_ROOT))
start_dashboard_background(_config, _store)

addons = make_addons(_config, store=_store)
