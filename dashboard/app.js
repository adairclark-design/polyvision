/* ── PolyVision Command Center — Application Logic ─────────────────────────── */
'use strict';

// ── Data: Whale Personas ─────────────────────────────────────────────────────
const WHALES = [
  {
    id: 'oracle',
    handle: 'The Oracle of Oregon',
    avatar: 'assets/avatar_oracle.png',
    wallet: '0xDeAd...f1234',
    winRate: 0.92,
    roi30d: 0.38,
    roiAllTime: 1.22,
    totalVolume: 2_840_000,
    dominantCategory: 'US Politics',
    totalTrades: 187,
    badge: '92% Election Accuracy',
    sparkData: [0, 4, 2, 8, 6, 12, 9, 15, 11, 18, 14, 22, 19, 28, 24, 31, 27, 35, 30, 38],
    recentTrades: [
      { market: 'Will Fed cut rates in March 2026?', outcome: 'YES', size: 50000 },
      { market: 'Trump approval above 45% in April?', outcome: 'YES', size: 28000 },
      { market: 'Hamas ceasefire held through Feb?', outcome: 'NO', size: 17000 },
    ],
  },
  {
    id: 'strategist',
    handle: 'The Strategist of Chicago',
    avatar: 'assets/avatar_strategist.png',
    wallet: '0xC4f3...a901',
    winRate: 0.78,
    roi30d: 0.21,
    roiAllTime: 0.82,
    totalVolume: 1_520_000,
    dominantCategory: 'Crypto Markets',
    totalTrades: 214,
    badge: '78% ROI on Crypto Calls',
    sparkData: [10, 8, 12, 6, 15, 10, 18, 14, 20, 16, 22, 17, 25, 20, 28, 22, 26, 23, 29, 25],
    recentTrades: [
      { market: 'BTC above $100k by end of March?', outcome: 'YES', size: 75000 },
      { market: 'ETH ETF net inflow positive in Feb?', outcome: 'YES', size: 31000 },
      { market: 'Coinbase stock above $300?', outcome: 'NO', size: 22000 },
    ],
  },
  {
    id: 'pioneer',
    handle: 'The Pioneer of the Pacific Northwest',
    avatar: 'assets/avatar_pioneer.png',
    wallet: '0x9b2E...de45',
    winRate: 0.71,
    roi30d: 0.15,
    roiAllTime: 0.61,
    totalVolume: 980_000,
    dominantCategory: 'Global Events',
    totalTrades: 156,
    badge: '71% Cross-Market Win Rate',
    sparkData: [5, 7, 4, 9, 6, 11, 8, 13, 10, 15, 12, 17, 13, 18, 14, 20, 16, 21, 18, 22],
    recentTrades: [
      { market: 'NATO expansion before June 2026?', outcome: 'NO', size: 12000 },
      { market: 'UK inflation below 2% in Q1?', outcome: 'YES', size: 18500 },
      { market: 'Elon Musk DOGE role until July?', outcome: 'YES', size: 42000 },
    ],
  },
];

// ── Data: Market Templates ────────────────────────────────────────────────────
const MARKETS = [
  'Will the Fed cut rates in March 2026?',
  'BTC above $100k by end of April 2026?',
  'Trump approval rating above 48% in Q2?',
  'Will NVIDIA report record earnings in Q1?',
  'Will there be a US government shutdown in 2026?',
  'Will the S&P 500 reach 6,500 before June?',
  'ETH price above $5,000 by June 2026?',
  'Will Elon Musk resign from DOGE before July?',
  'Will inflation fall below 2.5% in March CPI?',
  'Will a ceasefire hold in Gaza through April?',
];

const REASONING_CHIPS = {
  firstTrade: '🕒 First trade in 3 weeks',
  rapidFire: '⚡ 3rd position in 4 hours',
  hedging: '🛡 Possible hedge play',
  highConviction: '💎 Max position size',
  averagingIn: '📈 Averaging into position',
  insidersWindow: '🧠 Insider watch window',
  bigWall: '🧱 Large order wall placed',
};

// ── State ─────────────────────────────────────────────────────────────────────
const state = {
  events: [],
  following: new Set(),   // event IDs being mock-followed
  portfolio: {
    totalPnl: 0,
    totalInvested: 0,
    trackCount: 0,
  },
  filters: { minSize: 500, side: 'all' },
  sortBy: 'newest',  // 'newest' | 'largest'
  todayCount: 0,
  todayVolume: 0,
};

// ── DOM refs ─────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const pulseFeed = $('pulseFeed');
const whaleCardsEl = $('whaleCards');
const hudRoi = $('hudRoi');
const hudPnl = $('hudPnl');
const hudTracked = $('hudTracked');
const liveCount = $('liveCount');
const totalVolume = $('totalVolume');
const whaleCount = $('whaleCount');
const modalOverlay = $('modalOverlay');
const modalContent = $('modalContent');
const toastContainer = $('toastContainer');
const btnHudUpgrade = $('btnHudUpgrade');

window.isPro = function () {
  return window.Clerk && window.Clerk.user && window.Clerk.user.publicMetadata && window.Clerk.user.publicMetadata.tier === 'PRO';
};

window.checkoutPro = function () {
  // Open the polished PRO upgrade modal instead of a bare redirect
  $('proUpgradeOverlay').style.display = 'flex';
};

window.closeProModal = function () {
  $('proUpgradeOverlay').style.display = 'none';
};

// Wire the "Go PRO" button inside the modal to do the Stripe redirect
document.addEventListener('DOMContentLoaded', () => {
  const stripeBtn = $('btnGoStripe');
  if (stripeBtn) {
    stripeBtn.onclick = () => {
      if (!window.Clerk || !window.Clerk.user) {
        alert('Please sign in first.');
        return;
      }
      const userId = window.Clerk.user.id;
      const stripeUrl = 'https://buy.stripe.com/9B66oH5b17Nn4Qgc9d0sU00';
      window.location.href = `${stripeUrl}?client_reference_id=${userId}`;
    };
  }
});



if (btnHudUpgrade) {
  btnHudUpgrade.onclick = window.checkoutPro;
}

// Formatting helpers
function fmt(n) { return '$' + n.toLocaleString('en-US', { maximumFractionDigits: 0 }); }
function fmtPct(n) { return (n >= 0 ? '+' : '') + (n * 100).toFixed(1) + '%'; }
function fmtPrice(p) {
  // p is a decimal 0.0–1.0; display as cents with edge-case labels
  const cents = Math.round(p * 100);
  if (cents <= 0) return '<1¢';
  if (cents >= 100) return '>99¢';
  return `${cents}¢`;
}
function fmtWinRate(wr, resolvedCount) {
  // wr: decimal 0.0–1.0 from DB; resolvedCount: optional count of resolved trades
  if (wr === null || wr === undefined) return 'New';
  const pct = Math.round(wr * 100);
  if (pct === 0 && resolvedCount === 0) return 'New';
  if (pct === 0 && !resolvedCount) return 'New';
  return `${pct}%`;
}

function timeAgo(ms) {
  const s = Math.floor((Date.now() - ms) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}
function randomBetween(a, b) { return a + Math.random() * (b - a); }
function pickRandom(arr) { return arr[Math.floor(Math.random() * arr.length)]; }
function calcConviction(whale, usdValue, totalTrades, totalVolumeUsd) {
  const baseWr = whale.winRate || 0.5;

  // 1. Calculate Historical Baseline
  // (subtract current trade to find their *historical* average before this trade)
  const pastVolume = Math.max(0, (totalVolumeUsd || usdValue) - usdValue);
  const pastTrades = Math.max(1, (totalTrades || 1) - 1);
  const avgTradeSize = pastVolume > 0 ? (pastVolume / pastTrades) : 10000;

  // 2. Calculate Size Ratio (how many times larger than average is this trade?)
  const sizeRatio = usdValue / avgTradeSize;

  // 3. Logarithmic Size Score (1x = ~0.5, 3x+ = 1.0, 0.2x = ~0.1)
  // Baseline log curve that scales gracefully
  let sizeScore = 0;
  if (sizeRatio >= 1) {
    // Scales from 0.5 at 1x to 1.0 at ~3x+
    sizeScore = 0.5 + Math.min(0.5, Math.log10(sizeRatio) * 0.8);
  } else {
    // Drops off quickly below 1x
    sizeScore = 0.5 * Math.max(0, sizeRatio);
  }

  // 4. Weighting (60% Size Anomaly, 40% Win Rate)
  const raw = (sizeScore * 0.6 + baseWr * 0.4) * 10;
  return Math.round(Math.min(Math.max(raw, 1), 10));
}
/** Returns data-driven reasoning chips based on actual event properties */
function generateChips(ev) {
  const chips = [];

  // 1. TRADE FREQUENCY — how many trades has this wallet had in the last 4 hours?
  const fourHoursAgo = Date.now() - 4 * 60 * 60 * 1000;
  const recentTrades = state.events.filter(
    e => e.whale?.wallet === ev.whale?.wallet && e.timestamp >= fourHoursAgo
  );
  if (recentTrades.length >= 3) {
    chips.push(`⚡ ${recentTrades.length} positions in 4 hours`);
  }

  // 2. INACTIVITY — has this wallet not appeared in the previous events (before now)?
  const priorTrades = state.events.filter(
    e => e.whale?.wallet === ev.whale?.wallet && e.id !== ev.id
  );
  if (priorTrades.length === 0 && ev.wallet_total_trades && ev.wallet_total_trades > 1) {
    // wallet has history but hasn't appeared in this session
    chips.push('🕒 Returning after inactivity');
  }

  // 3. MAX POSITION SIZE — is this one of the largest trades we've seen?
  const allSizes = state.events.map(e => e.usdValue).filter(Boolean);
  const p90 = allSizes.length >= 5
    ? allSizes.sort((a, b) => a - b)[Math.floor(allSizes.length * 0.9)]
    : 50000;
  if (ev.usdValue >= p90 && ev.usdValue >= 25000) {
    chips.push('📎 Max position size');
  }

  // 4. HIGH CONVICTION PRICE — betting near certainty (price < 10¢ or > 90¢)
  const priceCents = Math.round((ev.price || 0.5) * 100);
  if (priceCents <= 10) {
    chips.push('🎯 Long-shot contrarian bet');
  } else if (priceCents >= 90) {
    chips.push('🛡 High-conviction near-certainty');
  }

  // 5. HEDGE SIGNAL — betting NO when most recent same-market trades are YES
  const sameMarketEvents = state.events.filter(e => e.market === ev.market && e.id !== ev.id);
  const dominantOutcome = sameMarketEvents.length > 0
    ? (sameMarketEvents.filter(e => e.outcome === 'YES').length > sameMarketEvents.length / 2 ? 'YES' : 'NO')
    : null;
  if (dominantOutcome && ev.outcome !== dominantOutcome && sameMarketEvents.length >= 2) {
    chips.push('🛡 Possible hedge play');
  }

  // 6. LARGE ABSOLUTE POSITION — any trade > $50K is noteworthy
  if (ev.usdValue >= 50000 && !chips.includes('📎 Max position size')) {
    chips.push('🧱 Large order wall placed');
  }

  // Return up to 2 chips (prioritise the most interesting ones)
  return chips.slice(0, 2);
}

// ── Sparkline Chart (Chart.js) ────────────────────────────────────────────────
const sparklineCharts = {};
function drawSparkline(canvasId, data, color) {
  if (sparklineCharts[canvasId]) {
    sparklineCharts[canvasId].destroy();
  }
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const gradient = ctx.createLinearGradient(0, 0, 0, 40);
  gradient.addColorStop(0, color + '44');
  gradient.addColorStop(1, color + '00');

  sparklineCharts[canvasId] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: data.map((_, i) => i),
      datasets: [{
        data,
        borderColor: color,
        borderWidth: 1.5,
        backgroundColor: gradient,
        fill: true,
        tension: 0.4,
        pointRadius: 0,
      }],
    },
    options: {
      responsive: false,
      animation: { duration: 600 },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: { x: { display: false }, y: { display: false } },
    },
  });
}

