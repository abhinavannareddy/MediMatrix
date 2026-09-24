// ===========================================================================
//  MediMatrx - API GATEWAY
// ---------------------------------------------------------------------------
//  Job in one sentence:
//     "I am the only door into the system. The web browser talks to me and
//      nobody else. I serve the dashboard, and I forward each API call to
//      whichever internal microservice owns that job."
//
//  This is the "API Gateway" cloud pattern. Why bother?
//    1. The browser needs ONE address, not four.
//    2. The internal services stay private - they are never exposed to the
//       internet, so their attack surface is zero from outside.
//    3. Security work (rate limiting, headers, authentication) is done once,
//       here, instead of being copy-pasted into every service.
//    4. Internal services can be renamed, rewritten or split up without the
//       browser ever noticing.
// ===========================================================================

const express = require('express');
const path = require('path');
const os = require('os');

const app = express();
app.use(express.json({ limit: '256kb' }));
app.disable('x-powered-by');   // do not advertise what we are running

const PORT = process.env.PORT || 8080;
const POD = process.env.POD_NAME || os.hostname();

// Where the internal services live. In Kubernetes these are DNS names that
// the cluster resolves to a load-balanced set of pods.
const INGEST_URL = process.env.INGEST_URL || 'http://localhost:8081';
const PRICE_URL = process.env.PRICE_URL || 'http://localhost:8082';
const OPTIMIZER_URL = process.env.OPTIMIZER_URL || 'http://localhost:8083';
const ASSISTANT_URL = process.env.ASSISTANT_URL || 'http://localhost:8084';
const FORECAST_URL = process.env.FORECAST_URL || 'http://localhost:8085';
const AUTH_URL = process.env.AUTH_URL || 'http://localhost:8086';
const VERIFICATION_URL = process.env.VERIFICATION_URL || 'http://localhost:8087';
const VALIDATION_URL = process.env.VALIDATION_URL || 'http://localhost:8088';
const AUTHORIZATION_URL = process.env.AUTHORIZATION_URL || 'http://localhost:8089';
const UPSTREAM_TIMEOUT_MS = parseInt(process.env.UPSTREAM_TIMEOUT_MS || '8000', 10);

function log(level, message, extra = {}) {
  console.log(JSON.stringify({
    ts: new Date().toISOString(), level, service: 'gateway', pod: POD, message, ...extra
  }));
}

// ---------------------------------------------------------------------------
//  SECURITY LAYER 1 - response headers
//  These tell the browser to lock things down. They cost nothing and they
//  block whole families of attack (clickjacking, MIME sniffing, XSS).
// ---------------------------------------------------------------------------
app.use((req, res, next) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'DENY');
  res.setHeader('Referrer-Policy', 'no-referrer');
  res.setHeader('Permissions-Policy', 'geolocation=(), microphone=(), camera=()');
  res.setHeader('Content-Security-Policy',
    "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'");
  next();
});

// ---------------------------------------------------------------------------
//  SECURITY LAYER 2 - rate limiting
//  A simple fixed-window counter per client IP. It stops one noisy or
//  malicious client from flooding the hospital's optimizer.
//
//  Honest limitation: this counter lives in the memory of ONE pod, so with
//  several gateway replicas the real limit is (limit x replicas). In
//  production you move this to Redis or to an ingress-level rate limiter.
//  It is documented in the report as a known trade-off.
// ---------------------------------------------------------------------------
const RATE_LIMIT = parseInt(process.env.RATE_LIMIT || '120', 10);  // requests
const RATE_WINDOW_MS = 60 * 1000;                                   // per minute
const hits = new Map();

setInterval(() => hits.clear(), RATE_WINDOW_MS).unref();

app.use('/api', (req, res, next) => {
  const ip = req.ip || 'unknown';
  const count = (hits.get(ip) || 0) + 1;
  hits.set(ip, count);
  res.setHeader('X-RateLimit-Limit', RATE_LIMIT);
  res.setHeader('X-RateLimit-Remaining', Math.max(RATE_LIMIT - count, 0));
  if (count > RATE_LIMIT) {
    log('warn', 'rate limit exceeded', { ip, count });
    return res.status(429).json({ error: 'too many requests', retryAfterSeconds: 60 });
  }
  next();
});

// Request logging
app.use((req, res, next) => {
  const started = Date.now();
  res.on('finish', () => {
    log('info', 'request', {
      method: req.method, path: req.originalUrl,
      status: res.statusCode, ms: Date.now() - started
    });
  });
  next();
});

