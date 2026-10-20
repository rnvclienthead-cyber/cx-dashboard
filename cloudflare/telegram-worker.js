/**
 * Cloudflare Worker для бота Видовито.
 *
 * 1) POST на корень / — алерты и webhook Telegram (как раньше).
 * 2) Остальные пути — прокси на https://cxvo.ru
 *    (окно чата в боте открывается отсюда, чтобы работать с VPN).
 * 3) scheduled (cron) — внешний монитор: пингует сайт со стороны Cloudflare
 *    и пишет в Telegram, если cxvo.ru не отвечает. Это «последняя линия»:
 *    работает, даже когда сам сервер мёртв и его собственные алерты молчат.
 *
 * В Cloudflare: Workers → lingering-recipe-b1b3 → Edit code → вставить это.
 * Переменные: TG_BOT_TOKEN, TG_WEBHOOK_SECRET, MONITOR_CHAT_ID.
 * Cron Trigger: каждые 2 минуты. Состояние — Cache API (или KV CX_MONITOR):
 * алерт один раз при падении, раз в час-напоминание и «снова в строю».
 */
const SITE = "https://cxvo.ru";
const TG_WEBHOOK = SITE + "/api/v1/tshopping/telegram-webhook";

// Монитор
const HEALTH_URL = SITE + "/api/v1/health/live";
const CHECK_TIMEOUT_MS = 10000;
const FAILS_TO_ALERT = 2;        // подряд провалов до тревоги (~4 мин при cron */2)
const REMIND_EVERY_MS = 3600000; // напоминать раз в час, пока лежит
const STATE_CACHE_URL = SITE + "/__cx_monitor_state";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/" && request.method === "POST") {
      return handleTelegram(request, env);
    }
    if (url.pathname === "/" && request.method === "GET") {
      return new Response("ok");
    }
    // Служебная самопроверка монитора: открыть в браузере, чтобы убедиться,
    // что Worker жив и достаёт до cxvo.ru. С ?alert=<TG_WEBHOOK_SECRET>
    // дополнительно шлёт тестовое сообщение в Telegram (проверка бота и chat_id).
    if (url.pathname === "/monitor-selftest" && request.method === "GET") {
      const res = await pingHealthReliable(env);
      const token = url.searchParams.get("alert");
      if (token && token === (env.TG_WEBHOOK_SECRET || "")) {
        await sendMonitorTg(env, "✅ Тест внешнего монитора: сообщение доходит.");
      }
      return new Response(JSON.stringify({ monitor: "alive", health: res }), {
        headers: { "content-type": "application/json" },
      });
    }
    return proxySite(request);
  },

  async scheduled(event, env, ctx) {
    ctx.waitUntil(runMonitor(env));
  },
};

function friendlyDetail(detail) {
  const s = String(detail || "");
  if (/abort/i.test(s)) {
    return "проверка не дождалась ответа за 10 сек (сайт мог тормозить)";
  }
  return s;
}

function retryWaitMs(env) {
  const raw = env && env.MONITOR_RETRY_MS;
  if (raw === 0 || raw === "0") return 0;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? n : 1500;
}

function sleep(ms) {
  if (!ms) return Promise.resolve();
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function pingHealth() {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), CHECK_TIMEOUT_MS);
  try {
    const r = await fetch(HEALTH_URL, {
      signal: ctrl.signal,
      cf: { cacheTtl: 0 },
      headers: { "cache-control": "no-cache" },
    });
    return { ok: r.ok, detail: r.ok ? "" : `HTTP ${r.status}` };
  } catch (e) {
    return { ok: false, detail: String((e && e.message) || e) };
  } finally {
    clearTimeout(timer);
  }
}

async function pingHealthReliable(env) {
  const first = await pingHealth();
  if (first.ok) return first;
  await sleep(retryWaitMs(env));
  const second = await pingHealth();
  if (second.ok) return second;
  return { ok: false, detail: friendlyDetail(second.detail || first.detail) };
}

async function loadMonitorState(env) {
  if (env.CX_MONITOR) {
    try { return JSON.parse((await env.CX_MONITOR.get("state")) || "{}"); } catch { return {}; }
  }
  try {
    const hit = await caches.default.match(STATE_CACHE_URL);
    if (!hit) return {};
    return await hit.json();
  } catch {
    return {};
  }
}