// ── Build Event Card HTML ─────────────────────────────────────────────────────
function buildEventCard(ev) {
  // ── CLUSTER card ────────────────────────────────────────────────────────────
  if (ev.tier === 'CLUSTER') {
    const participants = ev.clusterParticipants || [];
    const chipsHtml = participants.map(p =>
      `<span class="cluster-participant-chip">🐋 ${p.handle}</span>`
    ).join('');
    const outcome = (ev.outcome || 'YES').toUpperCase();
    const sideClass = outcome === 'YES' ? 'yes' : 'no';
    const following = state.following.has(ev.id);

    return `
    <div class="event-card tier-cluster" id="card-${ev.id}" data-id="${ev.id}" onclick="openTradeModal('${ev.id}')">
      <span class="expand-hint">Click for details</span>
      <div class="card-top">
        <span style="font-size:26px;flex-shrink:0">🚨</span>
        <div class="card-meta">
          <div class="card-handle" style="color:#FFB800">MEGA-CLUSTER ALERT</div>
          <div class="card-time">${timeAgo(ev.timestamp)}</div>
        </div>
        <div class="card-badges">
          <span class="tier-badge CLUSTER">🚨 CLUSTER ×${ev.clusterCount || participants.length}</span>
        </div>
      </div>
      <div class="cluster-meta">
        <span class="cluster-icon">⚡</span>
        <div>
          <div class="cluster-headline">${ev.clusterCount || participants.length} Whales · Same Side · 15-Min Window</div>
          <div class="cluster-sub">${outcome} on "${ev.market}" · Total size: ${fmt(ev.usdValue)}</div>
        </div>
      </div>
      <div class="cluster-participants">${chipsHtml}</div>
      <div class="card-market">
        <strong>${outcome === 'YES' ? '<span style="color:var(--mint)">YES</span>' : '<span style="color:var(--rose)">NO</span>'}</strong>
        on "${ev.market}"
      </div>
      <div class="card-actions">
        <button class="btn-card btn-mock-follow ${following ? 'following' : ''}"
                id="follow-${ev.id}"
                onclick="toggleFollow('${ev.id}', event)">
          ${following ? '✅ Following' : '+ Mock Follow This Trade'}
        </button>
        <button class="btn-card btn-dismiss" onclick="dismissCard('${ev.id}', event)">✕</button>
      </div>
    </div>`;
  }

  // ── Standard / Whale card ────────────────────────────────────────────────────
  const isYes = ev.outcome === 'YES';
  const tierClass = ev.tier.toLowerCase();
  const sideClass = isYes ? 'yes' : 'no';
  const conviction = calcConviction(ev.whale, ev.usdValue, ev.wallet_total_trades, ev.wallet_total_volume);
  const chips = ev.reasoningChips;
  const following = state.following.has(ev.id);

  const dots = Array.from({ length: 10 }, (_, i) =>
    `<span class="dot ${i < conviction ? 'filled' + (conviction <= 5 ? ' risk' : '') : ''}"></span>`
  ).join('');

  const chipsHtml = chips.map(c =>
    `<span class="chip">${c}</span>`
  ).join('');

  return `
  <div class="event-card ${sideClass}-card tier-${tierClass}" id="card-${ev.id}" data-id="${ev.id}" onclick="openTradeModal('${ev.id}')">
    <span class="expand-hint">Click for details</span>
    <div class="card-top">
      <img class="card-avatar ${sideClass}" src="${ev.whale.avatar}" alt="${ev.whale.handle}" />
      <div class="card-meta">
        <div class="card-handle">${ev.whale.handle}</div>
        <div class="card-time">${timeAgo(ev.timestamp)}</div>
      </div>
      <div class="card-badges">
        <span class="tier-badge ${ev.tier}">${ev.tier === 'WHALE' ? '🐋' : '🔵'} ${ev.tier}</span>
      </div>
    </div>
    <div class="card-market">
      <strong>${isYes ? '<span style="color:var(--mint)">YES</span>' : '<span style="color:var(--rose)">NO</span>'}</strong>
      on "${ev.market}"
    </div>
    <div class="card-stats">
      <div class="stat">
        <span class="label">SIZE</span>
        <span class="value ${isYes ? 'positive' : 'negative'}">${fmt(ev.usdValue)}</span>
      </div>
      <div class="stat">
        <span class="label">PRICE</span>
        <span class="value neutral">${fmtPrice(ev.price)}</span>
      </div>
      <div class="stat">
        <span class="label">WIN RATE</span>
        <span class="value ${(ev.whale.winRate||0) >= 0.75 ? 'positive' : 'neutral'}">${fmtWinRate(ev.whale.winRate, ev.wallet_resolved_trades)}</span>
      </div>
      <div class="conviction-bar">
        <div>
          <div class="conviction-label">CONVICTION</div>
          <div class="conviction-score" style="color:${conviction >= 7 ? 'var(--mint)' : conviction >= 5 ? 'var(--amber)' : 'var(--rose)'}">${conviction}/10</div>
        </div>
        <div class="conviction-dots">${dots}</div>
      </div>
    </div>
    <div class="reasoning-chips">${chipsHtml}</div>
    <div class="card-actions">
      <button class="btn-card btn-mock-follow ${following ? 'following' : ''}"
              id="follow-${ev.id}"
              onclick="toggleFollow('${ev.id}', event)">
        ${following ? '✅ Following' : '+ Mock Follow'}
      </button>
      <button class="btn-card btn-profile" onclick="openWhaleModal('${ev.whale.id}', event)">
        View Profile
      </button>
      <button class="btn-card btn-dismiss" onclick="dismissCard('${ev.id}', event)">✕</button>
    </div>
  </div>`;
}

// ── Build Whale Card HTML ─────────────────────────────────────────────────────
function buildWhaleCard(whale, rank) {
  const isTop = rank <= 3;
  const color = rank === 1 ? '#00FFA3' : rank === 2 ? '#4D8EFF' : '#FFB800';
  const canvasId = `spark-lb-${whale.id}`;

  return `
  <div class="whale-card" onclick="openWhaleModal('${whale.id}')">
    <div class="whale-card-top">
      <span class="whale-rank ${isTop ? 'top' : ''}">#${rank}</span>
      <img class="whale-avatar-sm" src="${whale.avatar}" alt="${whale.handle}" />
      <div class="whale-info">
        <div class="whale-handle">${whale.handle}</div>
        <div class="whale-badge">🏅 <span>${whale.badge}</span></div>
      </div>
    </div>
    <div class="sparkline-wrapper">
      <canvas id="${canvasId}" height="40"></canvas>
    </div>
    <div class="whale-stats-row">
      <div class="ws">
        <span class="ws-label">Win Rate</span>
        <span class="ws-value" style="color:var(--mint)">${fmtWinRate(whale.winRate)}</span>
      </div>
      <div class="ws">
        <span class="ws-label">30D ROI</span>
        <span class="ws-value" style="color:var(--mint)">${fmtPct(whale.roi30d)}</span>
      </div>
      <div class="ws">
        <span class="ws-label">Trades</span>
        <span class="ws-value">${whale.totalTrades}</span>
      </div>
    </div>
  </div>`;
}

// ── Render Leaderboard (LIVE — from real WebSocket data) ─────────────────────
function renderLeaderboard() {
  // Aggregate live whales from state.events by wallet address
  const walletMap = {};
  for (const ev of state.events) {
    const w = ev.whale;
    if (!w || !w.wallet) continue;
    const key = w.wallet;
    if (!walletMap[key]) {
      walletMap[key] = {
        handle:      w.handle,
        wallet:      w.wallet,
        winRate:     w.winRate,
        roi30d:      w.roi30d,
        badge:       w.badge,
        totalVolume: 0,
        tradeCount:  0,
        lastTrade:   ev.market,
        sparkData:   w.sparkData || [],
      };
    }
    walletMap[key].totalVolume += ev.usdValue || 0;
    walletMap[key].tradeCount++;
  }

  const ranked = Object.values(walletMap)
    .sort((a, b) => b.totalVolume - a.totalVolume)
    .slice(0, window.isPro() ? 20 : 5);

  if (!ranked.length) {
    whaleCardsEl.innerHTML = `
      <div class="empty-state" style="padding:32px 16px">
        <span class="empty-icon">🐋</span>
        <span>Whales appear here as they trade live. Stay tuned.</span>
      </div>`;
    return;
  }

  const pro = window.isPro();
  let html = ranked.map((w, i) => {
    const rank = i + 1;
    const isTop = rank <= 3;
    const vol = '$' + w.totalVolume.toLocaleString('en-US', { maximumFractionDigits: 0 });
    const shortWallet = w.wallet ? `${w.wallet.slice(0, 6)}…${w.wallet.slice(-4)}` : '';

    return `
    <div class="whale-card" onclick="openWhaleModal(${JSON.stringify({
      wallet: w.wallet, handle: w.handle, winRate: w.winRate, roi30d: w.roi30d
    }).replace(/"/g, '&quot;')})">
      <div class="whale-card-top">
        <span class="whale-rank ${isTop ? 'top' : ''}">#${rank}</span>
        <div class="whale-info">
          <div class="whale-handle">${w.handle}</div>
          <div class="whale-badge">🔥 <span>${vol} vol · ${w.tradeCount} trade${w.tradeCount > 1 ? 's' : ''}</span></div>
        </div>
      </div>
      <div class="whale-stats-row">
        <div class="ws">
          <span class="ws-label">Win Rate</span>
          <span class="ws-value" style="color:var(--mint)">${fmtWinRate(w.winRate)}</span>
        </div>
        <div class="ws">
          <span class="ws-label">30D ROI</span>
          <span class="ws-value" style="color:var(--mint)">${fmtPct(w.roi30d)}</span>
        </div>
        <div class="ws">
          <span class="ws-label">Badge</span>
          <span class="ws-value">${w.badge}</span>
        </div>
      </div>
    </div>`;
  }).join('');

  if (!pro && Object.keys(walletMap).length > 5) {
    html += `<div class="upgrade-banner" style="margin-top:8px">
      <h3>🔒 ${Object.keys(walletMap).length - 5} more whales tracked</h3>
      <p>Upgrade to Pro to see the full Leaderboard.</p>
      <button class="btn-upgrade" onclick="checkoutPro()" style="margin-top:10px;width:100%">Upgrade to Pro</button>
    </div>`;
  }

  whaleCardsEl.innerHTML = html;
}



