"""Shared setup wizard modal HTML/CSS/JS for dashboard pages."""

SETUP_MODAL_ASSETS = r"""
<style>
  .ts-setup-overlay {
    position: fixed; inset: 0; z-index: 1000;
    background: rgba(12,14,18,0.55);
    display: none; align-items: center; justify-content: center;
    padding: 20px;
    backdrop-filter: blur(4px);
  }
  .ts-setup-overlay.open { display: flex; }
  .ts-setup-modal {
    width: min(520px, 100%);
    max-height: min(90vh, 720px);
    overflow: auto;
    background: var(--panel, #fff);
    color: var(--ink, #12201c);
    border: 1px solid var(--line, #d7e0db);
    border-radius: 12px;
    box-shadow: 0 24px 64px rgba(0,0,0,0.28);
    padding: 22px 22px 18px;
  }
  .ts-setup-modal h1 {
    margin: 0 0 6px;
    font-size: 1.25rem;
    letter-spacing: -0.03em;
  }
  .ts-setup-modal .ts-step {
    font-family: ui-monospace, "JetBrains Mono", "IBM Plex Mono", monospace;
    font-size: 0.68rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted, #6b7a73);
    margin-bottom: 10px;
  }
  .ts-setup-modal .ts-lede {
    margin: 0 0 16px;
    color: var(--ink-soft, #3a4a44);
    font-size: 0.9rem;
    line-height: 1.4;
  }
  .ts-choices { display: grid; gap: 8px; margin-bottom: 14px; }
  .ts-choice {
    text-align: left;
    border: 1px solid var(--line, #d7e0db);
    background: transparent;
    color: inherit;
    border-radius: 10px;
    padding: 12px 12px;
    cursor: pointer;
    font: inherit;
  }
  .ts-choice:hover { border-color: var(--accent, #0f7a5f); }
  .ts-choice.active {
    border-color: var(--accent, #0f7a5f);
    background: var(--accent-soft, rgba(15,122,95,0.12));
  }
  .ts-choice:disabled { opacity: 0.45; cursor: not-allowed; }
  .ts-choice strong { display: block; font-size: 0.95rem; margin-bottom: 2px; }
  .ts-choice span { display: block; font-size: 0.78rem; color: var(--muted, #6b7a73); }
  .ts-yn { display: flex; gap: 8px; margin-bottom: 14px; }
  .ts-yn button {
    flex: 1;
    border: 1px solid var(--line, #d7e0db);
    background: transparent;
    color: inherit;
    border-radius: 10px;
    padding: 12px;
    cursor: pointer;
    font: inherit;
    font-weight: 600;
  }
  .ts-yn button.active {
    background: var(--ink, #12201c);
    color: var(--paper, #f4f7f5);
    border-color: transparent;
  }
  .ts-note {
    font-size: 0.82rem;
    color: var(--ink-soft, #3a4a44);
    background: var(--line-soft, #e8efeb);
    border-radius: 10px;
    padding: 12px;
    margin-bottom: 14px;
    line-height: 1.4;
    white-space: pre-wrap;
  }
  .ts-actions { display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; align-items: center; }
  .ts-actions button {
    font: inherit;
    font-weight: 600;
    font-size: 0.8rem;
    border-radius: 10px;
    padding: 10px 14px;
    cursor: pointer;
  }
  .ts-actions .ghost {
    background: transparent;
    border: 1px solid var(--line, #d7e0db);
    color: var(--ink-soft, #3a4a44);
  }
  .ts-actions .primary {
    background: var(--accent, #0f7a5f);
    color: #fff;
    border: 0;
  }
  .ts-actions .primary:disabled { opacity: 0.5; cursor: wait; }
  .ts-footer {
    display: flex; align-items: center; justify-content: space-between;
    gap: 12px; flex-wrap: wrap; margin-top: 4px;
  }
  .ts-noshow {
    display: inline-flex; align-items: center; gap: 8px;
    font-size: 0.8rem; color: var(--ink-soft, #3a4a44);
    user-select: none; cursor: pointer;
  }
  .ts-noshow input { width: 15px; height: 15px; accent-color: var(--accent, #0f7a5f); }
  .ts-noshow.is-disabled {
    opacity: 0.45; cursor: not-allowed; color: var(--muted, #6b7a73);
  }
  .ts-noshow.is-disabled input { cursor: not-allowed; }
  .ts-err { color: #b42318; font-size: 0.8rem; margin: 0 0 10px; min-height: 1.2em; }
</style>
<div class="ts-setup-overlay" id="ts-setup" role="dialog" aria-modal="true" aria-labelledby="ts-setup-title">
  <div class="ts-setup-modal">
    <div class="ts-step" id="ts-setup-step">Step 1 of 4</div>
    <h1 id="ts-setup-title">Token Saver setup</h1>
    <p class="ts-lede" id="ts-setup-lede"></p>
    <div id="ts-setup-body"></div>
    <p class="ts-err" id="ts-setup-err"></p>
    <div class="ts-footer">
      <label class="ts-noshow is-disabled" id="ts-setup-noshow-wrap" title="Save settings once to enable">
        <input type="checkbox" id="ts-setup-noshow" disabled />
        <span>Don't show this again</span>
      </label>
      <div class="ts-actions">
        <button type="button" class="ghost" id="ts-setup-back">Back</button>
        <button type="button" class="ghost" id="ts-setup-skip">Not now</button>
        <button type="button" class="primary" id="ts-setup-next">Continue</button>
      </div>
    </div>
  </div>
</div>
<script>
(function () {
  const overlay = document.getElementById("ts-setup");
  if (!overlay) return;
  const stepEl = document.getElementById("ts-setup-step");
  const ledeEl = document.getElementById("ts-setup-lede");
  const bodyEl = document.getElementById("ts-setup-body");
  const errEl = document.getElementById("ts-setup-err");
  const backBtn = document.getElementById("ts-setup-back");
  const skipBtn = document.getElementById("ts-setup-skip");
  const nextBtn = document.getElementById("ts-setup-next");
  const noShowWrap = document.getElementById("ts-setup-noshow-wrap");
  const noShowCb = document.getElementById("ts-setup-noshow");

  let payload = null;
  let step = 0; // 0 app, 1 dry-run, 2 ca, 3 save
  let app = 1;
  let dryRun = true;
  let savePrefs = true;
  let quickStart = false;
  let skipSetup = false;

  function openModal() { overlay.classList.add("open"); }
  function closeModal() {
    overlay.classList.remove("open");
    try {
      const u = new URL(location.href);
      u.searchParams.delete("setup");
      history.replaceState({}, "", u.pathname + u.search + u.hash);
    } catch (_) {}
  }

  function setErr(msg) { errEl.textContent = msg || ""; }

  function canSkipSetup() {
    const savedOk = !!(payload && payload.saved && payload.saved.exists);
    // Enabled once prefs exist, or when this apply will save them.
    if (savedOk) return true;
    if (step === 3 && savePrefs) return true;
    return false;
  }

  function syncNoShow() {
    const ok = canSkipSetup();
    noShowCb.disabled = !ok;
    noShowWrap.classList.toggle("is-disabled", !ok);
    noShowWrap.title = ok
      ? "Next launch opens the dashboard without this wizard"
      : "Save settings once to enable";
    if (!ok) {
      noShowCb.checked = false;
      skipSetup = false;
    } else {
      noShowCb.checked = !!skipSetup;
    }
  }

  function savedSummary() {
    const s = (payload && payload.saved) || {};
    if (!s.exists) return "";
    const labels = { 1: "Cursor", 2: "Claude Code", 3: "Both" };
    const appLabel = labels[s.app] || ("app " + s.app);
    const mode = s.dry_run ? "dry-run" : "live strip";
    return appLabel + " · " + mode + (s.saved_at ? " · saved " + s.saved_at : "");
  }

  async function applySettings() {
    nextBtn.disabled = true;
    nextBtn.textContent = "Applying…";
    const useQuick = app === 4;
    const saved = (payload && payload.saved) || {};
    const wantSkip = canSkipSetup() && !!noShowCb.checked;
    try {
      const res = await fetch("/api/setup", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          app: useQuick ? 4 : app,
          dry_run: useQuick ? !!saved.dry_run : dryRun,
          save: useQuick ? true : savePrefs,
          restart: true,
          quick_start: useQuick,
          skip_setup: wantSkip,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || ("setup " + res.status));
      closeModal();
      const meta = document.getElementById("meta");
      if (meta) meta.innerHTML = '<span class="live"></span>' + (data.message || "Settings applied");
    } catch (e) {
      setErr(String(e.message || e));
      nextBtn.disabled = false;
      nextBtn.textContent = useQuick ? "Apply saved settings" : "Apply & restart";
    }
  }

  function render() {
    setErr("");
    backBtn.style.visibility = step === 0 ? "hidden" : "visible";
    nextBtn.disabled = false;
    syncNoShow();
    if (step === 0) {
      stepEl.textContent = quickStart ? "Quick start" : "Step 1 of 4";
      ledeEl.textContent = "Which app are you connecting?";
      const choices = (payload && payload.choices) || [];
      bodyEl.innerHTML = '<div class="ts-choices" id="ts-app-choices"></div>';
      const root = document.getElementById("ts-app-choices");
      choices.forEach((c) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "ts-choice" + (app === c.id ? " active" : "");
        btn.disabled = c.id === 4 && !c.available;
        let detail = c.detail;
        if (c.id === 4) {
          detail = c.available
            ? ("Use saved: " + savedSummary())
            : "Save settings after setup — none saved yet";
        }
        btn.innerHTML = "<strong>" + c.id + ") " + c.label + "</strong><span>" + detail + "</span>";
        btn.onclick = () => { app = c.id; quickStart = c.id === 4; render(); };
        root.appendChild(btn);
      });
      if (quickStart) {
        const sum = savedSummary();
        if (sum) {
          bodyEl.insertAdjacentHTML(
            "beforeend",
            '<div class="ts-note">Applies saved settings now — no further prompts.\n\n' + sum + "</div>"
          );
        }
        nextBtn.textContent = "Apply saved settings";
      } else {
        nextBtn.textContent = "Continue";
      }
    } else if (step === 1) {
      stepEl.textContent = "Step 2 of 4";
      ledeEl.textContent = "Dry-run first? (log savings, do not rewrite bodies)";
      bodyEl.innerHTML =
        '<div class="ts-yn">' +
          '<button type="button" data-v="1" class="' + (dryRun ? "active" : "") + '">Yes — dry-run</button>' +
          '<button type="button" data-v="0" class="' + (!dryRun ? "active" : "") + '">No — live strip</button>' +
        '</div>';
      bodyEl.querySelectorAll("button").forEach((b) => {
        b.onclick = () => { dryRun = b.dataset.v === "1"; render(); };
      });
      nextBtn.textContent = "Continue";
    } else if (step === 2) {
      stepEl.textContent = "Step 3 of 4";
      const mitm = !(app === 2 || (quickStart && payload && payload.prefs && payload.prefs.mode === "anthropic"));
      const hints = (payload && payload.hints) || {};
      if (!mitm) {
        ledeEl.textContent = "Certificate";
        const claude = hints.claude_env ||
          ('export ANTHROPIC_BASE_URL="' + ((payload && payload.proxy_url) || "http://127.0.0.1:8080") + '"\nclaude');
        bodyEl.innerHTML = '<div class="ts-note">Claude Code reverse-proxy mode needs no mitmproxy CA trust.\n\nPoint Claude at:\n  ' +
          claude.replace(/\n/g, "\n  ") + "</div>";
      } else {
        ledeEl.textContent = "Trust mitmproxy CA (once) for Cursor HTTPS intercept";
        const caNote = hints.mitm_ca ||
          ('If replies fail with certificate errors, trust the CA once.\nCA: ' +
            ((payload && payload.ca_path) || "~/.mitmproxy/mitmproxy-ca-cert.pem"));
        bodyEl.innerHTML = '<div class="ts-note">' + caNote + "</div>";
      }
      nextBtn.textContent = "Continue";
    } else {
      stepEl.textContent = "Step 4 of 4";
      ledeEl.textContent = "Save these settings for Quick start next time?";
      const restartNote = ((payload && payload.hints && payload.hints.restart_note) ||
        "Apply will write launcher prefs and restart the proxy.");
      bodyEl.innerHTML =
        '<div class="ts-yn">' +
          '<button type="button" data-v="1" class="' + (savePrefs ? "active" : "") + '">Yes — save</button>' +
          '<button type="button" data-v="0" class="' + (!savePrefs ? "active" : "") + '">No — just this session</button>' +
        '</div>' +
        '<div class="ts-note">' + restartNote + "</div>";
      bodyEl.querySelectorAll(".ts-yn button").forEach((b) => {
        b.onclick = () => { savePrefs = b.dataset.v === "1"; render(); };
      });
      nextBtn.textContent = "Apply & restart";
    }
    syncNoShow();
  }

  async function load(opts) {
    const force = !!(opts && opts.force);
    const res = await fetch("/api/setup", { cache: "no-store" });
    if (!res.ok) throw new Error("setup " + res.status);
    payload = await res.json();
    step = 0;
    quickStart = false;
    savePrefs = true;
    skipSetup = !!(payload.skip_setup && payload.can_skip_setup);
    if (payload.prefs) {
      app = Number(payload.prefs.app || 1);
      if (app === 4) app = 1;
      dryRun = !!payload.prefs.dry_run;
    } else {
      app = 1;
      dryRun = true;
    }
    // Auto-launch path: settings saved + "don't show again" — skip wizard.
    if (!force && payload.skip_setup && payload.can_skip_setup) {
      closeModal();
      return;
    }
    render();
    openModal();
  }

  noShowCb.onchange = () => {
    if (noShowCb.disabled) {
      noShowCb.checked = false;
      skipSetup = false;
      return;
    }
    skipSetup = !!noShowCb.checked;
  };

  backBtn.onclick = () => {
    if (step > 0) {
      step -= 1;
      if (step === 0) quickStart = app === 4;
      render();
    }
  };
  skipBtn.onclick = () => closeModal();
  nextBtn.onclick = async () => {
    setErr("");
    // Quick start (saved): apply immediately — skip dry-run / CA / save prompts.
    if (step === 0 && app === 4) {
      const qs = payload && payload.choices && payload.choices.find((c) => c.id === 4);
      if (!qs || !qs.available) {
        setErr("No saved settings yet. Pick Cursor, Claude Code, or Both once.");
        return;
      }
      await applySettings();
      return;
    }
    if (step < 3) {
      step += 1;
      render();
      return;
    }
    await applySettings();
  };

  // Manual Setup button always shows the wizard (even if skip_setup is on).
  window.TokenSaverSetup = {
    open: () => { step = 0; load({ force: true }).catch((e) => setErr(String(e))); },
  };

  const params = new URLSearchParams(location.search);
  const want = params.get("setup") === "1" || params.get("setup") === "true";
  if (want) {
    load({ force: false }).catch((e) => { openModal(); setErr(String(e)); });
  }
})();
</script>
"""
