import os
import re
import json
import logging
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    log.error("DATABASE_URL missing.")
    exit(1)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{handle} Prediction Market Win Rate & Stats | PolyVision</title>
  <meta name="description" content="View the historic Polymarket/Kalshi win rate and top prediction market trades for {handle} ({wallet_address}).">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <script type="application/ld+json">
  {schema_json}
  </script>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800;900&display=swap" rel="stylesheet" />
  <style>
    body {{ font-family: 'Inter', sans-serif; background: #0d1117; color: #e6edf3; padding: 40px 20px; }}
    .container {{ max-width: 600px; margin: 0 auto; background: #161b22; padding: 30px; border-radius: 12px; border: 1px solid rgba(255,255,255,0.08); }}
    h1 {{ color: #00ffa3; font-size: 28px; margin-bottom: 8px; }}
    .meta {{ font-family: monospace; font-size: 12px; color: #8b949e; margin-bottom: 24px; word-break: break-all; }}
    .stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }}
    .stat {{ background: #21262d; padding: 16px; border-radius: 8px; text-align: center; }}
    .val {{ font-size: 24px; font-weight: 800; color: #00ffa3; }}
    .lbl {{ font-size: 11px; text-transform: uppercase; color: #8b949e; margin-top: 4px; }}
    h2 {{ font-size: 16px; margin-bottom: 12px; border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 8px; }}
    .trade {{ display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px solid rgba(255,255,255,0.03); gap: 12px;}}
    .trade-title {{ flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    .cta {{ background: #00ffa3; color: #000; padding: 12px; border-radius: 8px; text-align: center; font-weight: bold; text-decoration: none; display: block; margin-top: 30px; transition: opacity 0.2s; }}
    .cta:hover {{ opacity: 0.9; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>{handle} Stats</h1>
    <div class="meta">Wallet: {wallet_address} | Source: {source}</div>
    <div class="stats">
      <div class="stat"><div class="val">{win_rate}</div><div class="lbl">Win Rate</div></div>
      <div class="stat"><div class="val">{roi}</div><div class="lbl">All-Time ROI</div></div>
    </div>
    <h2>Top Verified Winning Trades</h2>
    <div class="trades">{trades_html}</div>
    <a href="../analyzer.html" class="cta">Grade Your Own Wallet's Win Rate for Free →</a>
  </div>
</body>
</html>
"""

def generate():
    out_dir = os.path.join(os.path.dirname(__file__), "../../dashboard/whales")
    os.makedirs(out_dir, exist_ok=True)
    try:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Top 5,000 wallets with at least 5 resolved trades
                cur.execute("""
                    SELECT w.wallet_address, w.handle, w.source, w.win_rate, w.roi_all_time,
                           COUNT(t.id) as resolved_count
                    FROM wallets w
                    JOIN trades t ON t.wallet_address = w.wallet_address
                    WHERE t.resolved = TRUE
                    GROUP BY w.wallet_address
                    HAVING COUNT(t.id) >= 1
                    ORDER BY resolved_count DESC
                    LIMIT 5000;
                """)
                wallets = cur.fetchall()
                
                log.info(f"Generating SEO pages for {len(wallets)} whales...")
                generated = 0
                for w in wallets:
                    w_addr = w['wallet_address']
                    cur.execute("""
                        SELECT market_title, outcome, size as profit
                        FROM trades
                        WHERE wallet_address = %s AND resolved = TRUE AND won = TRUE
                        ORDER BY size DESC LIMIT 5;
                    """, (w_addr,))
                    top_trades = cur.fetchall()
                    
                    trades_html = ""
                    if top_trades:
                        for t in top_trades:
                            pft = float(t['profit'])
                            trades_html += f"<div class='trade'><span class='trade-title'>[{t['outcome']}] {t['market_title']}</span><span style='color:#00ffa3'>+${pft:,.0f}</span></div>"
                    else:
                        trades_html = "<div style='color:#8b949e;font-size:12px;text-align:center;'>Wait for trades to resolve.</div>"
                        
                    schema = {
                        "@context": "https://schema.org",
                        "@type": "ProfilePage",
                        "mainEntity": {
                            "@type": "Person",
                            "name": w['handle'],
                            "identifier": w_addr
                        }
                    }
                    
                    wr_val = w['win_rate']
                    roi_val = w['roi_all_time']
                    
                    html_content = HTML_TEMPLATE.format(
                        handle=w['handle'],
                        wallet_address=w_addr,
                        source=w['source'],
                        schema_json=json.dumps(schema, indent=2),
                        win_rate=f"{float(wr_val)*100:.0f}%" if wr_val else "0%",
                        roi=f"{float(roi_val)*100:+.1f}%" if roi_val else "0%",
                        trades_html=trades_html
                    )
                    
                    safe_addr = re.sub(r'[^a-zA-Z0-9_-]', '', w_addr)
                    with open(os.path.join(out_dir, f"{safe_addr}.html"), "w", encoding="utf-8") as f:
                        f.write(html_content)
                    
                    generated += 1

                log.info(f"Successfully generated {generated} SEO pages.")
    except Exception as e:
        log.error(f"Failed to generate SEO directory: {e}")

if __name__ == "__main__":
    generate()
