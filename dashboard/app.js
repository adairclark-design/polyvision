/* ── PolyVision Command Center — Application Logic ─────────────────────────── */
const btnHudUpgrade = $('btnHudUpgrade');


// ── PRO Subscription Status ───────────────────────────────────────────────────
// Cached after first fetch; reloaded on every page load.
window.isPro = function () {
  // Fast path: use cached value loaded on init
  if (state.isProUser !== undefined) return state.isProUser;
  // Fallback: check Clerk publicMetadata (set manually for grandfathered users)
  return !!(window.Clerk?.user?.publicMetadata?.tier === 'PRO');
};


async function loadProStatus() {
  const user = window.Clerk?.user;
  if (!user) { state.isProUser = false; return; }
  try {
    const resp = await fetch(`${BRAIN_URL}/subscription/status?clerk_user_id=${encodeURIComponent(user.id)}`,
      { signal: AbortSignal.timeout(8000) });
    if (resp.ok) {
      const data = await resp.json();
      state.isProUser = data.is_pro === true;
      state.discordLinked = !!data.discord_user_id;

      // If explicitly redirected here with a session_id, verify it immediately
      const params = new URLSearchParams(window.location.search);
      const sessionId = params.get('session_id');
      if (sessionId && !state.isProUser) {
        console.log('Detected session_id in URL, triggering direct activation...');
        activateProDirectly(sessionId);
      }
      // Show/hide HUD upgrade button based on tier
      const upgradeBtn = $('btnHudUpgrade');
      if (upgradeBtn) upgradeBtn.style.display = state.isProUser ? 'none' : '';
      // Render Discord link button if PRO
      renderDiscordLinkButton();
      // Show welcome toast if returning from Stripe
      const params = new URLSearchParams(window.location.search);
      if (params.get('upgrade') === 'success') {
        showUpgradeWelcome();
      }
    }
  } catch (_) {
    // Brain offline — fall back to Clerk metadata
    state.isProUser = !!(window.Clerk?.user?.publicMetadata?.tier === 'PRO');
  }
}


// ── Discord OAuth Link UI ─────────────────────────────────────────────────────
// ── PRO Activation Poller ─────────────────────────────────────────────────────
// Called when user returns from Stripe with ?upgrade=success but Brain hasn't
// confirmed PRO yet (webhook timing lag). Polls every 2s for up to 30s.
/**
 * Verifies payment via the backend /stripe/confirm-checkout endpoint.
 * This activates PRO status immediately without waiting for a webhook.
 */
async function activateProDirectly(sessionId) {
  const user = window.Clerk?.user;
  if (!user) return;
  try {
    const resp = await fetch(`${BRAIN_URL}/stripe/confirm-checkout`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        clerk_user_id: user.id
      })
    });
    const data = await resp.json();
    if (data.is_pro) {
      state.isProUser = true;
      const upgradeBtn = $('btnHudUpgrade');
      if (upgradeBtn) upgradeBtn.style.display = 'none';
      showUpgradeWelcome();
    }
  } catch (e) {
    console.error('Failed direct PRO activation:', e);
  }
}

function showUpgradeWelcome() {
  // Show a high-impact welcome message using existing toast system
  showToast({
    tier: 'WHALE',
    whale: { handle: '🎉 Welcome to PolyVision PRO!' },
    market: 'All PRO features are now unlocked. Your feed just expanded to 50 events.',
    outcome: 'YES',
    usdValue: 0,
    timestamp: Date.now()
  });

  // Confetti!
  if (typeof confetti === 'function') {
    confetti({
      particleCount: 150,
      spread: 70,
      origin: { y: 0.6 }
    });
  }
}

async function pollForProStatus(attempts = 0) {
  if (attempts >= 15) {
    console.warn('[PolyVision] PRO polling timed out. Try refreshing.');
    return;
  }
  if (attempts === 0) {
    const existing = document.getElementById('proActivatingBanner');
    if (!existing) {
      const banner = document.createElement('div');
      banner.id = 'proActivatingBanner';
      banner.style.cssText = [
        'position:fixed;bottom:24px;left:50%;transform:translateX(-50%)',
        'background:#161b22;border:1px solid rgba(0,255,163,0.4);border-radius:12px',
        'padding:12px 24px;color:#e6edf3;font-size:14px;font-weight:600;z-index:9999',
        'box-shadow:0 4px 24px rgba(0,0,0,0.5);display:flex;align-items:center;gap:10px',
        'animation:fadeUp 0.4s ease',
      ].join(';');
      banner.innerHTML = '<span style="color:#00ffa3">⏳</span> Payment confirmed — activating your PRO account…';
      document.body.appendChild(banner);
    }
  }
  await new Promise(r => setTimeout(r, 2000));
  const user = window.Clerk?.user;
  if (!user) return;
  try {
    const resp = await fetch(
      BRAIN_URL + '/subscription/status?clerk_user_id=' + encodeURIComponent(user.id),
      { signal: AbortSignal.timeout(5000) }
    );
    if (resp.ok) {
      const data = await resp.json();
      if (data.is_pro === true) {
        state.isProUser = true;
        state.discordLinked = !!data.discord_user_id;
        history.replaceState({}, '', location.pathname);
        document.getElementById('proActivatingBanner')?.remove();
        const upgradeBtn = document.getElementById('btnHudUpgrade');
        if (upgradeBtn) upgradeBtn.style.display = 'none';
        renderDiscordLinkButton();
        showToast({ tier: 'WHALE', whale: { handle: '🎉 Welcome to PolyVision PRO!' },
          market: 'All PRO features are now unlocked. Your feed just expanded to 50 events.',
          outcome: 'YES', usdValue: 0, timestamp: Date.now() });
        if (!state.discordLinked) {
          setTimeout(() => {
            showToast({ tier: 'STANDARD', whale: { handle: '💙 Link Your Discord' },
              market: 'Connect Discord to get exclusive PRO channel access.',
              outcome: 'YES', usdValue: 0, timestamp: Date.now() });
          }, 4000);
        }
        return;
      }
    }
  } catch (_) {}
  pollForProStatus(attempts + 1);
}

// ── Discord OAuth Link UI ─────────────────────────────────────────────────────
function renderDiscordLinkButton() {
  const container = $('discordLinkContainer');
  if (!container) return;
  if (!state.isProUser) { container.style.display = 'none'; return; }
  container.style.display = '';
  container.innerHTML = state.discordLinked
    ? `<a class="discord-linked" href="https://discord.gg/XQWgDqdVmK" target="_blank" rel="noopener" title="Open the PolyVision Discord server">💙 Discord Linked ✅ — Join Server</a>`
    : `<button class="btn-discord-link" onclick="openDiscordOAuth()">💙 Link Discord — Get PRO Channel Access</button>`;


  // Always show Manage Subscription for PRO users below the Discord button
  container.innerHTML += `<button class="btn-manage-sub" onclick="openBillingPortal()">⚙️ Manage Subscription</button>`;
}


window.openDiscordOAuth = function () {
  const user = window.Clerk?.user;
  if (!user) return;
  const url = `${BRAIN_URL}/discord/oauth/start?clerk_user_id=${encodeURIComponent(user.id)}`;
  const popup = window.open(url, 'discord_oauth', 'width=500,height=700,scrollbars=yes');
  if (!popup) alert('Please allow popups to link your Discord account.');
};
