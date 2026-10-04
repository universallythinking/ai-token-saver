"""Tiny TCP proxy: memorable alias IP:80 → dashboard bind host:port.

Installed as a privileged LaunchDaemon / portproxy so
http://tokensaver.local/ works without typing :8081.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

log = logging.getLogger("aiproxy.port_alias")


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
    target_host: str,
    target_port: int,
) -> None:
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


async def run_proxy(listen_host: str, listen_port: int, target_host: str, target_port: int) -> None:
    server = await asyncio.start_server(
        lambda r, w: _handle(r, w, target_host, target_port),
        host=listen_host,
        port=listen_port,
    )
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets or [])
    log.info("port alias listening on %s → %s:%s", addrs, target_host, target_port)
    async with server:
        await server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="aiproxy dashboard port alias (80 → dashboard)")
    p.add_argument("--listen-host", default="127.0.0.2")
    p.add_argument("--listen-port", type=int, default=80)
    p.add_argument("--target-host", default="127.0.0.1")
    p.add_argument("--target-port", type=int, default=8081)
    args = p.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(
            run_proxy(args.listen_host, args.listen_port, args.target_host, args.target_port)
        )
    except PermissionError:
        log.error(
            "cannot bind %s:%s — need root/admin (install via: sudo python -m aiproxy --install-hostname)",
            args.listen_host,
            args.listen_port,
        )
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
