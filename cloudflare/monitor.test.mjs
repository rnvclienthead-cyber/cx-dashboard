// Тест логики внешнего монитора Worker. Запуск: node --test cloudflare/monitor.test.mjs
// Cloudflare-рантайм не нужен: подменяем global.fetch и KV.
import test from 'node:test';
import assert from 'node:assert/strict';
import worker from './telegram-worker.js';

function makeCache() {
  const m = new Map();
  return {
    match: async (key) => {
      const v = m.get(String(key));
      if (v == null) return undefined;
      return new Response(v, { headers: { 'content-type': 'application/json' } });
    },
    put: async (key, res) => {
      m.set(String(key), await res.clone().text());
    },
  };
}

function makeEnv({ kv = null, health = true, fetchImpl = null } = {}) {
  const sent = [];
  globalThis.caches = { default: makeCache() };
  globalThis.fetch = fetchImpl || (async (url) => {
    const u = String(url);
    if (u.includes('/api/v1/health/live')) {
      return { ok: health, status: health ? 200 : 503 };
    }
    if (u.includes('api.telegram.org')) {
      sent.push(u);
      return { ok: true };
    }
    return { ok: true };
  });
  return {
    env: { TG_BOT_TOKEN: 't', MONITOR_CHAT_ID: '1', CX_MONITOR: kv, MONITOR_RETRY_MS: 0 },
    sent,
  };
}

function makeKV() {
  const m = new Map();
  return {
    get: async (k) => (m.has(k) ? m.get(k) : null),
    put: async (k, v) => void m.set(k, v),
    _dump: () => JSON.parse(m.get('state') || '{}'),
  };
}

async function tick(env) {
  await new Promise((r) => worker.scheduled({}, env, { waitUntil: (p) => p.then(r) }));
}

test('healthy site: no alert', async () => {
  const { env, sent } = makeEnv({ kv: makeKV(), health: true });
  await tick(env);
  assert.equal(sent.length, 0);
});

test('down alerts once after threshold, not on every tick', async () => {
  const kv = makeKV();
  const { env, sent } = makeEnv({ kv, health: false });
  await tick(env); // fail 1 — молчим (порог 2)
  assert.equal(sent.length, 0);
  await tick(env); // fail 2 — первая тревога
  assert.equal(sent.length, 1);
  await tick(env); // fail 3 — без спама (час не прошёл)
  assert.equal(sent.length, 1);
});

test('recovery sends back-up message', async () => {
  const kv = makeKV();
  let ctx = makeEnv({ kv, health: false });
  await tick(ctx.env);
  await tick(ctx.env); // тревога
  assert.equal(ctx.sent.length, 1);
  // Сайт ожил
  ctx = makeEnv({ kv, health: true });
  await tick(ctx.env);
  assert.equal(ctx.sent.length, 1); // «снова в строю»
  assert.equal(kv._dump().alerted, false);
});

test('hourly reminder while still down', async () => {
  const kv = makeKV();
  const { env, sent } = makeEnv({ kv, health: false });
  await tick(env);
  await tick(env); // первая тревога
  assert.equal(sent.length, 1);
  // Отматываем lastAlert на час назад — должен прийти повтор
  const st = kv._dump();
  st.lastAlert = Date.now() - 3600001;
  await kv.put('state', JSON.stringify(st));
  await tick(env);
  assert.equal(sent.length, 2);
});

test('without KV: still alerts once after threshold, not on every tick', async () => {
  const { env, sent } = makeEnv({ kv: null, health: false });
  await tick(env);
  assert.equal(sent.length, 0);
  await tick(env);
  assert.equal(sent.length, 1);
  await tick(env);
  assert.equal(sent.length, 1);
});

test('one aborted probe then success: no alert', async () => {
  let n = 0;
  const { env, sent } = makeEnv({ kv: null, health: true });
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.includes('/api/v1/health/live')) {
      n += 1;
      if (n === 1) throw new Error('The operation was aborted');
      return { ok: true, status: 200 };
    }
    if (u.includes('api.telegram.org')) {
      sent.push(u);
      return { ok: true };
    }
    return { ok: true };
  };
  await tick(env);
  assert.equal(sent.length, 0);
  assert.equal(n, 2);
});