// ── WebSocket Ingestion (Replaces Mock Simulation) ───────────────────────────
function connectLiveFeed() {
  const wsUrl = window.ENV_WS_URL || 'ws://localhost:8000/ws/pulse';
  const ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log('[PolyVision] Connected to live Brain WebSocket feed.');
    // Optional: show a toast that we are live
  };

  ws.onmessage = (msg) => {
    try {
      const payload = JSON.parse(msg.data);
      if (!payload) return;

      // ── Handle history burst on connect ──────────────────────────────────
      if (payload.type === 'history' && Array.isArray(payload.events)) {
        const maxEvents = window.isPro() ? 50 : 10;
        let loaded = 0;
        for (const p of payload.events) {
          if (!p || (!p.alert_tier && !p.alert_id)) continue;
          if (state.events.length >= maxEvents) break;
          const ev = {
            id:                  p.alert_id || Math.random().toString(36).slice(2),
            tier:                p.alert_tier || 'STANDARD',
            market:              p.market_title || 'Unknown Market',
            marketId:            p.market_id || '',
            outcome:             p.outcome || 'YES',
            usdValue:            parseFloat(p.usd_value || 0),
            price:               parseFloat(p.price || 0.5),
            wallet_total_trades: parseInt(p.wallet_total_trades || 1),
            wallet_total_volume: parseFloat(p.wallet_total_volume || p.usd_value || 10000),
            timestamp:           p.timestamp ? new Date(p.timestamp).getTime() : Date.now(),
            historical:          true,   // flag: don't animate or alert
            whale: {
              id:        'real-whale',
              handle:    p.trader_handle || 'Unknown Whale',
              wallet:    p.wallet_address || '',
              avatar:    pickRandom(WHALES).avatar,
              badge:     p.wallet_win_rate >= 0.6 ? 'Shark' : (p.wallet_win_rate !== undefined ? 'Pro' : 'Newcomer'),
              winRate:   p.wallet_win_rate !== undefined ? parseFloat(p.wallet_win_rate) : 0,
              roi30d:    parseFloat(p.wallet_roi_30d || 0),
              sparkData: Array.from({ length: 12 }, () => Math.random() * 100),
            },
            reasoningChips: [],
          };
          state.events.push(ev);   // push to end (they arrive oldest-first)
          loaded++;
        }
        if (loaded > 0) {
          // Generate chips for historical events (no frequency context yet, that's fine)
          state.events.forEach(ev => { if (!ev.reasoningChips?.length) ev.reasoningChips = generateChips(ev); });
          renderFeed();
          updateStats();
          console.log(`[PolyVision] Seeded feed with ${loaded} historical events.`);
        }
        return;  // don't fall through to live-event handling
      }

      // Handle standard / whale alerts

      if (payload.alert_tier || payload.alert_id) {
        // Map backend WhaleAlertPayload schema to frontend UI schema
        const ev = {
          id: payload.alert_id || Math.random().toString(36).slice(2),
          tier: payload.alert_tier || 'STANDARD',
          market: payload.market_title || 'Unknown Market',
          marketId: payload.market_id || '',
          outcome: payload.outcome || 'YES',
          usdValue: parseFloat(payload.usd_value || 0),
          price: parseFloat(payload.price || 0.5),
          wallet_total_trades: parseInt(payload.wallet_total_trades || 1),
          wallet_total_volume: parseFloat(payload.wallet_total_volume || payload.usd_value || 10000),
          timestamp: payload.timestamp ? new Date(payload.timestamp).getTime() : Date.now(),
          // Assign random aesthetic elements to real wallets
          whale: {
            id: 'real-whale',
            handle: payload.trader_handle || 'Unknown Whale',
            wallet: payload.wallet_address || '',
            avatar: pickRandom(WHALES).avatar,
            badge: payload.wallet_win_rate >= 0.6 ? 'Shark' : (payload.wallet_win_rate !== undefined ? 'Pro' : 'Newcomer'),
            winRate: payload.wallet_win_rate !== undefined ? parseFloat(payload.wallet_win_rate) : 0,
            roi30d: parseFloat(payload.wallet_roi_30d || 0),
            sparkData: Array.from({ length: 12 }, () => Math.random() * 100),
          },
          reasoningChips: [],  // computed after push, below
        };

        state.events.unshift(ev);
        const maxEvents = window.isPro() ? 50 : 10;
        if (state.events.length > maxEvents) state.events.pop();

        // Generate chips NOW — after ev is in state.events so frequency counts work
        ev.reasoningChips = generateChips(ev);

        state.todayCount++;
        state.todayVolume += ev.usdValue;

        // Debounce render slightly so the initial 20-burst from cache doesn't lock the UI
        renderFeed();
        updateStats();
        checkEventAgainstRules(ev);

        // Animate the new card only if it's new (not a burst payload)
        // A simple heuristic: if it arrived in the last 10 seconds, it's live
        const isLive = (Date.now() - ev.timestamp) < 10000;
        if (isLive) {
          const firstCard = pulseFeed.querySelector('.event-card');
          if (firstCard) {
            firstCard.classList.add('event-card-new');
            firstCard.addEventListener('animationend', () => firstCard.classList.remove('event-card-new'), { once: true });
          }
          if (ev.tier === 'WHALE') showToast(ev);
        }
      } else if (payload.mega_cluster) {
        // Handle mega-cluster alerts
        const ev = {
          id: 'cls-' + (payload.anchor_trade?.id || Math.random()),
          tier: 'CLUSTER',
          market: payload.anchor_trade?.market_title || 'Unknown Market',
          outcome: payload.anchor_trade?.outcome || 'YES',
          usdValue: parseFloat(payload.total_usd_value || 0),
          price: parseFloat(payload.anchor_trade?.price || 0.5),
          clusterCount: (payload.participants || []).length,
          clusterParticipants: (payload.participants || []).map(p => ({
            handle: p.trader_handle,
            wallet: p.wallet_address,
            usd_value: p.usd_value,
            price: p.price
          })),
          timestamp: Date.now(),
          whale: pickRandom(WHALES), // fallback
          reasoningChips: [],
        };

        state.events.unshift(ev);
        if (state.events.length > 50) state.events.pop();

        renderFeed();
        updateStats();
        showClusterToast(ev);
      }
    } catch (e) {
      console.error('WebSocket receive error:', e, msg.data);
    }
  };

  ws.onclose = () => {
    console.warn('[PolyVision] WebSocket disconnected. Reconnecting in 5s...');
    setTimeout(connectLiveFeed, 5000);
  };
}


// ── Render Pulse Feed ─────────────────────────────────────────────────────────
function renderFeed() {
  let events = [...state.events];

  // Apply filters
  events = events.filter(ev => {
    if (ev.usdValue < state.filters.minSize) return false;
    if (state.filters.side !== 'all' && ev.outcome !== state.filters.side) return false;
    return true;
  });

  // Sort
  if (state.sortBy === 'largest') {
    events.sort((a, b) => b.usdValue - a.usdValue);
  }

  if (events.length === 0) {
    pulseFeed.innerHTML = `<div class="empty-state"><span class="empty-icon">🔍</span><span>Waiting for live signals...</span></div>`;
    return;
  }

  pulseFeed.innerHTML = events.map(buildEventCard).join('');

  // Keep the LIVE leaderboard in sync with new events
  renderLeaderboard();
}

// ── Update HUD ────────────────────────────────────────────────────────────────
function updateHud() {
  const { totalPnl, totalInvested, trackCount } = state.portfolio;
  const roi = totalInvested > 0 ? (totalPnl / totalInvested) : 0;
  const roiStr = fmtPct(roi);
  const pnlStr = (totalPnl >= 0 ? '+$' : '-$') + Math.abs(totalPnl).toLocaleString('en-US', { maximumFractionDigits: 0 });

  hudRoi.textContent = roiStr;
  hudPnl.textContent = pnlStr;
  hudTracked.textContent = `${trackCount} trade${trackCount !== 1 ? 's' : ''} tracked`;
  hudRoi.classList.toggle('negative', roi < 0);
  hudPnl.classList.toggle('negative', totalPnl < 0);
}

// ── Update Stats Bar ──────────────────────────────────────────────────────────
function updateStats() {
  liveCount.textContent = `${state.todayCount} signals today`;
  totalVolume.textContent = fmt(state.todayVolume);
  // Count distinct whales seen in this session
  const activeWhaleCount = new Set(state.events.map(e => e.whale?.wallet).filter(Boolean)).size;
  whaleCount.textContent = activeWhaleCount || '—';
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(ev) {
  const isYes = ev.outcome === 'YES';
  const toast = document.createElement('div');
  toast.className = `toast ${isYes ? '' : 'no-toast'}`;
  toast.innerHTML = `
    <div class="toast-title">🐋 ${ev.tier} ALERT</div>
    <div class="toast-subtitle">${ev.whale.handle} · ${ev.outcome} on "${ev.market.slice(0, 40)}..."</div>
    <div class="toast-amount" style="color:${isYes ? 'var(--mint)' : 'var(--rose)'}">${fmt(ev.usdValue)}</div>
  `;
  toast.onclick = () => dismissToast(toast);
  toastContainer.prepend(toast);
  setTimeout(() => dismissToast(toast), 6000);
}

function showClusterToast(ev) {
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.style.cssText = 'border-color:#FFB800;box-shadow:0 0 24px rgba(255,184,0,0.35)';
  toast.innerHTML = `
    <div class="toast-title" style="color:#FFB800">🚨 MEGA-CLUSTER DETECTED</div>
    <div class="toast-subtitle">${ev.clusterCount} whales · ${ev.outcome} · "${ev.market.slice(0, 38)}…"</div>
    <div class="toast-amount" style="color:#FFB800">${fmt(ev.usdValue)} Combined</div>
  `;
  toast.onclick = () => dismissToast(toast);
  toastContainer.prepend(toast);
  setTimeout(() => dismissToast(toast), 9000);
}

function dismissToast(toast) {
  toast.classList.add('fade-out');
  setTimeout(() => toast.remove(), 300);
}

// ── Paper Trading — Real P&L Engine ──────────────────────────────────────────
const BRAIN_URL = 'http://localhost:8000';
// Maps local event ID → paper trade_id returned by the Brain API
const paperTradeIds = {};

window.toggleFollow = async function (eventId, e) {
  e.stopPropagation();
  const ev = state.events.find(x => x.id === eventId);
  if (!ev) return;

  const conviction = calcConviction(ev.whale, ev.usdValue, ev.wallet_total_trades, ev.wallet_total_volume);
  const isFollowing = state.following.has(eventId);

  if (isFollowing) {
    // Unfollow: DELETE from Brain paper portfolio
    state.following.delete(eventId);
    state.portfolio.trackCount = Math.max(0, state.portfolio.trackCount - 1);
    const paperTradeId = paperTradeIds[eventId];
    if (paperTradeId) {
      try {
        await fetch(`${BRAIN_URL}/paper/follow/${paperTradeId}`, { method: 'DELETE' });
        delete paperTradeIds[eventId];
      } catch { /* Brain offline — local state still updated */ }
    }
  } else {
    // Follow: POST to Brain paper portfolio with real entry data
    state.following.add(eventId);
    state.portfolio.trackCount++;
    try {
      const resp = await fetch(`${BRAIN_URL}/paper/follow`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          alert_id: ev.id,
          market_id: ev.marketId || ev.id,
          market_title: ev.market,
          outcome: ev.outcome,
          price: ev.price,
          usd_value: ev.usdValue,
          trader_handle: ev.whale.handle,
          conviction,
        }),
      });
      if (resp.ok) {
        const data = await resp.json();
        paperTradeIds[eventId] = data.trade?.trade_id;
      }
    } catch { /* Brain offline — follow logged locally only */ }
  }

  updateHud();

  // Update card button text
  const btn = document.getElementById(`follow-${eventId}`);
  if (btn) {
    const nowFollowing = state.following.has(eventId);
    btn.textContent = nowFollowing ? '✅ Following' : '+ Mock Follow';
    btn.classList.toggle('following', nowFollowing);
  }
  // Update modal button text if open
  const modalBtn = document.getElementById(`modal-follow-${eventId}`);
  if (modalBtn) {
    const nowFollowing = state.following.has(eventId);
    modalBtn.textContent = nowFollowing ? '✅ Following' : '+ Mock Follow This Trade';
    modalBtn.classList.toggle('following', nowFollowing);
  }
};