async function saveMonitorState(env, st) {
  const body = JSON.stringify(st);
  if (env.CX_MONITOR) {
    await env.CX_MONITOR.put("state", body);
    return;
  }
  try {
    await caches.default.put(
      STATE_CACHE_URL,
      new Response(body, {
        headers: { "content-type": "application/json", "cache-control": "max-age=86400" },
      }),
    );
  } catch {
    /* Cache API недоступен — следующий тик начнёт без памяти */
  }
}

async function sendMonitorTg(env, text) {
  const token = env.TG_BOT_TOKEN || env.BOT_TOKEN || "";
  const chat = env.MONITOR_CHAT_ID || env.TG_ALERT_CHAT_ID || "";
  if (!token || !chat) return;
  try {
    await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ chat_id: chat, text, disable_web_page_preview: true }),
    });
  } catch {
    /* сеть до Telegram недоступна — в следующий тик попробуем снова */
  }
}

async function runMonitor(env) {
  const res = await pingHealthReliable(env);
  const st = await loadMonitorState(env);
  const now = Date.now();

  if (res.ok) {
    if (st.alerted) {
      await sendMonitorTg(env, "🟢 Внешний монитор: cxvo.ru снова отвечает.");
    }
    await saveMonitorState(env, { fails: 0, alerted: false, lastAlert: 0 });
    return;
  }

  const fails = (st.fails || 0) + 1;
  let alerted = !!st.alerted;
  let lastAlert = st.lastAlert || 0;

  if (!alerted && fails >= FAILS_TO_ALERT) {
    await sendMonitorTg(env, `🔴 Внешний монитор: cxvo.ru не отвечает (${fails} проверки подряд).\n${res.detail}`);
    alerted = true;
    lastAlert = now;
  } else if (alerted && now - lastAlert >= REMIND_EVERY_MS) {
    await sendMonitorTg(env, `🔴 Внешний монитор: cxvo.ru всё ещё лежит.\n${res.detail}`);
    lastAlert = now;
  }
  await saveMonitorState(env, { fails, alerted, lastAlert });
}

async function handleTelegram(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return new Response("Bad JSON", { status: 400 });
  }

  const isUpdate = body && (
    body.update_id != null ||
    body.message ||
    body.edited_message ||
    body.callback_query
  );
  if (isUpdate) {
    try {
      const r = await fetch(TG_WEBHOOK, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "X-Telegram-Bot-Api-Secret-Token": env.TG_WEBHOOK_SECRET || "",
        },
        body: JSON.stringify(body),
      });
      const text = await r.text();
      return new Response(text || '{"ok":true}', {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    } catch {
      return new Response('{"ok":true,"status":"origin_error"}', {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
  }

  if (body && typeof body.method === "string" && body.method && body.method !== "sendMessage") {
    const token = env.TG_BOT_TOKEN || env.BOT_TOKEN || "";
    const { method, ...params } = body;
    return fetch(`https://api.telegram.org/bot${token}/${method}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(params),
    });
  }

  if (body == null || body.chat_id == null || body.text == null || body.text === "") {
    return new Response("Missing chat_id or text", { status: 400 });
  }
  const token = env.TG_BOT_TOKEN || env.BOT_TOKEN || "";
  const payload = { chat_id: body.chat_id, text: body.text };
  if (body.reply_markup) payload.reply_markup = body.reply_markup;
  if (body.parse_mode) payload.parse_mode = body.parse_mode;
  if (body.disable_web_page_preview) payload.disable_web_page_preview = true;
  return fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function proxySite(request) {
  const incoming = new URL(request.url);
  const target = new URL(incoming.pathname + incoming.search, SITE);
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.set("X-Forwarded-Host", incoming.host);
  headers.set("X-Forwarded-Proto", "https");

  const init = {
    method: request.method,
    headers,
    redirect: "manual",
  };
  const upgrade = (request.headers.get("Upgrade") || "").toLowerCase();
  if (upgrade === "websocket") {
    return fetch(target.toString(), request);
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = request.body;
  }
  const resp = await fetch(target.toString(), init);
  const out = new Headers(resp.headers);
  const ctype = (out.get("content-type") || "").toLowerCase();
  if (ctype.includes("text/html")) {
    out.set("Cache-Control", "no-store, no-cache, must-revalidate");
  }
  return new Response(resp.body, {
    status: resp.status,
    statusText: resp.statusText,
    headers: out,
  });
}
