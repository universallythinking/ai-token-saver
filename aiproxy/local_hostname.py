"""Install memorable local hostname + port-80 alias for the dashboard.

Maps tokensaver.local → 127.0.0.2 and forwards :80 → dashboard :8081 so
http://tokensaver.local/ works without typing a port.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_HOSTNAME = "tokensaver.local"
DEFAULT_ALIAS_IP = "127.0.0.2"
DEFAULT_ALIAS_PORT = 80
LAUNCHD_LABEL = "com.aiproxy.tokensaver-http"
LAUNCHD_PLIST = Path("/Library/LaunchDaemons") / f"{LAUNCHD_LABEL}.plist"


def hosts_path() -> Path:
    if platform.system() == "Windows":
        root = os.environ.get("SystemRoot", r"C:\Windows")
        return Path(root) / "System32" / "drivers" / "etc" / "hosts"
    return Path("/etc/hosts")


def hosts_line(hostname: str, alias_ip: str = DEFAULT_ALIAS_IP) -> str:
    return f"{alias_ip} {hostname}"


def hostname_configured(
    hostname: str,
    path: Path | None = None,
    *,
    alias_ip: str | None = DEFAULT_ALIAS_IP,
) -> bool:
    path = path or hosts_path()
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    host = hostname.lower()
    want_ip = (alias_ip or "").lower() or None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().lower()
        if not line:
            continue
        parts = line.split()
        if host not in parts[1:]:
            continue
        if want_ip is None or parts[0] == want_ip:
            return True
    return False


def ensure_hosts_entry(hostname: str, alias_ip: str = DEFAULT_ALIAS_IP) -> tuple[bool, str]:
    """Write/replace hosts line so hostname resolves to alias_ip."""
    hostname = hostname.strip().lower()
    alias_ip = alias_ip.strip()
    if not hostname or "/" in hostname or " " in hostname:
        return False, f"invalid hostname: {hostname!r}"
    if not alias_ip:
        return False, "alias IP is empty"

    path = hosts_path()
    line = hosts_line(hostname, alias_ip)

    try:
        existing = path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else ""
    except PermissionError:
        return False, _hosts_permission_hint(path, line)
    except OSError as e:
        return False, f"could not read {path}: {e}"

    out_lines: list[str] = []
    removed = False
    for raw in existing.splitlines():
        stripped = raw.split("#", 1)[0].strip().lower()
        parts = stripped.split()
        if parts and hostname in parts[1:]:
            removed = True
            continue
        out_lines.append(raw)

    if hostname_configured(hostname, path, alias_ip=alias_ip) and not removed:
        # Already correct; still rewrite path if we didn't need changes.
        return True, f"{hostname} already points at {alias_ip} in {path}"

    if out_lines and out_lines[-1].strip():
        out_lines.append("")
    out_lines.append(line)
    out_lines.append("")
    text = "\n".join(out_lines)
    if not text.endswith("\n"):
        text += "\n"

    try:
        path.write_text(text, encoding="utf-8")
    except PermissionError:
        return False, _hosts_permission_hint(path, line)
    except OSError as e:
        return False, f"could not update {path}: {e}"

    action = "updated" if removed else "added"
    return True, f"{action} {line!r} in {path}"


def _hosts_permission_hint(path: Path, line: str) -> str:
    if platform.system() == "Windows":
        hint = (
            f'Run an elevated PowerShell:\n'
            f'  Add-Content -Path "{path}" -Value "{line}"'
        )
    else:
        hint = (
            "Re-run with sudo:\n"
            "  sudo .venv/bin/python -m aiproxy --install-hostname"
        )
    return f"need permission to write {path}\n{hint}"


def install_hostname(
    hostname: str,
    *,
    alias_ip: str = DEFAULT_ALIAS_IP,
    alias_port: int = DEFAULT_ALIAS_PORT,
    target_host: str = "127.0.0.1",
    target_port: int = 8081,
    python_exe: str | None = None,
) -> tuple[bool, str]:
    """Hosts entry + port-80 forwarder so http://hostname/ works."""
    messages: list[str] = []
    ok, msg = ensure_hosts_entry(hostname, alias_ip)
    messages.append(msg)
    if not ok:
        return False, "\n".join(messages)

    ok2, msg2 = install_port_forward(
        alias_ip=alias_ip,
        alias_port=alias_port,
        target_host=target_host,
        target_port=target_port,
        python_exe=python_exe or sys.executable,
    )
    messages.append(msg2)
    return ok2, "\n".join(messages)