// ── Real Portfolio P&L — polls Brain every 30s ────────────────────────────────
function startPaperPortfolioPolling() {
  async function fetchRealPortfolio() {
    try {
      const resp = await fetch(`${BRAIN_URL}/paper/portfolio`, { signal: AbortSignal.timeout(10000) });
      if (!resp.ok) return;
      const portfolio = await resp.json();

      // Update HUD with real numbers
      const pnl = portfolio.total_pnl ?? 0;
      const roi = portfolio.roi_pct ?? 0;
      const count = portfolio.total_trades ?? 0;

      hudTracked.textContent = `${count} trade${count !== 1 ? 's' : ''} tracked`;
      hudRoi.textContent = (roi >= 0 ? '+' : '') + roi.toFixed(2) + '%';
      hudPnl.textContent = (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toFixed(2);
      hudRoi.classList.toggle('negative', roi < 0);
      hudPnl.classList.toggle('negative', pnl < 0);

      // If Brain is responding, show a "LIVE P&L" badge on the HUD label
      const hudLabel = document.querySelector('.hud-label');
      if (hudLabel && !hudLabel.dataset.live) {
        hudLabel.dataset.live = '1';
        const badge = document.createElement('span');
        badge.textContent = ' · LIVE';
        badge.style.cssText = 'color:var(--mint);font-size:9px;font-weight:700';
        hudLabel.appendChild(badge);
      }
    } catch {
      // Brain unreachable — HUD stays on last known values, no crash
    }
  }

  // First poll 5 seconds after init, then every 30s
  setTimeout(fetchRealPortfolio, 5000);
  setInterval(fetchRealPortfolio, 30_000);
}


// ── Dismiss Card ──────────────────────────────────────────────────────────────
window.dismissCard = function (eventId, e) {
  e.stopPropagation();
  state.events = state.events.filter(x => x.id !== eventId);
  renderFeed();
};

// ── Whale Profile Modal ───────────────────────────────────────────────────────
window.openWhaleModal = function (whaleId, e) {
  if (e) e.stopPropagation();
  const whale = WHALES.find(w => w.id === whaleId);
  if (!whale) return;

  const recentHtml = whale.recentTrades.map(t => `
    <div class="recent-trade-row">
      <span class="trade-outcome ${t.outcome.toLowerCase()}">${t.outcome}</span>
      <span class="trade-market">${t.market.slice(0, 36)}${t.market.length > 36 ? '...' : ''}</span>
      <span class="trade-size">${fmt(t.size)}</span>
    </div>
  `).join('');

  modalContent.innerHTML = `
    <div class="modal-whale-top">
      <img class="modal-avatar" src="${whale.avatar}" alt="${whale.handle}" />
      <div>
        <div class="modal-handle">${whale.handle}</div>
        <div class="modal-wallet">${whale.wallet} · ${whale.dominantCategory}</div>
      </div>
    </div>
    <div class="modal-stats-grid">
      <div class="modal-stat">
        <div class="modal-stat-label">Win Rate</div>
        <div class="modal-stat-value" style="color:var(--mint)">${fmtWinRate(whale.winRate)}</div>
      </div>
      <div class="modal-stat">
        <div class="modal-stat-label">30D ROI</div>
        <div class="modal-stat-value" style="color:var(--mint)">${fmtPct(whale.roi30d)}</div>
      </div>
      <div class="modal-stat">
        <div class="modal-stat-label">All-Time ROI</div>
        <div class="modal-stat-value" style="color:var(--mint)">${fmtPct(whale.roiAllTime)}</div>
      </div>
      <div class="modal-stat">
        <div class="modal-stat-label">Volume</div>
        <div class="modal-stat-value">${fmt(whale.totalVolume)}</div>
      </div>
      <div class="modal-stat">
        <div class="modal-stat-label">Total Trades</div>
        <div class="modal-stat-value">${whale.totalTrades}</div>
      </div>
      <div class="modal-stat">
        <div class="modal-stat-label">Specialty</div>
        <div class="modal-stat-value" style="font-size:12px;margin-top:6px">${whale.dominantCategory}</div>
      </div>
    </div>
    <div class="modal-chart-title">30-Day P&amp;L Curve</div>
    <div class="sparkline-wrapper" style="height:60px">
      <canvas id="modal-spark-${whale.id}" height="60"></canvas>
    </div>
    <div class="modal-recent-title">Recent Positions</div>
    ${recentHtml}
    <div style="margin-top:16px;font-size:10px;color:var(--text-muted);line-height:1.6">
      ⚠️ Whales can hedge. Following a trade is at your own risk. This is not financial advice.
    </div>
  `;
  modalOverlay.classList.add('open');

  requestAnimationFrame(() => {
    const c = document.getElementById(`modal-spark-${whale.id}`);
    if (c) drawSparkline(`modal-spark-${whale.id}`, whale.sparkData, '#00FFA3');
  });
};

$('modalClose').onclick = () => modalOverlay.classList.remove('open');
$('modalOverlay').onclick = e => { if (e.target === modalOverlay) modalOverlay.classList.remove('open'); };

// ── Trade Detail Modal (click on a Pulse Feed card) ───────────────────────────
window.openTradeModal = function (eventId) {
  const ev = state.events.find(x => x.id === eventId);
  if (!ev) return;

  const whale = ev.whale;
  const isYes = ev.outcome === 'YES';
  const conviction = calcConviction(whale, ev.usdValue, ev.wallet_total_trades, ev.wallet_total_volume);
  const sideColor = isYes ? 'var(--mint)' : 'var(--rose)';
  const convColor = conviction >= 7 ? 'var(--mint)' : conviction >= 5 ? 'var(--amber)' : 'var(--rose)';
  const following = state.following.has(eventId);

  const dots = Array.from({ length: 10 }, (_, i) =>
    `<span class="dot ${i < conviction ? 'filled' + (conviction <= 5 ? ' risk' : '') : ''}"></span>`
  ).join('');

  const chipsHtml = ev.reasoningChips.map(c => `<span class="chip">${c}</span>`).join('');

  const recentHtml = whale.recentTrades.map(t => `
        <div class="recent-trade-row">
          <span class="trade-outcome ${t.outcome.toLowerCase()}">${t.outcome}</span>
          <span class="trade-market">${t.market.slice(0, 38)}${t.market.length > 38 ? '…' : ''}</span>
          <span class="trade-size">${fmt(t.size)}</span>
        </div>`).join('');

  modalContent.innerHTML = `
      <!-- ── Trade Header ── -->
      <div class="modal-trade-header">
        <img class="modal-avatar" src="${whale.avatar}" alt="${whale.handle}" />
        <div>
          <div class="modal-handle">${whale.handle}</div>
          <div class="modal-wallet">${whale.wallet} · ${whale.dominantCategory}</div>
        </div>
        <span class="tier-badge ${ev.tier}" style="margin-left:auto">${ev.tier === 'WHALE' ? '🐋' : '🔵'} ${ev.tier}</span>
      </div>

      <!-- ── This Trade ── -->
      <div class="modal-section-label">THIS TRADE</div>
      <div class="modal-trade-box" style="border-left: 3px solid ${sideColor}">
        <div class="modal-trade-market">"${ev.market}"</div>
        <div class="modal-trade-position" style="color:${sideColor}; font-size:22px; font-weight:800; margin: 8px 0">
          ${ev.outcome === 'YES' ? '▲ YES' : '▼ NO'}
        </div>
        <div class="modal-stats-grid" style="margin-top: 8px">
          <div class="modal-stat">
            <div class="modal-stat-label">POSITION SIZE</div>
            <div class="modal-stat-value" style="color:${sideColor}">${fmt(ev.usdValue)}</div>
          </div>
          <div class="modal-stat">
            <div class="modal-stat-label">ENTRY PRICE</div>
            <div class="modal-stat-value">${fmtPrice(ev.price)}</div>
          </div>
          <div class="modal-stat">
            <div class="modal-stat-label">MAX PAYOUT</div>
            <div class="modal-stat-value" style="color:var(--mint)">${fmt(Math.round(ev.usdValue / ev.price))}</div>
          </div>
          <div class="modal-stat">
            <div class="modal-stat-label">CONVICTION</div>
            <div class="modal-stat-value" style="color:${convColor}">${conviction}/10</div>
          </div>
        </div>
        <div class="conviction-dots" style="margin-top:8px">${dots}</div>
        <div class="reasoning-chips" style="margin-top:10px">${chipsHtml}</div>
      </div>

      <!-- ── AI Analysis + Live Context ── -->
      <div class="modal-section-label" style="margin-top:20px">🤖 AI ANALYSIS</div>
      <div style="
        background: var(--bg-secondary);
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        padding: 12px 14px;
        margin-bottom: 16px;
        font-size: 12px;
        line-height: 1.6;
        color: var(--text-secondary);
      ">
        ${ev.aiSummary
      ? `<span style="color:var(--text-primary)">${ev.aiSummary}</span>`
      : `<span style="color:var(--text-muted);font-style:italic">AI summary generated when live Brain data is streaming. Connect to RTDS to see real-time analysis.</span>`
    }
        ${(ev.liveContextSources && ev.liveContextSources.length > 0)
      ? `<div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--border)">
               <span style="font-size:10px;color:var(--amber);font-weight:700;letter-spacing:0.5px">📰 LIVE CONTEXT SOURCE</span>
               <a href="${ev.liveContextSources[0]}" target="_blank" rel="noopener noreferrer"
                  style="display:block;margin-top:3px;font-size:10px;color:var(--text-muted);word-break:break-all;text-decoration:underline;opacity:0.8">
                 ${ev.liveContextSources[0]}
               </a>
             </div>`
      : ''
    }
      </div>

      <!-- ── Whale Profile ── -->
      <div class="modal-section-label">WHALE PROFILE</div>
      <div class="modal-stats-grid">
        <div class="modal-stat">
          <div class="modal-stat-label">Win Rate</div>
          <div class="modal-stat-value" style="color:var(--mint)">${fmtWinRate(whale.winRate)}</div>
        </div>
        <div class="modal-stat">
          <div class="modal-stat-label">30D ROI</div>
          <div class="modal-stat-value" style="color:var(--mint)">${fmtPct(whale.roi30d)}</div>
        </div>
        <div class="modal-stat">
          <div class="modal-stat-label">All-Time ROI</div>
          <div class="modal-stat-value" style="color:var(--mint)">${fmtPct(whale.roiAllTime)}</div>
        </div>
        <div class="modal-stat">
          <div class="modal-stat-label">Total Volume</div>
          <div class="modal-stat-value">${fmt(whale.totalVolume)}</div>
        </div>
        <div class="modal-stat">
          <div class="modal-stat-label">Trades</div>
          <div class="modal-stat-value">${whale.totalTrades}</div>
        </div>
        <div class="modal-stat">
          <div class="modal-stat-label">Specialty</div>
          <div class="modal-stat-value" style="font-size:11px;margin-top:4px">${whale.dominantCategory}</div>
        </div>
      </div>

      <!-- ── Recent Positions ── -->
      <div class="modal-recent-title">Recent Positions</div>
      ${recentHtml}

      <!-- ── Actions ── -->
      <div style="display:flex; gap:8px; margin-top:18px">
        <button class="btn-mock-follow btn-card ${following ? 'following' : ''}"
                id="modal-follow-${eventId}"
                onclick="toggleFollow('${eventId}', event)">
          ${following ? '✅ Following' : '+ Mock Follow This Trade'}
        </button>
        <button class="btn-profile btn-card" onclick="openWhaleModal('${whale.id}', event)">
          Full Profile →
        </button>
      </div>

      <div style="margin-top:14px;font-size:10px;color:var(--text-muted);line-height:1.6">
        ⚠️ Whales can hedge. This is not financial advice. Trade at your own risk.
      </div>
    `;

  modalOverlay.classList.add('open');
};

// ── Filter Controls ────────────────────────────────────────────────────────────
document.querySelectorAll('#filterPills .pill').forEach(pill => {
  pill.onclick = () => {
    document.querySelectorAll('#filterPills .pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
    state.filters.minSize = parseInt(pill.dataset.min);
    renderFeed();
  };
});
document.querySelectorAll('#sidePills .pill').forEach(pill => {
  pill.onclick = () => {
    document.querySelectorAll('#sidePills .pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
    state.filters.side = pill.dataset.side;
    renderFeed();
  };
});

// ── Sort Controls ──────────────────────────────────────────────────────────────
$('sortNew').onclick = () => {
  state.sortBy = 'newest';
  $('sortNew').classList.add('active');
  $('sortSize').classList.remove('active');
  renderFeed();
};
$('sortSize').onclick = () => {
  state.sortBy = 'largest';
  $('sortSize').classList.add('active');
  $('sortNew').classList.remove('active');
  renderFeed();
};

// ── View Router ─────────────────────────────────────────────────────────────
const VIEWS = {
  'nav-pulse':    { col: 'pulseCol',   lb: true  },
  'nav-whales':   { col: 'whalesCol',  lb: false },
  'nav-markets':  { col: 'marketsCol', lb: false },
  'nav-mock':     { col: null,         lb: true,  action: () => openPortfolio() },
  'nav-briefing': { col: null,         lb: true,  action: () => openBriefing()  },
  'nav-alerts':   { col: null,         lb: true,  action: () => openAlerts()   },
};

function switchView(navId) {
  const view = VIEWS[navId];
  if (!view) return;

  // If it has a special action (modal/panel), run that and bail
  if (view.action) { view.action(); return; }

  // Toggle nav active state
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const navEl = document.getElementById(navId);
  if (navEl) navEl.classList.add('active');

  // Toggle columns
  const cols = ['pulseCol', 'whalesCol', 'marketsCol'];
  cols.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = (view.col === id) ? '' : 'none';
  });

  // Show/hide the right-side leaderboard col
  const lb = document.getElementById('leaderboardCol');
  if (lb) lb.style.display = view.lb ? '' : 'none';

  // Trigger data loads
  if (navId === 'nav-whales') loadWhaleProfiles();
  if (navId === 'nav-markets') loadTopMarkets();
}

document.querySelectorAll('.nav-item:not(.locked)').forEach(item => {
  item.onclick = (e) => {
    e.preventDefault();
    switchView(item.id);
  };
});

// ── Whale Profiles Loader ───────────────────────────────────────────────────
let whaleProfilesLoaded = false;

