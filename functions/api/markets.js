/**
 * Cloudflare Pages Function: /api/markets
 *
 * Proxies the Polymarket gamma-api from Cloudflare Edge IPs.
 * Runs on Cloudflare's global network — not blocked by Polymarket
 * the way Railway/AWS/GCP IPs are.
 */
export async function onRequest(context) {
  const { request } = context;

  // Handle CORS preflight
  if (request.method === 'OPTIONS') {
    return new Response(null, {
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
      },
    });
  }

  const url = new URL(request.url);
  const limit  = url.searchParams.get('limit')  || '60';
  const order  = url.searchParams.get('order')  || 'volume24hr';
  const asc    = url.searchParams.get('ascending') || 'false';

  const upstream = `https://gamma-api.polymarket.com/markets?limit=${limit}&order=${order}&ascending=${asc}&active=true`;

  try {
    const resp = await fetch(upstream, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'application/json',
      },
    });

    if (!resp.ok) {
      return new Response(JSON.stringify({ error: `Upstream error: ${resp.status}` }), {
        status: 502,
        headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
      });
    }

    const data = await resp.json();
    return new Response(JSON.stringify(data), {
      headers: {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Cache-Control': 'public, max-age=120',  // cache for 2 minutes at edge
      },
    });
  } catch (err) {
    return new Response(JSON.stringify({ error: err.message }), {
      status: 500,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
    });
  }
}
