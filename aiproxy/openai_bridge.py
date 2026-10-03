from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import ClientSession, ClientTimeout, web

from .config import Config
from .dashboard import start_dashboard_background
from .pricing import extract_model
from .stats import init_store, resolve_stats_path
from .stripper import estimate_tokens, try_strip_bytes

log = logging.getLogger("aiproxy.bridge")

# Long-lived chat streams must not hit aiohttp's default total timeout.
_UPSTREAM_TIMEOUT = ClientTimeout(total=None, sock_connect=30, sock_read=None)


def create_app(config: Config, store) -> web.Application:  # noqa: ANN001
    app = web.Application(client_max_size=64 * 1024 * 1024)
    app["config"] = config
    app["store"] = store
    app.router.add_route("*", "/{path:.*}", handle)
    return app


def resolve_upstream(config: Config, request: web.Request | None = None) -> str:
    if config.mode == "anthropic":
        return config.anthropic_upstream.rstrip("/")
    if config.mode == "openai":
        return config.openai_upstream.rstrip("/")

    # reverse: route by path / headers so one process can serve both apps
    path = ""
    headers = {}
    if request is not None:
        path = (request.match_info.get("path") or "").lower()
        headers = {k.lower(): v for k, v in request.headers.items()}
    looks_anthropic = (
        "anthropic-version" in headers
        or path.startswith("v1/messages")
        or path.endswith("/messages")
        or "anthropic" in path
        or ("x-api-key" in headers and "authorization" not in headers)
    )
    if looks_anthropic:
        return config.anthropic_upstream.rstrip("/")
    return config.openai_upstream.rstrip("/")


async def handle(request: web.Request) -> web.StreamResponse:
    config: Config = request.app["config"]
    store = request.app["store"]
    upstream = resolve_upstream(config, request)
    path = request.match_info.get("path", "")
    url = f"{upstream}/{path}"
    if request.query_string:
        url = f"{url}?{request.query_string}"

    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower()
        not in {
            "host",
            "content-length",
            "transfer-encoding",
            # Let aiohttp negotiate encoding with upstream; we re-emit plain bytes.
            "accept-encoding",
        }
    }

    body = await request.read()
    host = urlparse(upstream).hostname or "upstream"
    req_path = "/" + path if path else "/"

    if request.method.upper() in {"POST", "PUT", "PATCH"} and body:
        new_body, result = try_strip_bytes(body, config.strip)
        if result is not None:
            before = body.decode("utf-8", errors="ignore")
            after = (
                new_body.decode("utf-8", errors="ignore")
                if result.changed
                else before
            )
            tokens_in = estimate_tokens(before)
            tokens_out = estimate_tokens(after)
            chars_in = result.original_chars
            chars_out = result.stripped_chars if result.changed else chars_in
            store.record(
                host=host,
                path=req_path,
                chars_in=chars_in,
                chars_out=chars_out,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                stripped=result.changed,
                notes=result.notes,
                dry_run=config.strip.dry_run,
                model=extract_model(body, result.original),
            )
            if result.changed:
                log.info(
                    "strip %s%s: %d→%d chars (~%d→%d tok) notes=%s dry_run=%s",
                    host,
                    req_path,
                    chars_in,
                    chars_out,
                    tokens_in,
                    tokens_out,
                    result.notes,
                    config.strip.dry_run,
                )
                if not config.strip.dry_run:
                    body = new_body
            else:
                log.info("unchanged %s%s (%d chars)", host, req_path, chars_in)
        else:
            store.record_passthrough(
                "passthrough",
                host=host,
                path=req_path,
                chars_in=len(body),
                reason="non-json",
            )
            log.info("passthrough non-json %s%s (%d bytes)", host, req_path, len(body))

    async with ClientSession(timeout=_UPSTREAM_TIMEOUT) as session:
        async with session.request(
            request.method,
            url,
            headers=headers,
            data=body if body else None,
            allow_redirects=False,
        ) as resp:
            out_headers = {
                k: v
                for k, v in resp.headers.items()
                if k.lower()
                not in {
                    "content-encoding",
                    "transfer-encoding",
                    "content-length",
                    "connection",
                }
            }
            response = web.StreamResponse(status=resp.status, headers=out_headers)
            await response.prepare(request)
            async for chunk in resp.content.iter_any():
                await response.write(chunk)
            await response.write_eof()
            return response


def _banner(config: Config) -> str:
    base = f"http://{config.listen_host}:{config.listen_port}"
    dash = f"http://{config.listen_host}:{config.dashboard_port}/"
    dry = config.strip.dry_run
    if config.mode == "anthropic":
        return (
            f"aiproxy anthropic bridge on {base}\n"
            f"  upstream   {config.anthropic_upstream}\n"
            f"  dashboard  {dash}\n"
            f"  dry-run    {dry}\n"
            f"\n"
            f"Claude Code:\n"
            f"  export ANTHROPIC_BASE_URL=\"{base}\"\n"
            f"  claude\n"
            f"  /status   # confirm base URL\n"
        )
    if config.mode == "openai":
        return (
            f"aiproxy openai bridge on {base}\n"
            f"  upstream   {config.openai_upstream}\n"
            f"  dashboard  {dash}\n"
            f"  dry-run    {dry}\n"
            f"\n"
            f"Cursor → Settings → Models → OpenAI Base URL:\n"
            f"  {base}/v1\n"
            f"Keep your OpenAI API key. Select an OpenAI-compatible model.\n"
        )
    return (
        f"aiproxy reverse bridge on {base}\n"
        f"  openai     {config.openai_upstream}\n"
        f"  anthropic  {config.anthropic_upstream}\n"
        f"  dashboard  {dash}\n"
        f"  dry-run    {dry}\n"
        f"\n"
        f"Claude Code:  export ANTHROPIC_BASE_URL=\"{base}\" && claude\n"
        f"Cursor BYOK:  OpenAI Base URL → {base}/v1\n"
    )


async def run_openai_bridge(config: Config) -> None:
    root = Path(__file__).resolve().parent.parent
    store = init_store(resolve_stats_path(config.stats_path, base=root))
    start_dashboard_background(config, store)

    app = create_app(config, store)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.listen_host, config.listen_port)
    await site.start()
    print(_banner(config), flush=True)
    log.info(
        "%s bridge listening on http://%s:%s",
        config.mode,
        config.listen_host,
        config.listen_port,
    )
    await asyncio.Event().wait()