async function loadWhaleProfiles() {
  if (whaleProfilesLoaded) return;   // only fetch once per session
  const grid = document.getElementById('whaleProfilesGrid');
  const loading = document.getElementById('whaleProfilesLoading');

  try {
    const resp = await fetch(
      'https://data-api.polymarket.com/v1/leaderboard?category=OVERALL&timePeriod=ALL&orderBy=PNL&limit=50'
    );
    const data = await resp.json();
    const rows = Array.isArray(data) ? data : (data.data || []);

    if (!rows.length) {
      grid.innerHTML = '<div class="lb-loading">No data available.</div>';
      return;
    }

    loading && loading.remove();
    grid.innerHTML = rows.map((t, i) => {
      const rank = i + 1;
      const isTop3 = rank <= 3;
      const pnl = parseFloat(t.pnl || 0);
      const vol = parseFloat(t.vol || 0);
      const pnlStr = (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toLocaleString('en-US', { maximumFractionDigits: 0 });
      const volStr = '$' + vol.toLocaleString('en-US', { maximumFractionDigits: 0 });
      const handle = t.userName || t.name || `Trader 0x…${(t.proxyWallet || '').slice(-6).toUpperCase()}`;
      const shortWallet = t.proxyWallet ? `${t.proxyWallet.slice(0, 6)}…${t.proxyWallet.slice(-4)}` : '';

      return `
      <div class="whale-profile-card" onclick="openWhaleModal(${JSON.stringify({ 
        wallet: t.proxyWallet || '', handle, winRate: 0, roi30d: 0 
      }).replace(/"/g, '&quot;')})">
        <div class="wpc-rank ${isTop3 ? 'top3' : ''}">${isTop3 ? ['🥇','🥈','🥉'][rank-1] : '#' + rank}</div>
        <div class="wpc-info">
          <div class="wpc-handle">${handle}</div>
          <div class="wpc-wallet">${shortWallet}</div>
        </div>
        <div class="wpc-stats">
          <div class="wpc-stat">
            <span class="wpc-label">ALL-TIME P&L</span>
            <span class="wpc-value ${pnl >= 0 ? 'positive' : 'negative'}">${pnlStr}</span>
          </div>
          <div class="wpc-stat">
            <span class="wpc-label">VOLUME</span>
            <span class="wpc-value">${volStr}</span>
          </div>
        </div>
      </div>`;
    }).join('');

    whaleProfilesLoaded = true;
  } catch (err) {
    grid.innerHTML = `<div class="lb-loading">⚠️ Failed to load leaderboard: ${err.message}</div>`;
  }
}


// ── Top Markets Loader ──────────────────────────────────────────────────────
let marketsData = [];
let marketsSortBy = 'volume24hr';

function renderMarkets() {
  const grid = document.getElementById('marketsGrid');
  if (!marketsData.length) return;

  const sorted = [...marketsData].sort((a, b) => {
    const key = marketsSortBy;
    return parseFloat(b[key] || 0) - parseFloat(a[key] || 0);
  });

  grid.innerHTML = sorted.slice(0, 40).map((m, i) => {
    const vol24h = parseFloat(m.volume24hr || m.volume || 0);
    const liq    = parseFloat(m.liquidity || 0);
    const price  = parseFloat(m.lastTradePrice || m.outcomePrices?.[0] || 0);
    const pctYes = Math.round(price * 100);
    const pctNo  = 100 - pctYes;
    const title  = m.question || m.title || 'Unknown Market';
    const endDate = m.endDate ? new Date(m.endDate).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—';
    const isHot  = vol24h > 50000;

    return `
    <div class="market-card">
      <div class="mc-top">
        <div class="mc-title">${title}</div>
        ${isHot ? '<span class="mc-hot-badge">🔥 HOT</span>' : ''}
      </div>
      <div class="mc-bar-wrap">
        <div class="mc-bar-yes" style="width:${pctYes}%"></div>
        <div class="mc-bar-no"  style="width:${pctNo}%"></div>
      </div>
      <div class="mc-odds">
        <span class="mc-yes">YES ${pctYes}¢</span>
        <span class="mc-no">NO  ${pctNo}¢</span>
      </div>
      <div class="mc-meta">
        <span class="mc-meta-item">📈 $${vol24h.toLocaleString('en-US', { maximumFractionDigits: 0 })} vol/24h</span>
        <span class="mc-meta-item">💧 $${liq.toLocaleString('en-US', { maximumFractionDigits: 0 })} liq</span>
        <span class="mc-meta-item">🗓 ${endDate}</span>
      </div>
    </div>`;
  }).join('');
}

async function loadTopMarkets() {
  if (marketsData.length) { renderMarkets(); return; } // already loaded
  const grid = document.getElementById('marketsGrid');

  // Try data-api first (CORS-friendly, works from Cloudflare Pages)
  const endpoints = [
    'https://data-api.polymarket.com/markets?limit=60&order=volume24hr&ascending=false&active=true',
    'https://gamma-api.polymarket.com/markets?limit=60&order=volume24hr&ascending=false&active=true',
  ];

  for (const url of endpoints) {
    try {
      const resp = await fetch(url, { signal: AbortSignal.timeout(10000) });
      if (!resp.ok) continue;
      const data = await resp.json();
      const raw = Array.isArray(data) ? data : (data.data || data.markets || []);

      // Normalize field names — data-api uses camelCase slightly differently
      marketsData = raw.map(m => ({
        question:      m.question || m.title || m.name || 'Unknown Market',
        volume24hr:    parseFloat(m.volume24hr || m.volume_24hr || m.oneDayVolume || 0),
        liquidity:     parseFloat(m.liquidity || m.liquidityNum || 0),
        lastTradePrice: parseFloat(m.lastTradePrice || m.lastPrice || m.price || 0.5),
        endDate:       m.endDate || m.end_date || null,
        outcomePrices: m.outcomePrices || [],
      }));

      document.getElementById('marketsLoading')?.remove();
      renderMarkets();

      // Wire sort buttons
      const btnVol = document.getElementById('mktSortVol');
      const btnLiq = document.getElementById('mktSortLiq');
      if (btnVol) btnVol.onclick = () => {
        marketsSortBy = 'volume24hr';
        btnVol.classList.add('active'); btnLiq?.classList.remove('active');
        renderMarkets();
      };
      if (btnLiq) btnLiq.onclick = () => {
        marketsSortBy = 'liquidity';
        btnLiq.classList.add('active'); btnVol?.classList.remove('active');
        renderMarkets();
      };
      return; // success — stop trying
    } catch (_) {
      // Try next endpoint
    }
  }

  // Both failed
  grid.innerHTML = `<div class="lb-loading">⚠️ Could not load markets. Check your internet connection.</div>`;
}


// ── Startup (Clerk Auth Wrapper) ─────────────────────────────────────────────
async function initAuth() {
  try {
    await window.Clerk.load();
    if (window.Clerk.user) {
      // User is authenticated
      document.getElementById('authOverlay').style.display = 'none';
      window.Clerk.mountUserButton(document.getElementById('clerk-user-button'));

      initApp();
    } else {
      // User is not authenticated
      const signInDiv = document.getElementById('clerk-sign-in');
      window.Clerk.mountSignIn(signInDiv);
    }
  } catch (err) {
    console.error('Error loading Clerk:', err);
    document.getElementById('authOverlay').style.display = 'none';
    initApp(); // fallback if Clerk fails
  }
}

function initApp() {
  updateStats();
  updateHud();

  // Connect to the Live Backend WebSocket Pipeline
  connectLiveFeed();

  startPaperPortfolioPolling();
  initLeaderboardTabs();
}

window.addEventListener('load', () => {
  const checkClerk = setInterval(() => {
    if (window.Clerk) {
      clearInterval(checkClerk);
      initAuth();
    }
  }, 100);

  // Fallback after 5 seconds
  setTimeout(() => {
    if (!window.Clerk) {
      clearInterval(checkClerk);
      console.error('Clerk script failed to load after 5 seconds');
      document.getElementById('authOverlay').style.display = 'none';
      initApp();
    }
  }, 5000);
});

// ── Alpha Leaderboard ─────────────────────────────────────────────────────────
let lbActiveTab = 'live';
let lbLoaded = false;

function initLeaderboardTabs() {
  const tabs = document.querySelectorAll('.lb-tab');
  const whaleCards = $('whaleCards');
  const lbAlltime = $('lbAlltime');
  const lbRefresh = $('lbRefresh');

  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      tabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      lbActiveTab = tab.dataset.tab;

      if (lbActiveTab === 'live') {
        whaleCards.classList.remove('hidden');
        lbAlltime.classList.add('hidden');
      } else {
        whaleCards.classList.add('hidden');
        lbAlltime.classList.remove('hidden');
        if (!lbLoaded) fetchLeaderboard();
      }
    });
  });

  if (lbRefresh) {
    lbRefresh.addEventListener('click', () => {
      if (lbActiveTab === 'alltime') {
        fetchLeaderboard(true);
      }
    });
  }
}

