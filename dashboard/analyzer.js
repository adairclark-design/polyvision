const BRAIN_URL = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
  ? 'http://localhost:8000/api'
  : 'https://polyvision-production.up.railway.app/api';

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
    let targetQuery = input;
    let res = await fetch(`${BRAIN_URL}/analyze-wallet?query=${encodeURIComponent(targetQuery)}`);
    
    // If backend doesn't find the username/handle natively, use Polymarket API as a historical fallback proxy
    if (!res.ok && res.status === 404 && !input.startsWith('0x')) {
      try {
        const pmRes = await fetch(`https://gamma-api.polymarket.com/profiles?username=${encodeURIComponent(input)}`);
        if (pmRes.ok) {
          const pmData = await pmRes.json();
          const pf = Array.isArray(pmData) ? pmData[0] : pmData;
          if (pf && pf.proxyWallet) {
            targetQuery = pf.proxyWallet;
            res = await fetch(`${BRAIN_URL}/analyze-wallet?query=${encodeURIComponent(targetQuery)}`);
          }
        }
      } catch (proxyErr) {
        console.warn('Proxy fallback failed:', proxyErr);
      }
    }

    if (!res.ok) {
      throw new Error("Wallet not found or no historical data available.");
    }
    
    const data = await res.json();
    
    // If the proxy succeeded, replace the backend's generated fake handle with the real searched username
    if (targetQuery !== input && data.handle) {
      data.handle = input;
    }
    
    renderArtifact(data);

    load.style.display = 'none';
    artifact.style.display = 'block';
    actions.style.display = 'flex';
  } catch (e) {
    load.style.display = 'none';
    err.textContent = e.message || "Failed to analyze wallet.";
    err.style.display = 'block';
  } finally {
    btn.disabled = false;
  }
}

function renderArtifact(data) {
  document.getElementById('lbl-handle').textContent = data.handle || 'Unknown Whale';
  document.getElementById('lbl-address').textContent = data.wallet_address || '';
  document.getElementById('lbl-source').textContent = (data.source || 'POLYGON').toUpperCase();

  // Win Rate
  const wr = data.win_rate;
  const wrEl = document.getElementById('lbl-winrate');
  wrEl.textContent = `${(wr * 100).toFixed(0)}%`;
  wrEl.className = wr < 0.5 ? 'stat-val negative' : 'stat-val';

  // ROI
  const roi = data.roi_all_time;
  const roiEl = document.getElementById('lbl-roi');
  roiEl.textContent = `${roi > 0 ? '+' : ''}${(roi * 100).toFixed(1)}%`;
  roiEl.className = roi < 0 ? 'stat-val negative' : 'stat-val';

  // Volume
  document.getElementById('lbl-volume').textContent = `$${Math.round(data.total_volume).toLocaleString()}`;
  document.getElementById('lbl-trades').textContent = data.total_trades.toLocaleString();

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
      backgroundColor: '#161b22', // Match --bg2
      scale: 2, // High-res
      useCORS: true
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
