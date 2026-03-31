const BRAIN_URL = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
  ? 'http://localhost:8000/api'
  : 'https://polyvision-production.up.railway.app/api';

let currentPlatform = 'polymarket';

function setPlatform(platform) {
  currentPlatform = platform;
  const input = document.getElementById('wallet-input');
  const kalshiInfo = document.getElementById('kalshi-info');
  const btnPoly = document.getElementById('btn-poly');
  const btnKalshi = document.getElementById('btn-kalshi');

  if (platform === 'kalshi') {
    btnPoly.className = '';
    btnKalshi.className = 'active-kalshi';
    input.placeholder = 'Enter the handle shown on your Kalshi trades...';
    kalshiInfo.style.display = 'block';
  } else {
    btnPoly.className = 'active-poly';
    btnKalshi.className = '';
    input.placeholder = 'Enter Polymarket username or wallet address...';
    kalshiInfo.style.display = 'none';
  }

  // Clear any previous results when switching
  document.getElementById('artifact').style.display = 'none';
  document.getElementById('action-buttons').style.display = 'none';
  document.getElementById('error-msg').style.display = 'none';
}

async function analyzeWallet() {
  const input = document.getElementById('wallet-input').value.trim();
  if (!input) return;

  const btn = document.querySelector('.search-box button');
  const err = document.getElementById('error-msg');
  const load = document.getElementById('loading');
  const artifact = document.getElementById('artifact');
  const actions = document.getElementById('action-buttons');

  btn.disabled = true;
  err.style.display = 'none';
  artifact.style.display = 'none';
  actions.style.display = 'none';
  load.style.display = 'block';

  try {
    const url = `${BRAIN_URL}/analyze-wallet?query=${encodeURIComponent(input)}&platform=${currentPlatform}`;
    let res = await fetch(url);

    // Polymarket-only fallback: if backend doesn't find a username and it's not a 0x address,
    // the backend now handles resolution server-side via the Polymarket leaderboard API.
    // No client-side Gamma API proxy needed.

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || 'Wallet not found or no historical data available.');
    }

    const data = await res.json();
    renderArtifact(data);

    load.style.display = 'none';
    artifact.style.display = 'block';
    actions.style.display = 'flex';
  } catch (e) {
    load.style.display = 'none';
    err.textContent = e.message || 'Failed to analyze wallet.';
    err.style.display = 'block';
  } finally {
    btn.disabled = false;
  }
}

function animateValue(obj, start, end, duration, formatFn) {
  let startTimestamp = null;
  const step = (timestamp) => {
    if (!startTimestamp) startTimestamp = timestamp;
    const progress = Math.min((timestamp - startTimestamp) / duration, 1);
    // Ease out quad
    const easeProgress = progress * (2 - progress);
    const value = easeProgress * (end - start) + start;
    obj.innerHTML = formatFn(value);
    if (progress < 1) {
      window.requestAnimationFrame(step);
    }
  };
  window.requestAnimationFrame(step);
}

function renderArtifact(data) {
  const handle = data.handle || 'Unknown Whale';
  document.getElementById('lbl-handle').textContent = handle;
  document.getElementById('lbl-address').textContent = data.wallet_address || '';

  // Source badge — Kalshi gets blue, Polymarket gets mint
  const sourceBadge = document.getElementById('lbl-source');
  const src = (data.source || 'POLYMARKET').toUpperCase();
  sourceBadge.textContent = src;
  sourceBadge.className = src.includes('KALSHI') ? 'source-badge kalshi' : 'source-badge polymarket';

  // Win Rate
  const wr = data.win_rate;
  const wrEl = document.getElementById('lbl-winrate');
  const wrPct = (wr * 100);
  wrEl.className = wr < 0.5 ? 'stat-val negative' : 'stat-val';
  if (wrPct >= 99.5) {
    wrEl.textContent = '99%+';
  } else {
    animateValue(wrEl, 0, wrPct, 1200, (v) => `${v.toFixed(0)}%`);
  }

  // ROI
  const roi = data.roi_all_time;
  const roiEl = document.getElementById('lbl-roi');
  const roiPct = (roi * 100);
  roiEl.className = roi < 0 ? 'stat-val negative' : 'stat-val';
  if (roiPct >= 99.5) {
    roiEl.textContent = '+99%+';
  } else {
    animateValue(roiEl, 0, roiPct, 1200, (v) => `${v > 0 ? '+' : ''}${v.toFixed(1)}%`);
  }

  // Volume & Trades
  const volEl = document.getElementById('lbl-volume');
  animateValue(volEl, 0, Math.round(data.total_volume), 1200, (v) => `$${Math.round(v).toLocaleString()}`);
  
  const tradesEl = document.getElementById('lbl-trades');
  animateValue(tradesEl, 0, data.total_trades, 1200, (v) => Math.round(v).toLocaleString());

  // Top Trades
  const body = document.getElementById('trades-list-body');
  body.innerHTML = '';

  if (!data.best_trades || data.best_trades.length === 0) {
    body.innerHTML = '<div style="color:var(--muted); font-size:13px; text-align:center;">No verified winning trades found.</div>';
  } else {
    data.best_trades.forEach(t => {
      const el = document.createElement('div');
      el.className = 'trade-item';

      const title = document.createElement('div');
      title.className = 'trade-title';
      title.textContent = `[${t.outcome}] ${t.market_title}`;

      const profit = document.createElement('div');
      profit.className = 'trade-profit';
      profit.textContent = `+$${Math.round(t.profit).toLocaleString()}`;

      el.appendChild(title);
      el.appendChild(profit);
      body.appendChild(el);
    });
  }
}

async function downloadArtifact() {
  const artifact = document.getElementById('artifact');
  const btn = document.querySelector('.btn-share');

  try {
    const originalText = btn.textContent;
    btn.textContent = '📸 Generating...';
    btn.disabled = true;

    const canvas = await html2canvas(artifact, {
      backgroundColor: '#050505',
      scale: 2,
      useCORS: true,
      allowTaint: true
    });

    const dataUrl = canvas.toDataURL('image/png');
    const link = document.createElement('a');
    link.download = 'whale_grade.png';
    link.href = dataUrl;
    link.click();

    btn.textContent = '✅ Downloaded!';
    setTimeout(() => { btn.textContent = originalText; btn.disabled = false; }, 2000);
  } catch (err) {
    console.error(err);
    alert('Failed to generate image. Try screenshotting instead!');
    btn.textContent = '📸 Error! Try Screenshotting';
    btn.disabled = false;
  }
}

document.getElementById('wallet-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    analyzeWallet();
  }
});
