const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const { URL } = require('url');

const ENV_PATH = path.join(__dirname, '..', '.env');
const ADMIN_SECRET = process.env.ADMIN_SECRET || 'cyberstrike-admin';

// Load keys from .env
function loadKeys() {
  const keys = [];
  if (fs.existsSync(ENV_PATH)) {
    const env = fs.readFileSync(ENV_PATH, 'utf8');
    env.split('\n').forEach(line => {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) return;
      const m = trimmed.match(/^(?:KEY_\d+|OPENCODE_KEY_\d+|API_KEY_\d+)=(.*)$/);
      if (m && m[1].trim()) {
        const val = m[1].trim().replace(/^['"]|['"]$/g, '');
        keys.push({
          email: `account${keys.length + 1}@opencode.zen`,
          key: val,
          session_status: 'active',
          access_tier: 'free',
          daily_limit: 200,
          today_used: 0,
          last_usage: null,
          index: keys.length
        });
      }
    });
  }
  return keys;
}

// Persist keys back to .env
function saveKeys(keys) {
  try {
    const lines = keys.map((k, i) => `KEY_${i + 1}=${k.key}`);
    fs.writeFileSync(ENV_PATH, lines.join('\n') + '\n', 'utf8');
  } catch (e) {
    console.error('Failed to persist keys to .env:', e.message);
  }
}

let tokensData = loadKeys();
let currentIndex = 0;

function getNextKey() {
  if (tokensData.length === 0) return null;
  const keyObj = tokensData[currentIndex % tokensData.length];
  currentIndex = (currentIndex + 1) % tokensData.length;
  keyObj.today_used++;
  keyObj.last_usage = new Date().toISOString();
  return keyObj;
}

function verifyAdminAuth(req) {
  const authHeader = req.headers['authorization'] || req.headers['x-admin-key'];
  if (!authHeader) return false;
  const token = authHeader.replace(/^Bearer\s+/i, '').trim();
  return token === ADMIN_SECRET;
}

function handleRequest(req, res) {
  const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);

  // CORS Headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Admin-Key');

  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return;
  }

  // Admin API - list tokens (GET)
  if (url.pathname === '/admin/api/tokens' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ tokens: tokensData, count: tokensData.length }));
    return;
  }

  // Admin API - add token (POST)
  if (url.pathname === '/admin/api/tokens' && req.method === 'POST') {
    if (!verifyAdminAuth(req)) {
      res.writeHead(401, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Unauthorized. Provide valid Authorization or X-Admin-Key header.' }));
      return;
    }

    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      try {
        const data = JSON.parse(body);
        if (data.key) {
          const newToken = {
            email: data.email || `account${tokensData.length + 1}@opencode.zen`,
            key: data.key,
            session_status: 'active',
            access_tier: data.access_tier || 'free',
            daily_limit: data.daily_limit || 200,
            today_used: 0,
            last_usage: null,
            index: tokensData.length
          };
          tokensData.push(newToken);
          saveKeys(tokensData);
          res.writeHead(201, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: true, index: newToken.index, count: tokensData.length }));
        } else {
          res.writeHead(400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'key is required' }));
        }
      } catch (e) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'invalid JSON' }));
      }
    });
    return;
  }

  // Admin API - delete token (DELETE)
  if (url.pathname.startsWith('/admin/api/tokens/') && req.method === 'DELETE') {
    if (!verifyAdminAuth(req)) {
      res.writeHead(401, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Unauthorized. Provide valid Authorization or X-Admin-Key header.' }));
      return;
    }

    const idx = parseInt(url.pathname.split('/').pop(), 10);
    if (!isNaN(idx) && tokensData[idx]) {
      tokensData.splice(idx, 1);
      tokensData.forEach((t, i) => t.index = i);
      saveKeys(tokensData);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, count: tokensData.length }));
    } else {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Token index not found' }));
    }
    return;
  }

  // OpenAI-compatible /v1/models
  if (url.pathname === '/v1/models' && req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      object: 'list',
      data: [
        { id: 'mimo-v2.6-flash-free', object: 'model', owned_by: 'opencode' },
        { id: 'space-bunny-free', object: 'model', owned_by: 'opencode' },
        { id: 'ling-3.0-flash-fin-free', object: 'model', owned_by: 'opencode' },
        { id: 'nemotron-3.5-lightning-free', object: 'model', owned_by: 'opencode' },
        { id: 'deepseek-v4-flash-free', object: 'model', owned_by: 'opencode' },
        { id: 'big-pickle', object: 'model', owned_by: 'opencode' },
        { id: 'muse-spark-1.3-contributor-free', object: 'model', owned_by: 'opencode' }
      ]
    }));
    return;
  }

  // OpenAI-compatible /v1/chat/completions (Live Forwarding Proxy)
  if (url.pathname === '/v1/chat/completions' && req.method === 'POST') {
    const keyObj = getNextKey();
    if (!keyObj) {
      res.writeHead(503, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'No API keys configured in pool. Add keys to .env or via /admin/api/tokens.' }));
      return;
    }

    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', () => {
      const upstreamUrl = new URL('https://opencode.ai/zen/v1/chat/completions');
      const isStream = req.headers['accept']?.includes('text/event-stream') || body.includes('"stream":true');

      const options = {
        hostname: upstreamUrl.hostname,
        port: 443,
        path: upstreamUrl.pathname,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${keyObj.key}`,
          'User-Agent': 'OpenCode-Proxy/2.0'
        }
      };

      const proxyReq = https.request(options, (proxyRes) => {
        res.writeHead(proxyRes.statusCode, proxyRes.headers);
        proxyRes.pipe(res, { end: true });
      });

      proxyReq.on('error', (err) => {
        console.error(`Proxy upstream error using key index ${keyObj.index}:`, err.message);
        if (!res.headersSent) {
          res.writeHead(502, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'Upstream gateway error', details: err.message }));
        }
      });

      proxyReq.write(body);
      proxyReq.end();
    });
    return;
  }

  // Serve dashboard
  if (url.pathname === '/' || url.pathname === '/dashboard') {
    const dashboardPath = path.join(__dirname, '..', 'freebuff-dashboard.html');
    if (fs.existsSync(dashboardPath)) {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end(fs.readFileSync(dashboardPath));
    } else {
      res.writeHead(404, { 'Content-Type': 'text/plain' });
      res.end('Dashboard HTML file not found.');
    }
    return;
  }

  res.writeHead(404, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ error: 'Endpoint not found' }));
}

const PORT = process.env.PROXY_PORT || 8099;
const HOST = process.env.PROXY_HOST || '127.0.0.1';

http.createServer(handleRequest).listen(PORT, HOST, () => {
  console.log(`[CyberStrike Proxy] Running on http://${HOST}:${PORT}`);
  console.log(`[CyberStrike Proxy] Dashboard: http://${HOST}:${PORT}/`);
  console.log(`[CyberStrike Proxy] Admin API: http://${HOST}:${PORT}/admin/api/tokens`);
  console.log(`[CyberStrike Proxy] Loaded ${tokensData.length} key(s) from .env`);
});