async function fetchLeaderboard(forceRefresh = false) {
  const lbLoading = $('lbLoading');
  const lbRows    = $('lbRows');

  if (lbLoading) lbLoading.style.display = 'block';
  if (lbRows)    lbRows.innerHTML = '';

  // Try Railway brain first (has richer data)
  try {
    const url  = `${BRAIN_URL}/leaderboard?limit=100${forceRefresh ? '&refresh=true' : ''}`;
    const resp = await fetch(url, { signal: AbortSignal.timeout(6000) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    renderGlobalLeaderboard(data.traders || []);
    lbLoaded = true;
    if (lbLoading) lbLoading.style.display = 'none';
    return;
  } catch (_) {
    // Railway unavailable — fall back to public Polymarket API
  }

  // Fallback: Polymarket public leaderboard API (no backend needed)
  try {
    const resp = await fetch(
      'https://data-api.polymarket.com/v1/leaderboard?category=OVERALL&timePeriod=ALL&orderBy=PNL&limit=100',
      { signal: AbortSignal.timeout(10000) }
    );
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const rows = await resp.json();

    // Map Polymarket API shape → renderGlobalLeaderboard format
    const traders = (Array.isArray(rows) ? rows : []).map((t, i) => ({
      rank:        i + 1,
      wallet:      t.proxyWallet || '',
      handle:      t.userName || t.name || `Trader 0x…${(t.proxyWallet || '').slice(-6).toUpperCase()}`,
      win_rate:    null,  // not in public API
      pnl:         parseFloat(t.pnl || 0),
      trades:      null,
    }));

    renderGlobalLeaderboard(traders);
    lbLoaded = true;

    // Show a small "via public API" note
    if (lbRows) {
      const note = document.createElement('div');
      note.style.cssText = 'text-align:center;font-size:10px;color:var(--text-muted);padding:8px;';
      note.textContent = '📡 Data from Polymarket public API';
      lbRows.appendChild(note);
    }
  } catch (err) {
    if (lbRows) lbRows.innerHTML = `
      <div class="lb-loading">
        ⚠️ Could not load leaderboard.<br>
        <small>Check your internet connection and try again.</small>
      </div>`;
  } finally {
    if (lbLoading) lbLoading.style.display = 'none';
  }
}


function renderGlobalLeaderboard(traders) {
  const lbRows = $('lbRows');
  if (!lbRows) return;

  if (!traders.length) {
    lbRows.innerHTML = '<div class="lb-loading">No leaderboard data available.</div>';
    return;
  }

  const MEDALS = { 1: '🥇', 2: '🥈', 3: '🥉' };

  lbRows.innerHTML = traders.map(t => {
    const rankLabel = MEDALS[t.rank] || `#${t.rank}`;
    const isTop3 = t.rank <= 3;
    const pnlPos = t.pnl >= 0;
    const pnlStr = (pnlPos ? '+$' : '-$') + Math.abs(t.pnl).toLocaleString('en-US', { maximumFractionDigits: 0 });
    const winStr = t.win_rate ? `${(t.win_rate * 100).toFixed(0)}% win` : 'New';
    const tradesStr = t.trades ? `${t.trades.toLocaleString()} trades` : '';
    const meta = [winStr, tradesStr].filter(Boolean).join(' · ');
    const shortWallet = t.wallet ? `${t.wallet.slice(0, 6)}…${t.wallet.slice(-4)}` : '';

    return `
        <div class="lb-row" onclick="openLbTraderModal('${t.wallet}', '${t.handle.replace(/'/g, "\\'")}', ${JSON.stringify(t).replace(/</g, '\\u003c')})">
            <span class="lb-rank ${isTop3 ? 'top3' : ''}">${rankLabel}</span>
            <div class="lb-info">
                <span class="lb-handle">${t.handle}</span>
                <span class="lb-meta">${meta}${meta && shortWallet ? ' · ' : ''}${shortWallet}</span>
            </div>
            <span class="lb-pnl ${pnlPos ? 'positive' : 'negative'}">${pnlStr}</span>
        </div>`;
  }).join('');
}

window.openLbTraderModal = function (wallet, handle, traderData) {
  const trader = typeof traderData === 'string' ? JSON.parse(traderData) : traderData;
  const pnlPos = trader.pnl >= 0;
  const pnlStr = (pnlPos ? '+$' : '-$') + Math.abs(trader.pnl).toLocaleString('en-US', { maximumFractionDigits: 0 });

  $('modalContent').innerHTML = `
        <div class="modal-header">
            <div class="modal-avatar">👑</div>
            <div class="modal-title-block">
                <h2 class="modal-trader-name">${handle}</h2>
                <p class="modal-wallet">${wallet ? wallet.slice(0, 10) + '…' + wallet.slice(-6) : 'Unknown Wallet'}</p>
            </div>
        </div>
        <div class="modal-section-label">ALL-TIME PERFORMANCE</div>
        <div class="modal-stats-grid">
            <div class="stat-block">
                <span class="stat-label">All-Time P&L</span>
                <span class="stat-value ${pnlPos ? 'positive' : 'negative'}">${pnlStr}</span>
            </div>
            <div class="stat-block">
                <span class="stat-label">Win Rate</span>
                <span class="stat-value">${t.win_rate ? (t.win_rate * 100).toFixed(1) + '%' : 'New'}</span>
            </div>
            <div class="stat-block">
                <span class="stat-label">Total Trades</span>
                <span class="stat-value">${trader.trades ? trader.trades.toLocaleString() : 'N/A'}</span>
            </div>
            <div class="stat-block">
                <span class="stat-label">Volume</span>
                <span class="stat-value">$${trader.volume ? trader.volume.toLocaleString('en-US', { maximumFractionDigits: 0 }) : 'N/A'}</span>
            </div>
            <div class="stat-block">
                <span class="stat-label">Global Rank</span>
                <span class="stat-value">#${trader.rank}</span>
            </div>
        </div>
        <div class="modal-section-label">WALLET</div>
        <div class="modal-trade-box">
            <code style="font-size:11px;color:var(--text-muted);word-break:break-all">${wallet || 'N/A'}</code>
        </div>
        <div class="modal-actions">
            <a class="btn-modal-primary" href="https://polymarket.com/profile/${wallet}" target="_blank" rel="noopener">
                🔗 View on Polymarket
            </a>
        </div>`;
  $('modalOverlay').classList.add('active');
};


// ── Wallet X-Ray (3-tab deep profile) ─────────────────────────────────────────
let xrayChartInstance = null;

window.openXrayModal = async function (wallet, handle) {
  // Show modal immediately with loading spinner
  $('modalContent').innerHTML = `
        <div class="modal-header">
            <div class="modal-avatar">🔬</div>
            <div class="modal-title-block">
                <h2 class="modal-trader-name">${handle}</h2>
                <p class="modal-wallet">${wallet.slice(0, 10)}…${wallet.slice(-6)}</p>
            </div>
        </div>
        <div class="xray-loading">⏳ Loading wallet X-Ray data…</div>`;
  $('modalOverlay').classList.add('active');

  // Destroy any previous Chart.js instance
  if (xrayChartInstance) { xrayChartInstance.destroy(); xrayChartInstance = null; }

  let profile;
  try {
    const resp = await fetch(`${BRAIN_URL}/wallet/${wallet}/xray`, { signal: AbortSignal.timeout(15000) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    profile = await resp.json();
  } catch (err) {
    $('modalContent').innerHTML += `
            <p style="color:var(--rose);text-align:center;padding:24px">
                ⚠️ Could not load X-Ray data. Brain may still be startring up.
            </p>`;
    return;
  }

  const pnl = profile.all_time_pnl ?? 0;
  const vol = profile.all_time_vol ?? 0;
  const pnlPos = pnl >= 0;
  const pnlStr = (pnlPos ? '+$' : '-$') + Math.abs(pnl).toLocaleString('en-US', { maximumFractionDigits: 0 });

  // ── Positions Tab ──
  const positions = profile.positions || [];
  const openPositions = positions.filter(p => p.is_open);
  const closedPositions = positions.filter(p => !p.is_open);

  const renderPositions = (arr) => arr.length === 0
    ? '<p style="color:var(--text-muted);font-size:12px;padding:8px 0">No positions found.</p>'
    : arr.map(p => {
      const pnlPos = p.net_pnl >= 0;
      const pnlStr = (pnlPos ? '+$' : '-$') + Math.abs(p.net_pnl).toLocaleString('en-US', { maximumFractionDigits: 0 });
      const openBadge = p.is_open ? '<span class="open-badge">OPEN</span>' : '';
      return `
            <div class="xray-position ${p.status}">
                <div>
                    <div class="xray-pos-title">${p.title.slice(0, 55)}${p.title.length > 55 ? '…' : ''}${openBadge}</div>
                    <div class="xray-pos-meta">${p.outcome} · Spent $${p.spent.toLocaleString('en-US', { maximumFractionDigits: 0 })}</div>
                </div>
                <span class="xray-pos-pnl ${pnlPos ? 'positive' : 'negative'}">${pnlStr}</span>
            </div>`;
    }).join('');

  // ── History Tab ──
  const history = profile.history || [];
  const historyHtml = history.length === 0
    ? '<p style="color:var(--text-muted);font-size:12px;padding:8px 0">No history found.</p>'
    : history.slice(0, 50).map(h => {
      const date = h.timestamp ? new Date(h.timestamp * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '';
      const size = parseFloat(h.usdcSize || 0).toLocaleString('en-US', { maximumFractionDigits: 0 });
      const title = (h.title || h.slug || h.conditionId || 'Unknown Market').slice(0, 42);
      const typeColor = h.type === 'BUY' ? 'var(--rose)' : 'var(--mint)';
      return `
            <div class="xray-hist-row">
                <span class="xray-hist-type" style="color:${typeColor}">${h.type || '?'}</span>
                <span class="xray-hist-title">${title}</span>
                <span class="xray-hist-size">$${size}</span>
            </div>`;
    }).join('');

  // ── Build full modal HTML ──
  $('modalContent').innerHTML = `
        <div class="modal-header">
            <div class="modal-avatar">🔬</div>
            <div class="modal-title-block">
                <h2 class="modal-trader-name">${profile.handle || handle}</h2>
                <p class="modal-wallet">${wallet.slice(0, 10)}…${wallet.slice(-6)}</p>
            </div>
        </div>
        <div class="modal-stats-grid" style="margin-bottom:4px">
            <div class="stat-block">
                <span class="stat-label">All-Time P&L</span>
                <span class="stat-value ${pnlPos ? 'positive' : 'negative'}">${pnlStr}</span>
            </div>
            <div class="stat-block">
                <span class="stat-label">Volume</span>
                <span class="stat-value">$${vol.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
            </div>
            <div class="stat-block">
                <span class="stat-label">Open Positions</span>
                <span class="stat-value">${openPositions.length}</span>
            </div>
            <div class="stat-block">
                <span class="stat-label">Markets Tracked</span>
                <span class="stat-value">${positions.length}</span>
            </div>
        </div>

        <div class="xray-tabs" id="xrayTabs">
            <button class="xray-tab active" data-panel="portfolio">📊 Portfolio</button>
            <button class="xray-tab" data-panel="curve">📈 Equity Curve</button>
            <button class="xray-tab" data-panel="history">📋 History</button>
        </div>

        <div class="xray-panel active" id="xray-portfolio" style="max-height:360px;overflow-y:auto">
            ${openPositions.length > 0 ? `<div class="modal-section-label">OPEN POSITIONS (${openPositions.length})</div>${renderPositions(openPositions)}` : ''}
            ${closedPositions.length > 0 ? `<div class="modal-section-label" style="margin-top:8px">CLOSED / SETTLED</div>${renderPositions(closedPositions)}` : ''}
        </div>

        <div class="xray-panel" id="xray-curve">
            <div class="xray-chart-wrap">
                <canvas id="xrayChart"></canvas>
            </div>
            <p style="font-size:10px;color:var(--text-muted);text-align:center;margin-top:6px">
                Cumulative P&amp;L derived from trade activity (BUYs &amp; REDEEMs)
            </p>
        </div>

        <div class="xray-panel" id="xray-history" style="max-height:360px;overflow-y:auto">
            ${historyHtml}
        </div>

        <div class="modal-actions">
            <a class="btn-modal-primary" href="https://polymarket.com/profile/${wallet}" target="_blank" rel="noopener">
                🔗 View on Polymarket
            </a>
        </div>`;

  // Wire tab buttons
  document.querySelectorAll('.xray-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.xray-tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.xray-panel').forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById(`xray-${tab.dataset.panel}`).classList.add('active');

      // Render chart on first open of Equity Curve tab
      if (tab.dataset.panel === 'curve' && !xrayChartInstance) {
        renderEquityCurve(profile.equity_curve || []);
      }
    });
  });
};

function renderEquityCurve(curveData) {
  const canvas = document.getElementById('xrayChart');
  if (!canvas || curveData.length === 0) return;

  const labels = curveData.map(d => d.date || '');
  const values = curveData.map(d => d.pnl);
  const lastVal = values[values.length - 1] ?? 0;
  const color = lastVal >= 0 ? '#00ffa3' : '#ff4d6d';

  xrayChartInstance = new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        data: values,
        borderColor: color,
        backgroundColor: `${color}18`,
        borderWidth: 2,
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 4,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false }, tooltip: {
          callbacks: {
            label: ctx => `P&L: $${ctx.parsed.y.toLocaleString('en-US', { maximumFractionDigits: 0 })}`,
          }
        }
      },
      scales: {
        x: { display: false },
        y: {
          grid: { color: 'rgba(255,255,255,0.04)' },
          ticks: {
            color: '#6b7a99',
            font: { size: 10 },
            callback: v => `$${(v / 1000).toFixed(0)}k`,
          }
        }
      }
    }
  });
}

// Override the leaderboard trader modal to use X-Ray
window.openLbTraderModal = function (wallet, handle, traderData) {
  openXrayModal(wallet, handle);
};

// ── Portfolio Slide-Over Page ─────────────────────────────────────────────────
let pfChartInstance = null;

function openPortfolio() {
  $('portfolioOverlay').classList.add('active');
  renderPortfolio();
}

function closePortfolio() {
  $('portfolioOverlay').classList.remove('active');
}

// ── Morning Alpha Briefing Panel ──────────────────────────────────────────────
window.openBriefing = async function () {
  $('briefingOverlay').classList.add('active');
  // Try to load cached briefing immediately
  try {
    const res = await fetch(`${BRAIN_URL}/briefing/latest`);
    const data = await res.json();
    if (data && data.report) {
      renderBriefing(data);
    }
    // else keep the empty state shown
  } catch (e) {
    console.warn('Could not fetch briefing:', e);
  }
};

window.closeBriefing = function () {
  $('briefingOverlay').classList.remove('active');
};

function renderBriefing(data) {
  const report = data.report || '';
  const stats = data.stats || {};
  const dateStr = data.date_str || data.generated_at?.slice(0, 10) || '';

  // Update date subtitle
  $('briefingDate').textContent = `Generated ${dateStr} · PolyVision AI`;

  // Stats bar
  const totalVol = stats.total_volume || 0;
  const tradeCount = stats.trade_count || 0;
  const topMkt = stats.top_markets?.[0]?.[0] || '—';
  const yesVol = stats.yes_volume || 0;
  const noVol = stats.no_volume || 0;
  const sentiment = yesVol >= noVol ? '📈 Bullish' : '📉 Bearish';
  const sentColor = yesVol >= noVol ? 'var(--mint)' : 'var(--rose)';

  $('bStatAlerts').textContent = tradeCount;
  $('bStatVol').textContent = fmt(Math.round(totalVol));
  $('bStatMkt').textContent = topMkt;
  $('bStatSentiment').textContent = sentiment;
  $('bStatSentiment').style.color = sentColor;
  $('briefingStats').style.display = 'flex';

  // Render formatted report: split into labelled paragraphs
  const sections = report.split(/\n\n+/);
  const htmlParts = sections.map(block => {
    block = block.trim();
    if (!block) return '';
    // Detect section headings (PARAGRAPH 1 — TITLE or similar)
    const headMatch = block.match(/^PARAGRAPH\s+\d+\s*[—\-–]\s*(.+)/i);
    if (headMatch) {
      const heading = headMatch[1].trim();
      const body = block.slice(headMatch[0].length).trim();
      return `<span class="section-head">${heading}</span><span class="section-body">${body}</span>`;
    }
    // Sign-off line
    if (block.startsWith('—') || block.startsWith('–')) {
      return `<span class="sign-off">${block}</span>`;
    }
    return `<span style="display:block;margin-bottom:14px;color:var(--text-primary)">${block}</span>`;
  }).join('');

  $('briefingReport').innerHTML = htmlParts;
  $('briefingEmpty').style.display = 'none';
  $('briefingReport').style.display = 'block';
}