// ---------------------------------------------------------------------------
//  The proxy helper
//  Forwards a call to an internal service, with a hard timeout so a slow
//  service can never hang the browser. Timeout + clear error = "fail fast",
//  which is what keeps a distributed system usable when one part is sick.
// ---------------------------------------------------------------------------
async function proxy(res, targetUrl, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);
  try {
    const upstream = await fetch(targetUrl, { ...options, signal: controller.signal });
    const body = await upstream.text();
    res.status(upstream.status);
    res.setHeader('Content-Type', upstream.headers.get('content-type') || 'application/json');
    res.setHeader('X-Gateway-Pod', POD);
    res.send(body);
  } catch (err) {
    const timedOut = err.name === 'AbortError';
    log('error', 'upstream call failed', { targetUrl, error: err.message, timedOut });
    res.status(502).json({
      error: timedOut ? 'upstream service timed out' : 'upstream service unavailable',
      target: targetUrl.replace(/\/\/.*@/, '//'),   // never leak credentials
      gatewayPod: POD
    });
  } finally {
    clearTimeout(timer);
  }
}

// Like proxy(), but returns the parsed JSON body to the caller instead of
// writing straight to the response - used when the gateway needs to look
// at the answer (authorize, validate) before deciding what to send back.
async function callJSON(targetUrl, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);
  try {
    const upstream = await fetch(targetUrl, { ...options, signal: controller.signal });
    const body = await upstream.json().catch(() => ({}));
    return { ok: upstream.ok, status: upstream.status, body };
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
//  SECURITY LAYER 3 - authentication + authorization
//
//  Every /api route except /api/auth/* requires a bearer token. The gateway
//  never decides for itself whether a token is valid or a role is allowed
//  to do something - it asks authorization-service, which is the one place
//  that knows the signing secret and the permission rules. This keeps the
//  gateway's own job simple: front door, not judge.
//
//  "action" is deliberately coarse - "read" or "write" - because that is
//  the distinction the platform's roles actually need (see
//  services/authorization/main.py). A finer-grained scheme would live
//  there, not here.
// ---------------------------------------------------------------------------
function actionFor(req) {
  // req.path is relative to the router's mount point - inside this
  // middleware (mounted at app.use('/api', requireAuth)) that means the
  // leading "/api" is already gone, so we check req.originalUrl instead,
  // which always holds the full path the browser actually requested.
  const path = req.originalUrl.split('?')[0];
  const mutating = req.method === 'POST' && (path === '/api/readings' || path === '/api/simulate');
  return mutating ? 'write' : 'read';
}

async function requireAuth(req, res, next) {
  const header = req.headers['authorization'] || '';
  const token = header.startsWith('Bearer ') ? header.slice(7) : header;
  if (!token) {
    return res.status(401).json({ authorized: false, error: 'missing bearer token' });
  }

  try {
    const { status, body } = await callJSON(`${AUTHORIZATION_URL}/api/authorize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, action: actionFor(req) })
    });
    if (!body.authorized) {
      return res.status(status || 401).json(body);
    }
    req.user = { username: body.username, role: body.role };
    next();
  } catch (err) {
    log('error', 'authorization check failed', { error: err.message });
    res.status(502).json({ authorized: false, error: 'authorization-service unavailable' });
  }
}

// ===========================================================================
//  HEALTH
// ===========================================================================
app.get('/healthz', (req, res) => res.json({ status: 'alive', service: 'gateway', pod: POD }));
app.get('/readyz', (req, res) => res.json({ status: 'ready', service: 'gateway', pod: POD }));

// ===========================================================================
//  ROUTES -> AUTH / VERIFICATION SERVICES
//  The only /api routes that do NOT require a bearer token - you cannot be
//  authorized before you have an identity to check.
// ===========================================================================
app.post('/api/auth/register', async (req, res) => {
  const check = await callJSON(`${VALIDATION_URL}/api/validate/registration`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req.body || {})
  });
  if (!check.body.valid) {
    return res.status(check.status || 400).json(check.body);
  }
  return proxy(res, `${AUTH_URL}/api/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req.body || {})
  });
});

app.post('/api/auth/login', (req, res) => proxy(res, `${AUTH_URL}/api/login`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(req.body || {})
}));

app.post('/api/auth/verify', (req, res) => proxy(res, `${VERIFICATION_URL}/api/confirm`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(req.body || {})
}));

// Everything below this line requires a valid, authorized token.
app.use('/api', requireAuth);

// ===========================================================================
//  ROUTES -> INGEST SERVICE
// ===========================================================================
app.get('/api/zones', (req, res) => proxy(res, `${INGEST_URL}/api/zones`));
app.get('/api/summary', (req, res) => proxy(res, `${INGEST_URL}/api/summary`));

app.get('/api/readings', (req, res) => {
  const zone = req.query.zone ? `zone=${encodeURIComponent(req.query.zone)}&` : '';
  const limit = encodeURIComponent(req.query.limit || 50);
  return proxy(res, `${INGEST_URL}/api/readings?${zone}limit=${limit}`);
});