def install_port_forward(
    *,
    alias_ip: str,
    alias_port: int,
    target_host: str,
    target_port: int,
    python_exe: str,
) -> tuple[bool, str]:
    system = platform.system()
    if system == "Darwin":
        return _install_launchd(
            alias_ip=alias_ip,
            alias_port=alias_port,
            target_host=target_host,
            target_port=target_port,
            python_exe=python_exe,
        )
    if system == "Windows":
        return _install_windows_portproxy(
            alias_ip=alias_ip,
            alias_port=alias_port,
            target_host=target_host,
            target_port=target_port,
        )
    return _install_linux_systemd(
        alias_ip=alias_ip,
        alias_port=alias_port,
        target_host=target_host,
        target_port=target_port,
        python_exe=python_exe,
    )


def _install_launchd(
    *,
    alias_ip: str,
    alias_port: int,
    target_host: str,
    target_port: int,
    python_exe: str,
) -> tuple[bool, str]:
    py = str(Path(python_exe).resolve())
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{py}</string>
    <string>-m</string>
    <string>aiproxy.port_alias</string>
    <string>--listen-host</string>
    <string>{alias_ip}</string>
    <string>--listen-port</string>
    <string>{alias_port}</string>
    <string>--target-host</string>
    <string>{target_host}</string>
    <string>--target-port</string>
    <string>{target_port}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/var/log/aiproxy-tokensaver-http.log</string>
  <key>StandardErrorPath</key>
  <string>/var/log/aiproxy-tokensaver-http.log</string>
</dict>
</plist>
"""
    try:
        LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
        LAUNCHD_PLIST.write_text(plist, encoding="utf-8")
        os.chmod(LAUNCHD_PLIST, 0o644)
    except PermissionError:
        return False, (
            f"need permission to write {LAUNCHD_PLIST}\n"
            "Re-run with sudo:\n"
            "  sudo .venv/bin/python -m aiproxy --install-hostname"
        )
    except OSError as e:
        return False, f"could not write {LAUNCHD_PLIST}: {e}"

    # Prefer modern launchctl; fall back to load/unload.
    domain = f"system/{LAUNCHD_LABEL}"
    subprocess.run(["launchctl", "bootout", domain], capture_output=True, check=False)
    boot = subprocess.run(
        ["launchctl", "bootstrap", "system", str(LAUNCHD_PLIST)],
        capture_output=True,
        text=True,
        check=False,
    )
    if boot.returncode != 0:
        subprocess.run(["launchctl", "unload", str(LAUNCHD_PLIST)], capture_output=True, check=False)
        load = subprocess.run(
            ["launchctl", "load", "-w", str(LAUNCHD_PLIST)],
            capture_output=True,
            text=True,
            check=False,
        )
        if load.returncode != 0:
            err = (boot.stderr or boot.stdout or load.stderr or load.stdout or "").strip()
            return False, f"wrote {LAUNCHD_PLIST} but could not load service: {err}"
    else:
        subprocess.run(["launchctl", "enable", domain], capture_output=True, check=False)
        subprocess.run(
            ["launchctl", "kickstart", "-k", domain],
            capture_output=True,
            check=False,
        )

    return True, (
        f"port alias service {LAUNCHD_LABEL}: {alias_ip}:{alias_port} → "
        f"{target_host}:{target_port}"
    )


def _install_windows_portproxy(
    *,
    alias_ip: str,
    alias_port: int,
    target_host: str,
    target_port: int,
) -> tuple[bool, str]:
    # Remove any prior mapping for this listen address/port, then add.
    subprocess.run(
        [
            "netsh",
            "interface",
            "portproxy",
            "delete",
            "v4tov4",
            f"listenaddress={alias_ip}",
            f"listenport={alias_port}",
        ],
        capture_output=True,
        check=False,
    )
    add = subprocess.run(
        [
            "netsh",
            "interface",
            "portproxy",
            "add",
            "v4tov4",
            f"listenaddress={alias_ip}",
            f"listenport={alias_port}",
            f"connectaddress={target_host}",
            f"connectport={target_port}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if add.returncode != 0:
        err = (add.stderr or add.stdout or "").strip()
        return False, (
            f"netsh portproxy failed: {err}\n"
            "Run elevated PowerShell:\n"
            f"  netsh interface portproxy add v4tov4 "
            f"listenaddress={alias_ip} listenport={alias_port} "
            f"connectaddress={target_host} connectport={target_port}"
        )
    return True, f"portproxy {alias_ip}:{alias_port} → {target_host}:{target_port}"


def _install_linux_systemd(
    *,
    alias_ip: str,
    alias_port: int,
    target_host: str,
    target_port: int,
    python_exe: str,
) -> tuple[bool, str]:
    if shutil.which("systemctl") is None:
        return False, (
            "systemd not available — start the alias manually as root:\n"
            f"  {python_exe} -m aiproxy.port_alias "
            f"--listen-host {alias_ip} --listen-port {alias_port} "
            f"--target-host {target_host} --target-port {target_port}"
        )

    py = str(Path(python_exe).resolve())
    unit_path = Path("/etc/systemd/system/aiproxy-tokensaver-http.service")
    unit = f"""[Unit]