window.triggerBriefing = async function () {
  const btn = $('btnTriggerBriefing');
  btn.disabled = true;
  btn.textContent = '⏳ Generating…';

  try {
    await fetch(`${BRAIN_URL}/briefing/trigger`, { method: 'POST' });
    // Poll /briefing/latest until the report appears (up to ~30s)
    let attempts = 0;
    const poll = setInterval(async () => {
      attempts++;
      try {
        const res = await fetch(`${BRAIN_URL}/briefing/latest`);
        const data = await res.json();
        if (data && data.report) {
          clearInterval(poll);
          renderBriefing(data);
          btn.disabled = false;
          btn.textContent = '⚡ Generate Now';
        }
      } catch (_) { }
      if (attempts >= 10) {
        clearInterval(poll);
        btn.disabled = false;
        btn.textContent = '⚡ Generate Now';
      }
    }, 3000);
  } catch (e) {
    console.error('Trigger failed:', e);
    btn.disabled = false;
    btn.textContent = '⚡ Generate Now';
  }
};


async function renderPortfolio() {
  // Reset UI to loading state
  $('pfLoading').classList.remove('hidden');
  $('pfTable').classList.add('hidden');
  $('pfEmpty').classList.add('hidden');
  if (pfChartInstance) { pfChartInstance.destroy(); pfChartInstance = null; }

  let portfolio;
  try {
    const resp = await fetch(`${BRAIN_URL}/paper/portfolio`, { signal: AbortSignal.timeout(12000) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    portfolio = await resp.json();
  } catch {
    $('pfLoading').textContent = '⚠️ Could not reach the Brain. Is Docker running?';
    return;
  }

  $('pfLoading').classList.add('hidden');

  const trades = portfolio.trades || [];
  const pnl = portfolio.total_pnl ?? 0;
  const roi = portfolio.roi_pct ?? 0;
  const invested = portfolio.total_invested ?? 0;
  const winRate = portfolio.win_rate ?? 0;

  // Update stats bar
  $('pfInvested').textContent = `$${invested.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;

  const pnlEl = $('pfPnl');
  pnlEl.textContent = (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toFixed(2);
  pnlEl.className = 'pf-stat-value ' + (pnl >= 0 ? 'positive' : 'negative');

  const roiEl = $('pfRoi');
  roiEl.textContent = (roi >= 0 ? '+' : '') + roi.toFixed(2) + '%';
  roiEl.className = 'pf-stat-value ' + (roi >= 0 ? 'positive' : 'negative');

  $('pfWinRate').textContent = trades.length > 0
    ? `${(winRate * 100).toFixed(0)}%`
    : '—';
  $('pfCount').textContent = trades.length;

  // Build equity curve — sorted by followed_at, cumulative P&L
  const sorted = [...trades].sort((a, b) =>
    new Date(a.followed_at) - new Date(b.followed_at)
  );
  const curveLabels = sorted.map(t =>
    new Date(t.followed_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  );
  let cumulative = 0;
  const curveValues = sorted.map(t => {
    cumulative += (t.pnl ?? 0);
    return +cumulative.toFixed(2);
  });

  if (curveValues.length > 0) {
    $('pfChartEmpty').style.display = 'none';
    const lastVal = curveValues[curveValues.length - 1];
    const color = lastVal >= 0 ? '#00ffa3' : '#ff4d6d';
    pfChartInstance = new Chart($('pfChart'), {
      type: 'line',
      data: {
        labels: curveLabels,
        datasets: [{
          data: curveValues,
          borderColor: color,
          backgroundColor: `${color}18`,
          borderWidth: 2,
          fill: true,
          tension: 0.3,
          pointRadius: curveValues.length < 20 ? 4 : 0,
          pointHoverRadius: 5,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => `P&L: ${ctx.parsed.y >= 0 ? '+$' : '-$'}${Math.abs(ctx.parsed.y).toFixed(2)}`
            }
          }
        },
        scales: {
          x: { display: curveValues.length < 30, ticks: { color: '#6b7a99', font: { size: 9 } } },
          y: {
            grid: { color: 'rgba(255,255,255,0.04)' },
            ticks: {
              color: '#6b7a99',
              font: { size: 10 },
              callback: v => `$${v.toFixed(0)}`
            }
          }
        }
      }
    });
  } else {
    $('pfChartEmpty').style.display = 'block';
  }

  // Render positions table
  if (trades.length === 0) {
    $('pfEmpty').classList.remove('hidden');
    return;
  }

  $('pfTable').classList.remove('hidden');
  $('pfTableBody').innerHTML = trades.map(t => {
    const pnl = t.pnl ?? null;
    const pnlPos = pnl !== null ? pnl >= 0 : true;
    const pnlStr = pnl !== null
      ? (pnlPos ? '+$' : '-$') + Math.abs(pnl).toFixed(2)
      : '…';
    const curPrice = t.current_price !== null ? `${(t.current_price * 100).toFixed(1)}¢` : '…';
    const entryPrice = `${(t.entry_price * 100).toFixed(1)}¢`;
    const date = t.followed_at
      ? new Date(t.followed_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
      : '—';
    const outcome = (t.outcome || 'YES').toUpperCase();
    const outcomeClass = outcome === 'YES' ? 'yes' : 'no';

    return `<tr id="pf-row-${t.trade_id}">
            <td>
                <div class="pf-market-title">${t.market_title || 'Unknown Market'}</div>
                <div class="pf-market-sub">${t.trader_handle || ''}</div>
            </td>
            <td><span class="pf-outcome-badge ${outcomeClass}">${outcome}</span></td>
            <td class="pf-price">${entryPrice}</td>
            <td class="pf-price">${curPrice}</td>
            <td class="pf-pnl ${pnlPos ? 'positive' : 'negative'}">${pnlStr}</td>
            <td class="pf-date">${date}</td>
            <td><button class="pf-close-btn" onclick="closePosition('${t.trade_id}')">Close</button></td>
        </tr>`;
  }).join('');
}

window.closePosition = async function (tradeId) {
  try {
    const resp = await fetch(`${BRAIN_URL}/paper/follow/${tradeId}`, { method: 'DELETE' });
    if (!resp.ok) throw new Error();
    // Animate row out
    const row = document.getElementById(`pf-row-${tradeId}`);
    if (row) {
      row.style.transition = 'opacity 0.3s, transform 0.3s';
      row.style.opacity = '0';
      row.style.transform = 'translateX(20px)';
      setTimeout(() => { row.remove(); renderPortfolio(); }, 300);
    }
    // Sync client-side following state
    Object.keys(paperTradeIds).forEach(eventId => {
      if (paperTradeIds[eventId] === tradeId) {
        state.following.delete(eventId);
        delete paperTradeIds[eventId];
        const btn = document.getElementById(`follow-${eventId}`);
        if (btn) { btn.textContent = '+ Mock Follow'; btn.classList.remove('following'); }
      }
    });
  } catch {
    alert('Could not close position — Brain may be offline.');
  }
};

// Wire portfolio open triggers
document.addEventListener('DOMContentLoaded', () => {
  const btnView = $('btnViewPortfolio');
  if (btnView) btnView.addEventListener('click', openPortfolio);

  const navMock = $('nav-mock');
  if (navMock) navMock.addEventListener('click', (e) => { e.preventDefault(); openPortfolio(); });

  const pfClose = $('pfClose');
  if (pfClose) pfClose.addEventListener('click', closePortfolio);

  // Close on backdrop click
  $('portfolioOverlay').addEventListener('click', (e) => {
    if (e.target === $('portfolioOverlay')) closePortfolio();
  });

  // Close on Escape
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closePortfolio();
  });
});

// ── Wallet Search ─────────────────────────────────────────────────────────────
window.closeWalletSearch = function () {
  $('walletSearchOverlay').style.display = 'none';
  $('walletSearchInput').value = '';
};

async function runWalletSearch(query) {
  const q = (query || '').trim();
  if (!q) return;

  const overlay  = $('walletSearchOverlay');
  const content  = $('walletSearchContent');
  overlay.style.display = 'flex';
  content.innerHTML = `<div style="text-align:center;padding:40px 0;color:var(--text-muted)">
    <div style="font-size:28px">⏳</div>
    <div style="margin-top:8px">Looking up <strong>${q.slice(0, 30)}${q.length > 30 ? '…' : ''}</strong>…</div>
  </div>`;

  // Determine if it looks like a wallet address (0x…) or a username
  const isWallet = q.startsWith('0x') && q.length >= 10;

  try {
    let profile = null;

    if (isWallet) {
      // ── Lookup by wallet address via Polymarket API ──
      const [xrayResp, pfResp] = await Promise.allSettled([
        fetch(`https://data-api.polymarket.com/v1/leaderboard?category=OVERALL&timePeriod=ALL&orderBy=PNL&proxyWallet=${q.toLowerCase()}&limit=1`),
        fetch(`https://gamma-api.polymarket.com/profiles?wallet=${q.toLowerCase()}`),
      ]);

      const xray = xrayResp.status === 'fulfilled' && xrayResp.value.ok
        ? (await xrayResp.value.json())[0] : null;
      const pf = pfResp.status === 'fulfilled' && pfResp.value.ok
        ? await pfResp.value.json() : null;

      if (!xray && !pf) throw new Error('not_found');

      profile = {
        wallet:   q,
        handle:   pf?.username || pf?.name || xray?.userName || `0x${q.slice(2, 8)}…`,
        pnl:      parseFloat(xray?.pnl || 0),
        winRate:  xray?.winRate ? parseFloat(xray.winRate) : null,
        volume:   parseFloat(xray?.volume || 0),
        trades:   xray?.numTrades || null,
        bio:      pf?.bio || null,
      };
    } else {
      // ── Lookup by username via Gamma profile API ──
      const resp = await fetch(`https://gamma-api.polymarket.com/profiles?username=${encodeURIComponent(q)}`);
      if (!resp.ok) throw new Error('not_found');
      const data = await resp.json();
      const pf = Array.isArray(data) ? data[0] : data;
      if (!pf?.proxyWallet) throw new Error('not_found');

      // Get stats via leaderboard
      const statsResp = await fetch(
        `https://data-api.polymarket.com/v1/leaderboard?category=OVERALL&timePeriod=ALL&proxyWallet=${pf.proxyWallet}&limit=1`
      );
      const stats = statsResp.ok ? (await statsResp.json())[0] : null;

      profile = {
        wallet:   pf.proxyWallet,
        handle:   pf.username || pf.name || q,
        pnl:      parseFloat(stats?.pnl || 0),
        winRate:  stats?.winRate ? parseFloat(stats.winRate) : null,
        volume:   parseFloat(stats?.volume || 0),
        trades:   stats?.numTrades || null,
        bio:      pf.bio || null,
      };
    }

    // Render the result
    const wr  = profile.winRate !== null
      ? `<span style="color:var(--mint);font-weight:700">${(profile.winRate * 100).toFixed(1)}%</span>`
      : '<span style="color:var(--text-muted)">N/A</span>';
    const pnlColor = profile.pnl >= 0 ? 'var(--mint)' : 'var(--rose)';
    const shortW = profile.wallet ? `${profile.wallet.slice(0,6)}…${profile.wallet.slice(-4)}` : '';

    content.innerHTML = `
      <div style="padding:8px 0">
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
          <div style="width:44px;height:44px;border-radius:50%;background:var(--bg-card);display:flex;align-items:center;justify-content:center;font-size:22px;border:2px solid var(--border)">🐋</div>
          <div>
            <div style="font-size:18px;font-weight:800;color:var(--text-primary)">${profile.handle}</div>
            <div style="font-size:11px;color:var(--text-muted);font-family:var(--text-mono)">${shortW}</div>
          </div>
        </div>
        ${profile.bio ? `<p style="color:var(--text-muted);font-size:13px;margin:0 0 16px;line-height:1.5">${profile.bio}</p>` : ''}
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:20px">
          <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius-md);padding:12px;text-align:center">
            <div style="font-size:10px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;letter-spacing:.6px">Total P&amp;L</div>
            <div style="font-size:16px;font-weight:700;color:${pnlColor}">${profile.pnl >= 0 ? '+' : ''}$${Math.abs(profile.pnl).toLocaleString('en-US',{maximumFractionDigits:0})}</div>
          </div>
          <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius-md);padding:12px;text-align:center">
            <div style="font-size:10px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;letter-spacing:.6px">Win Rate</div>
            <div style="font-size:16px;font-weight:700">${wr}</div>
          </div>
          <div style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius-md);padding:12px;text-align:center">
            <div style="font-size:10px;color:var(--text-muted);margin-bottom:4px;text-transform:uppercase;letter-spacing:.6px">Trades</div>
            <div style="font-size:16px;font-weight:700;color:var(--text-primary)">${profile.trades ?? '—'}</div>
          </div>
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn-card btn-mock-follow" style="flex:1"
            onclick="window.open('https://polymarket.com/profile/${profile.wallet}','_blank')">
            🔗 View on Polymarket
          </button>
        </div>
      </div>`;

  } catch (err) {
    content.innerHTML = `
      <div style="text-align:center;padding:32px 0;color:var(--text-muted)">
        <div style="font-size:32px">🤷</div>
        <div style="margin-top:8px;font-weight:700;color:var(--text-primary)">Whale not found</div>
        <div style="margin-top:4px;font-size:13px">Try a full wallet address (0x…) or exact Polymarket username.</div>
      </div>`;
  }
}

