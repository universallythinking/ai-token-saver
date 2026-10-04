# aiproxy on Windows

Local proxy between **Cursor** / **Claude Code** and the model APIs. Strips bulky JSON context to save tokens, then forwards upstream.

**Dashboard:** [http://127.0.0.1:8081/](http://127.0.0.1:8081/)

> **Python note:** Use the **Windows** Python launcher or `python.exe` to create the venv. After that, always run aiproxy with **`.venv\Scripts\python.exe`** (not a random global `python`) so deps resolve.
>
> macOS / Linux instructions: [README.md](README.md)

---

## 1. Install (once)

**Easiest:** double-click [`start-windows.bat`](start-windows.bat)

It opens a console, installs deps if needed, asks **Cursor / Claude / Both**, dry-run yes/no, then starts the proxy.

Or manually in **PowerShell**:

```powershell
cd $HOME\code\aiproxy
# adjust path if your clone lives elsewhere
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .
```

If `python` is missing, try:

```powershell
py -3 -m venv .venv
```

**Optional activate** (then bare `python` is the venv):

```powershell
.\.venv\Scripts\Activate.ps1
```

If execution policy blocks activate:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Safest without activate:

```powershell
.\.venv\Scripts\python.exe -m aiproxy --dry-run
```

Print ready-to-copy snippets:

```powershell
.\.venv\Scripts\python.exe -m aiproxy --print-cursor-settings
.\.venv\Scripts\python.exe -m aiproxy --print-claude-settings
```

---

## 2. Pick a mode

| Mode | Command | Best for | CA cert needed? |
|------|---------|----------|-----------------|
| **reverse** (default) | `.\.venv\Scripts\python.exe -m aiproxy` | Cursor BYOK **and** Claude Code (JSON strip) | No |
| **openai** | `...\python.exe -m aiproxy --mode openai` | Cursor OpenAI Base URL / BYOK only | No |
| **anthropic** | `...\python.exe -m aiproxy --mode anthropic` | Claude Code via `ANTHROPIC_BASE_URL` only | No |
| **mitm** | `...\python.exe -m aiproxy --mode mitm` | Inspect all HTTPS (Cursor agent = protobuf — little stripping) | Yes |

**Recommendation**

- Claude Code and/or Cursor with your own OpenAI key → **reverse** (default)
- Avoid **mitm** unless you need raw intercept; default Cursor models won’t strip well

Dry-run first (logs savings, does **not** change bodies):

```powershell
.\.venv\Scripts\python.exe -m aiproxy --dry-run
```

When you’re happy with the dashboard:

```powershell
.\.venv\Scripts\python.exe -m aiproxy
```

---

## 3. Connect Cursor (OpenAI BYOK — recommended)

Default Cursor models talk protobuf to Cursor’s servers — this proxy **cannot strip those**. Use an OpenAI-compatible model with a Base URL instead.

**Terminal 1**

```powershell
cd $HOME\code\aiproxy
.\.venv\Scripts\python.exe -m aiproxy --dry-run
```

**Cursor**

1. Settings → **Models**
2. OpenAI API Key → your key
3. OpenAI **Base URL** → `http://127.0.0.1:8080/v1`
4. Select an OpenAI-compatible model
5. Chat. Watch [http://127.0.0.1:8081/](http://127.0.0.1:8081/)

No CA install. Leave `http.proxy` **unset**.

---

## 4. Connect Claude Code (recommended)

**Terminal 1 — start reverse proxy**

```powershell
cd $HOME\code\aiproxy
.\.venv\Scripts\python.exe -m aiproxy --dry-run
```

**Terminal 2 — run Claude through it**

```powershell
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:8080"
# keep your existing key if you use one:
# $env:ANTHROPIC_API_KEY = "sk-ant-..."
claude
```

**cmd.exe**

```bat
set ANTHROPIC_BASE_URL=http://127.0.0.1:8080
claude
```

Inside Claude, run `/status` and confirm the base URL is `http://127.0.0.1:8080`.

**Persist for every Claude session** — `%USERPROFILE%\.claude\settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8080"
  }
}
```

Your API key stays wherever you already keep it (`ANTHROPIC_API_KEY` or Claude login). aiproxy forwards `x-api-key` / `Authorization` unchanged.

---

## 5. Verify it’s working

| Check | Expected |
|-------|----------|
| Dashboard | [http://127.0.0.1:8081/](http://127.0.0.1:8081/) loads |
| Send a chat | **Requested** counters rise |
| Strip rules fire | **Saved** tokens/chars > 0 (or notes in dry-run logs) |
| `logs\` | `*.meta.json` before/after dumps when bodies change |

Safe rollout:

1. `--dry-run` → watch dashboard  
2. Tune `config.yaml` → `strip` if needed  
3. Restart **without** `--dry-run` to forward stripped bodies  

---

## 6. MITM (Cursor default models, no BYOK)

Use this when you don’t have OpenAI BYOK. aiproxy streams SSE/Connect responses immediately so chats can reply (mitmproxy’s default body buffering used to stall them). Agent request bodies are still mostly protobuf — limited stripping.

**Start**

```powershell
.\.venv\Scripts\python.exe -m aiproxy --mode mitm --dry-run
```

First run creates the mitmproxy CA at:

`%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.pem`

**Trust the CA (once)**

1. Double-click `%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.cer`  
   (if only `.pem` exists, rename/copy to `.cer` or open via certmgr)
2. **Install Certificate…** → **Local Machine** (or Current User)
3. Place in **Trusted Root Certification Authorities**
4. Finish

Or PowerShell (Admin):

```powershell
Import-Certificate -FilePath "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.cer" `
  -CertStoreLocation Cert:\LocalMachine\Root
```

**Point Cursor at the proxy**

`Ctrl+Shift+P` → **Open User Settings (JSON)** — typically:

`%APPDATA%\Cursor\User\settings.json`

```json
{
  "http.proxy": "http://127.0.0.1:8080",
  "http.proxySupport": "override",
  "http.proxyStrictSSL": false,
  "cursor.general.disableHttp2": true
}
```

Fully quit Cursor, then relaunch from a shell with the CA for Node:

**PowerShell**

```powershell
$env:NODE_EXTRA_CA_CERTS = "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.pem"
Start-Process "$env:LOCALAPPDATA\Programs\cursor\Cursor.exe"
```

Path may vary (e.g. Installer vs Store). If `Start-Process` fails, start Cursor from the Start menu after setting `NODE_EXTRA_CA_CERTS` in the same session / user env vars.

**Claude via MITM**

```powershell
$env:HTTPS_PROXY = "http://127.0.0.1:8080"
$env:HTTP_PROXY = "http://127.0.0.1:8080"
$env:NODE_EXTRA_CA_CERTS = "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.pem"
claude
```

If chats send but never reply, turn MITM off (remove `http.proxy*`) and use the reverse-proxy path instead.

---

## 7. Ports & files

| What | Where |
|------|--------|
| Proxy | `127.0.0.1:8080` |
| Dashboard | `127.0.0.1:8081` |
| Config | `config.yaml` |
| Stats (persisted across launches) | `logs\stats.json` |
| Body dumps | `logs\` |
| mitmproxy CA | `%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.pem` |
| Cursor settings | `%APPDATA%\Cursor\User\settings.json` |
| Claude settings | `%USERPROFILE%\.claude\settings.json` |

---

## 8. Firewall / port already in use

If start fails with “address already in use”:

```powershell
netstat -ano | findstr :8080
```

Stop the other process, or change ports in `config.yaml` (`listen_port`, `dashboard_port`).

### Memorable dashboard URL

Default alias is **http://tokensaver.local/** (no port). Install once from an **Administrator** PowerShell in the project root — hosts entry + startup task that forwards `:80` using `dashboard_port` from `config.yaml` (auto-detected if the port changes later):

```powershell
cd C:\path\to\ai-token-saver
# Prefer the absolute path (relative .\ .venv\… under elevation is easy to get wrong)
& "$(Get-Location)\.venv\Scripts\python.exe" -m aiproxy --install-hostname
```

Or:

```powershell
& "C:\path\to\ai-token-saver\.venv\Scripts\python.exe" -m aiproxy --install-hostname `
  -c "C:\path\to\ai-token-saver\config.yaml"
```

Direct URL still works: `http://127.0.0.1:8081/` · ops board: `http://tokensaver.local/v2`

Allow local access if Windows Firewall prompts — aiproxy only needs **inbound** on `127.0.0.1` (loopback); no public exposure required.

---

## 9. Quick reference

```powershell
cd $HOME\code\aiproxy
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# reverse proxy (Cursor BYOK + Claude) — default
.\.venv\Scripts\python.exe -m aiproxy --dry-run

# Claude Code
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:8080"
claude

# Cursor: Settings → Models → OpenAI Base URL = http://127.0.0.1:8080/v1

# snippets
.\.venv\Scripts\python.exe -m aiproxy --print-cursor-settings
.\.venv\Scripts\python.exe -m aiproxy --print-claude-settings

# memorable dashboard URL (once; Admin PowerShell; use absolute path)
& "$(Get-Location)\.venv\Scripts\python.exe" -m aiproxy --install-hostname
# → http://tokensaver.local/  and  http://tokensaver.local/v2

# tests
.\.venv\Scripts\python.exe -m aiproxy.test_stripper
```