Description=aiproxy tokensaver.local port alias
After=network.target

[Service]
ExecStart={py} -m aiproxy.port_alias --listen-host {alias_ip} --listen-port {alias_port} --target-host {target_host} --target-port {target_port}
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
"""
    try:
        unit_path.write_text(unit, encoding="utf-8")
    except PermissionError:
        return False, (
            f"need permission to write {unit_path}\n"
            "Re-run with sudo:\n"
            "  sudo .venv/bin/python -m aiproxy --install-hostname"
        )
    except OSError as e:
        return False, f"could not write {unit_path}: {e}"

    cmds = [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "--now", "aiproxy-tokensaver-http.service"],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            return False, f"wrote {unit_path} but {' '.join(cmd)} failed: {err}"

    return True, (
        f"systemd aiproxy-tokensaver-http: {alias_ip}:{alias_port} → "
        f"{target_host}:{target_port}"
    )


def print_install_help(
    hostname: str,
    *,
    dashboard_port: int = 8081,
    alias_ip: str = DEFAULT_ALIAS_IP,
    alias_port: int = DEFAULT_ALIAS_PORT,
) -> None:
    path = hosts_path()
    line = hosts_line(hostname, alias_ip)
    pretty = f"http://{hostname}/" if alias_port == 80 else f"http://{hostname}:{alias_port}/"
    print(f"Memorable dashboard URL: {pretty}")
    print(f"  (forwards {alias_ip}:{alias_port} → 127.0.0.1:{dashboard_port})")
    print(f"Hosts file: {path}")
    if hostname_configured(hostname, path, alias_ip=alias_ip):
        print(f"Hosts entry OK — finish with port alias if needed")
    else:
        print("Not configured yet. One-time install:")
        if platform.system() == "Windows":
            print(f'  # elevated PowerShell')
            print(f'  .\\.venv\\Scripts\\python.exe -m aiproxy --install-hostname')
        else:
            print("  sudo .venv/bin/python -m aiproxy --install-hostname")
            print(f"  # hosts line: {line}")
    print()
    print("Direct URL (always works, no alias):")
    print(f"  http://127.0.0.1:{dashboard_port}/")