// Wire search input + button on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  const input = $('walletSearchInput');
  const btn   = $('walletSearchBtn');
  if (!input || !btn) return;

  btn.onclick = () => runWalletSearch(input.value);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') runWalletSearch(input.value);
  });
});

// ── Custom Alerts System ──────────────────────────────────────────────────────

const ALERTS_KEY   = 'pv_alert_rules';    // localStorage cache for browser toasts
const ALERTS_EMAIL = 'pv_alert_email';    // remember user's email between sessions

function loadAlertRules() {
  try { return JSON.parse(localStorage.getItem(ALERTS_KEY) || '[]'); }
  catch { return []; }
}

function saveAlertRules(rules) {
  localStorage.setItem(ALERTS_KEY, JSON.stringify(rules));
}

/** Get a Railway-auth token — uses Clerk session token */
async function getAuthHeader() {
  try {
    const token = await window.Clerk?.session?.getToken();
    return token ? { 'Authorization': `Bearer ${token}` } : {};
  } catch { return {}; }
}

/** Sync rules TO backend (fire-and-forget) */
async function pushRuleToBackend(rule) {
  const BRAIN = window.ENV_BRAIN_URL || 'https://polyvision-brain.railway.app';
  try {
    const headers = await getAuthHeader();
    await fetch(`${BRAIN}/alerts/rules`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers },
      body: JSON.stringify(rule),
    });
  } catch (e) {
    console.warn('Backend alert sync failed (rules still saved locally):', e);
  }
}

/** Delete rule from backend */
async function deleteRuleFromBackend(ruleId) {
  const BRAIN = window.ENV_BRAIN_URL || 'https://polyvision-brain.railway.app';
  try {
    const headers = await getAuthHeader();
    await fetch(`${BRAIN}/alerts/rules/${ruleId}`, {
      method: 'DELETE',
      headers,
    });
  } catch (e) {
    console.warn('Backend rule delete failed:', e);
  }
}

/** Load rules FROM backend and merge into localStorage */
async function syncRulesFromBackend() {
  const BRAIN = window.ENV_BRAIN_URL || 'https://polyvision-brain.railway.app';
  try {
    const headers = await getAuthHeader();
    const resp = await fetch(`${BRAIN}/alerts/rules`, { headers });
    if (!resp.ok) return;
    const { rules } = await resp.json();
    if (Array.isArray(rules) && rules.length) {
      // Normalize backend snake_case to camelCase for frontend
      const normalized = rules.map(r => ({
        id:      r.id,
        minSize: parseFloat(r.min_size || 10000),
        side:    r.side || 'both',
        keyword: r.keyword || '',
        wallet:  r.wallet || '',
        email:   r.email || '',
        created: r.created_at
          ? new Date(r.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
          : '—',
      }));
      saveAlertRules(normalized);
    }
  } catch (e) {
    console.warn('Backend rule sync skipped:', e);
  }
}

window.openAlerts = function () {
  $('alertsOverlay').classList.add('active');
  // Pre-fill email from last save
  const savedEmail = localStorage.getItem(ALERTS_EMAIL) || '';
  if (savedEmail && $('alertEmail')) $('alertEmail').value = savedEmail;
  renderAlertRules();
  // Sync from backend in background then re-render
  syncRulesFromBackend().then(renderAlertRules);
  if (Notification.permission === 'default') {
    $('alertsPermission').style.display = 'flex';
  } else {
    $('alertsPermission').style.display = 'none';
  }
};

window.closeAlerts = function () {
  $('alertsOverlay').classList.remove('active');
};

window.requestAlertPermission = async function () {
  const perm = await Notification.requestPermission();
  if (perm === 'granted') {
    $('alertsPermission').style.display = 'none';
    showToast({ tier: 'INFO', whale: { handle: '🔔 Notifications enabled' }, market: 'You\'ll receive alerts for your custom rules.', outcome: 'OK', usdValue: 0, timestamp: Date.now() });
  }
};

window.saveAlertRule = function () {
  const email   = ($('alertEmail')?.value || '').trim();
  const minSize = parseInt($('alertMinSize').value || 10000);
  const side    = $('alertSide').value || 'both';
  const keyword = ($('alertKeyword').value || '').trim().toLowerCase();
  const wallet  = ($('alertWallet').value || '').trim().toLowerCase();

  if (!email) {
    showToast({ tier: 'INFO', whale: { handle: '⚠️ Email required' }, market: 'Please enter your email so we can notify you when browser is closed.', outcome: 'INFO', usdValue: 0, timestamp: Date.now() });
    $('alertEmail')?.focus();
    return;
  }

  // Save email for next time
  localStorage.setItem(ALERTS_EMAIL, email);

  const rule = {
    id:      Date.now().toString(),
    email,
    minSize,
    side,
    keyword,
    wallet,
    created: new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }),
  };

  const rules = loadAlertRules();
  rules.push(rule);
  saveAlertRules(rules);

  // Push to backend (email alerts even when tab is closed)
  pushRuleToBackend({
    id:       rule.id,
    email:    rule.email,
    min_size: rule.minSize,
    side:     rule.side,
    keyword:  rule.keyword,
    wallet:   rule.wallet,
  });

  // Reset inputs (keep email)
  $('alertKeyword').value = '';
  $('alertWallet').value  = '';

  renderAlertRules();
  showToast({ tier: 'INFO', whale: { handle: '✅ Rule saved' }, market: `Min $${minSize.toLocaleString()} · ${side} · ${keyword || 'Any market'} · 📧 ${email}`, outcome: 'OK', usdValue: 0, timestamp: Date.now() });
};

window.deleteAlertRule = function (id) {
  const rules = loadAlertRules().filter(r => r.id !== id);
  saveAlertRules(rules);
  deleteRuleFromBackend(id);
  renderAlertRules();
};

function renderAlertRules() {
  const rules = loadAlertRules();
  const list  = $('alertsRulesList');

  if (!rules.length) {
    list.innerHTML = `<div class="briefing-empty" id="alertsEmpty">
      <span style="font-size:32px">🔕</span>
      <div style="margin-top:8px;font-weight:700;color:var(--text-primary)">No rules saved yet</div>
      <div style="margin-top:4px;color:var(--text-muted);font-size:12px">Create a rule above to get notified by email and in-browser when whales match your criteria.</div>
    </div>`;
    return;
  }

  list.innerHTML = rules.map(r => {
    const parts = [
      `Min $${(r.minSize || 0).toLocaleString()}`,
      r.side !== 'both' ? r.side + ' only' : 'YES & NO',
      r.keyword ? `"${r.keyword}"` : '',
      r.wallet  ? `wallet: ${r.wallet.slice(0, 8)}…` : '',
    ].filter(Boolean).join(' · ');

    const emailBadge = r.email
      ? `<span style="font-size:10px;color:var(--mint);margin-left:4px">📧 ${r.email}</span>`
      : `<span style="font-size:10px;color:var(--rose);margin-left:4px">⚠️ Browser only</span>`;

    return `<div class="alert-rule-row">
      <div class="alert-rule-info">
        <span class="alert-rule-label">${parts}</span>
        ${emailBadge}
        <span class="alert-rule-date">Added ${r.created}</span>
      </div>
      <button class="alert-rule-delete" onclick="deleteAlertRule('${r.id}')">✕</button>
    </div>`;
  }).join('');
}


/** Called from the WebSocket onmessage handler for every incoming trade event */
function checkEventAgainstRules(ev) {
  const rules = loadAlertRules();
  if (!rules.length) return;

  for (const rule of rules) {
    const sizeOk    = ev.usdValue >= rule.minSize;
    const sideOk    = rule.side === 'both' || ev.outcome === rule.side;
    const keywordOk = !rule.keyword || (ev.market || '').toLowerCase().includes(rule.keyword);
    const walletOk  = !rule.wallet || (ev.whale?.wallet || '').toLowerCase().includes(rule.wallet);

    if (sizeOk && sideOk && keywordOk && walletOk) {
      // In-app alert toast (always fires)
      showToast({
        ...ev,
        tier: 'WHALE',
        market: `🔔 Alert: ${ev.market}`,
      });

      // Browser push notification (fires if permission granted)
      if (Notification.permission === 'granted') {
        new Notification(`🐋 PolyVision Alert`, {
          body: `${ev.whale?.handle || 'Whale'} — $${Math.round(ev.usdValue).toLocaleString()} ${ev.outcome} on "${ev.market}"`,
          icon: '/favicon.ico',
        });
      }
      break; // only fire one alert per trade even if multiple rules match
    }
  }
}

// ── OneSignal Push Notification Integration ────────────────────────────────────

// Replace 'YOUR_ONESIGNAL_APP_ID' below with your real App ID from onesignal.com
const ONESIGNAL_APP_ID = '94643e9f-b6f2-4682-91f3-7484780f933e';

function initPushNotifications() {
  const btn = $('btnNotify');
  if (!btn) return;

  // If no real App ID has been set yet, update the button to show setup pending
  if (!ONESIGNAL_APP_ID || ONESIGNAL_APP_ID === 'YOUR_ONESIGNAL_APP_ID') {
    btn.textContent = '🔔 Alerts (Setup Required)';
    btn.title = 'Set your OneSignal App ID in app.js to enable push notifications';
    btn.disabled = true;
    btn.style.opacity = '0.5';
    return;
  }

  // Wait for OneSignal SDK to load
  window.OneSignalDeferred = window.OneSignalDeferred || [];
  window.OneSignalDeferred.push(async function (OneSignal) {
    await OneSignal.init({
      appId: ONESIGNAL_APP_ID,
      serviceWorkerPath: 'OneSignalSDKWorker.js',
      notifyButton: { enable: false },   // we use our own button
      allowLocalhostAsSecureOrigin: true, // allow local dev server
    });

    // Sync button state to current subscription status
    async function syncButtonState() {
      const isSubscribed = await OneSignal.User.PushSubscription.optedIn;
      if (isSubscribed) {
        btn.textContent = '🔕 Unsubscribe';
        btn.classList.add('subscribed');
      } else {
        btn.textContent = '🔔 Subscribe to Alerts';
        btn.classList.remove('subscribed');
      }
    }

    await syncButtonState();

    // Toggle subscription on click
    btn.addEventListener('click', async () => {
      const isSubscribed = await OneSignal.User.PushSubscription.optedIn;
      if (isSubscribed) {
        await OneSignal.User.PushSubscription.optOut();
        showToast({ tier: 'INFO', whale: { handle: 'Notifications' }, market: 'Whale alerts unsubscribed.', outcome: 'OK', usdValue: 0, timestamp: Date.now() });
      } else {
        await OneSignal.login(crypto.randomUUID());   // anonymous user ID
        await OneSignal.User.PushSubscription.optIn();
        showToast({ tier: 'INFO', whale: { handle: '🔔 Subscribed!' }, market: 'You\'ll receive whale alerts instantly.', outcome: 'OK', usdValue: 0, timestamp: Date.now() });
      }
      await syncButtonState();
    });
  });
}

// Init push after DOM and app are ready
document.addEventListener('DOMContentLoaded', initPushNotifications);
