from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _venv_python() -> Path:
    """Preferred interpreter for running aiproxy: project .venv if present."""
    candidate = _project_root() / ".venv" / "bin" / "python"
    if candidate.exists():
        return candidate
    return Path(sys.executable)


def _bootstrap_python() -> str:
    """Interpreter used to *create* the venv (python3 on macOS/Linux)."""
    # Prefer python3 when available — bare `python` is often missing on macOS.
    from shutil import which

    if which("python3"):
        return "python3"
    if which("python"):
        return "python"
    return "python3"


def _trust_ca_snippet(ca: Path) -> str:
    cer = ca.with_suffix(".cer")
    return f"""# Trust mitmproxy CA (macOS, once).
# Prefer the CLI — Keychain UI often hides the cert.
#
# Option 1 (recommended) — trust as a system root (asks for Mac password):
sudo security add-trusted-cert -d -r trustRoot \\
  -k /Library/Keychains/System.keychain \\
  "{ca}"
#
# Option 2 — open the .cer (not .pem) and set Always Trust in the dialog:
open "{cer}"
#   Then: double-click mitmproxy → Trust → Always Trust → close → password
#
# Verify it is present:
security find-certificate -c mitmproxy -a
#
# Also set NODE_EXTRA_CA_CERTS whenever you launch Cursor/Claude (Node path):
#   export NODE_EXTRA_CA_CERTS="{ca}"
"""


def _install_snippet() -> str:
    root = _project_root()
    py_boot = _bootstrap_python()
    py = _venv_python()
    return f"""# === Install deps (once) ===
# Use python3 to create the venv (not bare `python` — often missing on macOS).
cd "{root}"
{py_boot} -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -e .

# Always run aiproxy with the venv interpreter from this directory
# (or after pip install -e ., from anywhere):
#   {py} -m aiproxy ...
"""


def _cursor_settings_snippet(config) -> str:  # noqa: ANN001
    proxy = f"http://{config.listen_host}:{config.listen_port}"
    ca = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"
    py = _venv_python()
    return f"""{_install_snippet()}
# === Cursor + aiproxy (MITM — default Cursor models) ===
# SSE/Connect responses are streamed (not buffered) so replies work.
# Agent bodies are protobuf — little JSON stripping on that path.
#
# 1) Start:
{py} -m aiproxy --mode mitm --dry-run

{_trust_ca_snippet(ca)}
# 3) Launch Cursor with:
export NODE_EXTRA_CA_CERTS="{ca}"
open -a Cursor

# 4) Cursor → Settings → Open User Settings (JSON):
{{
  "http.proxy": "{proxy}",
  "http.proxySupport": "override",
  "http.proxyStrictSSL": false,
  "cursor.general.disableHttp2": true
}}

# 5) Fully quit Cursor (Cmd+Q) and relaunch with NODE_EXTRA_CA_CERTS set.
# Dashboard: http://{config.listen_host}:{config.dashboard_port}/
#
# --- Alternative: OpenAI Base URL (BYOK only) ---
# {py} -m aiproxy --mode openai
# Cursor → Models → OpenAI Base URL: {proxy}/v1
"""


def _claude_settings_snippet(config) -> str:  # noqa: ANN001
    proxy = f"http://{config.listen_host}:{config.listen_port}"
    ca = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"
    py = _venv_python()
    return f"""{_install_snippet()}
# === Claude Code + aiproxy (recommended: ANTHROPIC_BASE_URL) ===
# Strips JSON. No CA cert.
#
# 1) Start reverse proxy:
{py} -m aiproxy --mode anthropic
#    (or both apps: {py} -m aiproxy --mode reverse)
#
# 2) Point Claude at it (keep your existing API key / login):
export ANTHROPIC_BASE_URL="{proxy}"
claude
# Check: /status  (base URL should be {proxy})
#
# Persist for every session — ~/.claude/settings.json:
# {{
#   "env": {{
#     "ANTHROPIC_BASE_URL": "{proxy}"
#   }}
# }}
#
# Dashboard: http://{config.listen_host}:{config.dashboard_port}/
#
# --- Alternative: MITM via HTTPS_PROXY ---
# {py} -m aiproxy --mode mitm --dry-run
{_trust_ca_snippet(ca)}
# export HTTPS_PROXY="{proxy}" HTTP_PROXY="{proxy}"
# export NODE_EXTRA_CA_CERTS="{ca}"
# claude
"""


