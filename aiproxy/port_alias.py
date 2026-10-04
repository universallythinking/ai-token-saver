"""Tiny TCP proxy: memorable alias IP:80 → dashboard bind host:port.

Reads config.yaml for the upstream dashboard port on each connection, so
changing dashboard_port does not require re-running --install-hostname.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("aiproxy.port_alias")


class TargetResolver:
    """Resolve dashboard upstream from config; refresh when the file changes."""

    def __init__(
        self,
        config_path: Path | None,
        *,
        fallback_host: str = "127.0.0.1",
        fallback_port: int = 8081,
    ) -> None:
        self.config_path = config_path
        self.fallback_host = fallback_host
        self.fallback_port = fallback_port
        self._mtime: float | None = None
        self._host = fallback_host
        self._port = fallback_port
        self.refresh(force=True)

    def refresh(self, *, force: bool = False) -> tuple[str, int]:
        path = self.config_path
        if path is None or not path.is_file():
            self._host, self._port = self.fallback_host, self.fallback_port
            return self._host, self._port
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return self._host, self._port
        if not force and self._mtime is not None and mtime == self._mtime:
            return self._host, self._port
        try:
            from .config import Config

            cfg = Config.load(path)
            host = cfg.listen_host
            if host in ("0.0.0.0", "::", "[::]"):
                host = "127.0.0.1"
            port = int(cfg.dashboard_port)
            if (host, port) != (self._host, self._port):
                log.info("upstream now %s:%s (from %s)", host, port, path)
            self._host, self._port = host, port
            self._mtime = mtime
        except Exception as e:
            log.warning("could not load %s (%s); using %s:%s", path, e, self._host, self._port)
        return self._host, self._port


def resolve_listen(config_path: Path | None, listen_host: str | None, listen_port: int | None) -> tuple[str, int]:
    host = listen_host
    port = listen_port
    if config_path and config_path.is_file():
        try:
            from .config import Config

            cfg = Config.load(config_path)
            if not host:
                host = cfg.dashboard_alias_ip or "127.0.0.2"
            if port is None:
                port = int(cfg.dashboard_alias_port or 80)
        except Exception as e:
            log.warning("listen defaults from config failed: %s", e)
    return host or "127.0.0.2", int(port if port is not None else 80)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def _handle(
    client_r: asyncio.StreamReader,
    client_w: asyncio.StreamWriter,
    resolver: TargetResolver,
) -> None:
    target_host, target_port = resolver.refresh()
    try:
        remote_r, remote_w = await asyncio.open_connection(target_host, target_port)
    except OSError as e:
        peer = client_w.get_extra_info("peername")
        log.warning("upstream %s:%s unavailable (%s) for %s", target_host, target_port, e, peer)
        client_w.close()
        await client_w.wait_closed()
        return
    try:
        await asyncio.gather(
            _pipe(client_r, remote_w),
            _pipe(remote_r, client_w),
        )
    finally:
        for w in (client_w, remote_w):
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass


async def run_proxy(listen_host: str, listen_port: int, resolver: TargetResolver) -> None:
    server = await asyncio.start_server(
        lambda r, w: _handle(r, w, resolver),
        host=listen_host,
        port=listen_port,
    )
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets or [])
    th, tp = resolver.refresh()
    log.info("port alias listening on %s → %s:%s (auto from config)", addrs, th, tp)
    async with server:
        await server.serve_forever()


def default_config_path() -> Path:
    env = os.environ.get("AIPROXY_CONFIG", "").strip()
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parent.parent / "config.yaml"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="aiproxy dashboard port alias (80 → dashboard; port from config)"
    )
    p.add_argument(
        "--config",
        default=str(default_config_path()),
        help="config.yaml used to discover dashboard_port (re-read when it changes)",
    )
    p.add_argument("--listen-host", default=None, help="override dashboard_alias_ip")
    p.add_argument("--listen-port", type=int, default=None, help="override dashboard_alias_port")
    p.add_argument("--target-host", default=None, help="fallback upstream host if config missing")
    p.add_argument("--target-port", type=int, default=None, help="fallback upstream port if config missing")
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config_path = Path(args.config).expanduser().resolve() if args.config else None
    listen_host, listen_port = resolve_listen(config_path, args.listen_host, args.listen_port)
    resolver = TargetResolver(
        config_path if config_path and config_path.is_file() else None,
        fallback_host=args.target_host or "127.0.0.1",
        fallback_port=int(args.target_port or 8081),
    )

    try:
        asyncio.run(run_proxy(listen_host, listen_port, resolver))
    except PermissionError:
        log.error(
            "cannot bind %s:%s — need root/admin (install via: sudo python -m aiproxy --install-hostname)",
            listen_host,
            listen_port,
        )
        return 1
    except OSError as e:
        log.error("bind failed %s:%s — %s", listen_host, listen_port, e)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