app.post('/api/readings', async (req, res) => {
  const check = await callJSON(`${VALIDATION_URL}/api/validate/reading`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req.body || {})
  });
  if (!check.body.valid) {
    return res.status(check.status || 400).json(check.body);
  }
  return proxy(res, `${INGEST_URL}/api/readings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req.body || {})
  });
});

app.post('/api/simulate', (req, res) => proxy(res, `${INGEST_URL}/api/simulate`, { method: 'POST' }));

// ===========================================================================
//  ROUTES -> PRICE SERVICE
// ===========================================================================
app.get('/api/prices', (req, res) => {
  const area = encodeURIComponent(req.query.area || 'SE4');
  const day = req.query.day === 'tomorrow' ? '&day=tomorrow' : '';
  return proxy(res, `${PRICE_URL}/api/prices?area=${area}${day}`);
});

app.get('/api/prices/cheapest-window', (req, res) => {
  const area = encodeURIComponent(req.query.area || 'SE4');
  const hours = encodeURIComponent(req.query.hours || 3);
  return proxy(res, `${PRICE_URL}/api/prices/cheapest-window?area=${area}&hours=${hours}`);
});

// ===========================================================================
//  ROUTES -> OPTIMIZER SERVICE
// ===========================================================================
app.get('/api/optimize', (req, res) => {
  const area = encodeURIComponent(req.query.area || 'SE4');
  // Optional what-if override, e.g. flex=laundry:0.9. The optimizer validates
  // it strictly (known deferrable zones only, never a clinical one), so the
  // gateway just needs to pass it through safely encoded.
  const flex = req.query.flex ? `&flex=${encodeURIComponent(req.query.flex)}` : '';
  return proxy(res, `${OPTIMIZER_URL}/api/optimize?area=${area}${flex}`);
});

app.get('/api/anomalies', (req, res) => proxy(res, `${OPTIMIZER_URL}/api/anomalies`));

// ===========================================================================
//  ROUTES -> ASSISTANT SERVICE
//  The assistant is read-only by design, but this is still a POST because
//  the question goes in the body rather than the URL - a question can be
//  long, and putting user text in a query string means it ends up in every
//  access log along the way.
// ===========================================================================
app.post('/api/chat', (req, res) => proxy(res, `${ASSISTANT_URL}/api/chat`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(req.body || {})
}));

app.get('/api/chat/suggestions', (req, res) =>
  proxy(res, `${ASSISTANT_URL}/api/chat/suggestions`));

// ===========================================================================
//  ROUTES -> FORECAST SERVICE
//  Everything else in the platform describes yesterday. These describe
//  tomorrow, which is the difference between a report and a plan.
// ===========================================================================
app.get('/api/forecast/weather', (req, res) =>
  proxy(res, `${FORECAST_URL}/api/forecast/weather`));

app.get('/api/forecast/plan', (req, res) => {
  const area = encodeURIComponent(req.query.area || 'SE4');
  return proxy(res, `${FORECAST_URL}/api/forecast/plan?area=${area}`);
});

// ===========================================================================
//  TOPOLOGY - "who is actually running right now?"
//  Calls every service's health endpoint and reports which pod answered.
//  This is what makes horizontal scaling visible in the demo: scale a
//  service up, refresh, and watch different pod names come back.
// ===========================================================================
app.get('/api/topology', async (req, res) => {
  const targets = [
    { name: 'ingest', url: `${INGEST_URL}/healthz` },
    { name: 'price', url: `${PRICE_URL}/healthz` },
    { name: 'optimizer', url: `${OPTIMIZER_URL}/healthz` },
    { name: 'assistant', url: `${ASSISTANT_URL}/healthz` },
    { name: 'forecast', url: `${FORECAST_URL}/healthz` },
    { name: 'auth', url: `${AUTH_URL}/healthz` },
    { name: 'verification', url: `${VERIFICATION_URL}/healthz` },
    { name: 'validation', url: `${VALIDATION_URL}/healthz` },
    { name: 'authorization', url: `${AUTHORIZATION_URL}/healthz` }
  ];

  const results = await Promise.all(targets.map(async (t) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    try {
      const r = await fetch(t.url, { signal: controller.signal });
      const body = await r.json();
      return { service: t.name, up: r.ok, pod: body.pod };
    } catch (err) {
      return { service: t.name, up: false, pod: null, error: err.message };
    } finally {
      clearTimeout(timer);
    }
  }));

  res.json({
    gatewayPod: POD,
    checkedAt: new Date().toISOString(),
    services: [{ service: 'gateway', up: true, pod: POD }, ...results]
  });
});

// ===========================================================================
//  THE DASHBOARD (static files)
// ===========================================================================
app.use(express.static(path.join(__dirname, 'public'), { maxAge: '5m' }));

app.use((req, res) => res.status(404).json({ error: 'not found', path: req.originalUrl }));

app.listen(PORT, () => {
  log('info', `Gateway listening on port ${PORT}`);
  log('info', 'upstreams configured', { INGEST_URL, PRICE_URL, OPTIMIZER_URL });
});

process.on('SIGTERM', () => {
  log('info', 'SIGTERM received, shutting down gracefully');
  process.exit(0);
});