def _run_mitm(config) -> int:  # noqa: ANN001
    from mitmproxy.tools.main import mitmdump

    addon_path = Path(__file__).resolve().parent / "mitm_entry.py"
    os.environ["AIPROXY_CONFIG"] = getattr(
        config, "_config_path", str(_project_root() / "config.yaml")
    )
    if config.strip.dry_run:
        os.environ["AIPROXY_DRY_RUN"] = "1"
    else:
        os.environ.pop("AIPROXY_DRY_RUN", None)

    # http2=false: pair with Cursor's cursor.general.disableHttp2 (HTTP/1.1 SSE).
    # Do NOT enable stream_large_bodies — it overrides request buffering and
    # prevents Connect/protobuf stripping on large agent bodies. Responses are
    # still streamed via the addon's responseheaders hook.
    sys.argv = [
        "mitmdump",
        "--listen-host",
        config.listen_host,
        "--listen-port",
        str(config.listen_port),
        "--set",
        "block_global=false",
        "--set",
        "http2=false",
        "--set",
        "websocket=true",
        "-s",
        str(addon_path),
    ]
    ca = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"
    print(
        f"aiproxy mitm on {config.listen_host}:{config.listen_port}\n"
        f"dashboard      http://{config.listen_host}:{config.dashboard_port}/\n"
        f"dashboard v2   http://{config.listen_host}:{config.dashboard_port}/v2\n"
        f"Dry-run={config.strip.dry_run}  http2=off (use Cursor disableHttp2)\n"
        f"\n"
        f"Cursor settings.json:\n"
        f'  "http.proxy": "http://{config.listen_host}:{config.listen_port}",\n'
        f'  "http.proxySupport": "override",\n'
        f'  "http.proxyStrictSSL": false,\n'
        f'  "cursor.general.disableHttp2": true\n'
        f"\n"
        f"Relaunch Cursor with:\n"
        f'  export NODE_EXTRA_CA_CERTS="{ca}"\n'
        f"  open -a Cursor\n"
        f"\n"
        f"Streaming SSE/Connect responses are forwarded immediately "
        f"(fixes 'sent but no reply').\n",
        flush=True,
    )
    mitmdump()
    return 0


def main(argv: list[str] | None = None) -> int:  # setuptools entry point
    parser = argparse.ArgumentParser(
        description="Intercept Cursor traffic and strip fluff before forwarding."
    )
    parser.add_argument(
        "-c",
        "--config",
        default=str(_project_root() / "config.yaml"),
        help="Path to config.yaml",
    )
    parser.add_argument(
        "--mode",
        choices=("mitm", "openai", "anthropic", "reverse"),
        help="mitm=HTTPS intercept; openai/anthropic/reverse=JSON reverse proxy (recommended)",
    )
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log savings but forward original bodies",
    )
    parser.add_argument(
        "--print-cursor-settings",
        action="store_true",
        help="Print Cursor settings.json snippet and exit",
    )
    parser.add_argument(
        "--print-claude-settings",
        action="store_true",
        help="Print Claude Code env / settings snippet and exit",
    )
    args = parser.parse_args(argv)

    from .config import Config

    config = Config.load(args.config)
    config._config_path = str(Path(args.config).resolve())  # type: ignore[attr-defined]
    if args.mode:
        config.mode = args.mode
    if args.host:
        config.listen_host = args.host
    if args.port:
        config.listen_port = args.port
    if args.dry_run:
        config.strip.dry_run = True

    logging.basicConfig(
        level=getattr(logging, config.logging.level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.print_cursor_settings:
        print(_cursor_settings_snippet(config))
        return 0
    if args.print_claude_settings:
        print(_claude_settings_snippet(config))
        return 0

    os.environ["AIPROXY_CONFIG"] = config._config_path  # type: ignore[attr-defined]

    if config.mode in {"openai", "anthropic", "reverse"}:
        from .openai_bridge import run_openai_bridge

        asyncio.run(run_openai_bridge(config))
        return 0

    return _run_mitm(config)


if __name__ == "__main__":
    raise SystemExit(main())
